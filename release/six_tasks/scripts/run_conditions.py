# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
E2_DIR = HERE.parent
REPO_ROOT = E2_DIR


def _load_env():
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(HERE))

from healthclaw.agent import HealthClawAgent
from healthclaw.memory import FiveLayerMemory
from healthclaw.llm_client import make_doubao_client
from adapters._common import DATA_DIR, TASKS, load_adapter

UTILITY_INJECT_MIN_BY_TASK = {"diabetes_readmission": 0.3}


def _apply_utility_inject_min(memory, task: str):
    try:
        env_min = float(os.environ.get("E2_UTILITY_MIN", ""))
    except (TypeError, ValueError):
        env_min = 0.0
    if env_min > 0:
        return
    memory.UTILITY_INJECT_MIN = UTILITY_INJECT_MIN_BY_TASK.get(task, 0.0)


REACT_CONSTITUTION = """你是一个严谨的任务执行助手。你自身具备完成任务所需的知识与感知能力。

【工具使用原则】
1. 工具输出仅供参考，不是标准答案：调用工具后，必须结合题面与你自己的判断独立复核。
2. 不盲目信任任何单一工具；工具输出与你的判断矛盾时，优先相信经你复核后更有依据的一方，
   并在答案中体现你的独立结论。
3. 你完全有能力依靠自身能力作答：工具只是辅助参考，不是必经步骤，答得好比调了工具更重要。

【表达规范】
1. 直接回答问题本身；不暴露内部过程（不提工具名）。
2. 答案需给出明确、可判定的结果（数值/选项/结论），不含糊。
"""

REACT_CAPABILITY_INDEX = """【能力索引】
- 任务专属工具：见工具清单；每个工具输出含可靠性标记，低置信度输出需自行复核。
"""


def _render(adapter, payload, with_tools: bool, split: str = "test") -> str:
    try:
        return adapter.render_case(payload, split=split, with_tools=with_tools)
    except TypeError:
        return adapter.render_case(payload, split=split)


def run_base(adapter, cases, client, out_path, done_ids, split: str = "test"):
    t0 = time.time()
    for i, case in enumerate(cases):
        if case["case_id"] in done_ids:
            continue
        q = _render(adapter, case["payload"], with_tools=False, split=split)
        answer = client.ask_block(q, max_tokens=2000, temperature=0.0)
        sc = adapter.score(answer, case.get("gold"))
        rec = {"case_id": case["case_id"], "reward": float(sc.get("reward", 0.0)),
               "error_hint": sc.get("error_hint"), "answer": answer[:2000]}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if (i + 1) % 20 == 0:
            print(f"  [base] {i+1}/{len(cases)} sec/case={(time.time()-t0)/(i+1):.0f}", flush=True)


def run_agent_condition(adapter, cases, client, out_path, done_ids, condition: str,
                        task: str, frozen_path: Path, split: str = "test"):
    runs_dir = Path(out_path).parent
    mem_dir = runs_dir / ("memory_test_" + Path(out_path).stem)
    memory = FiveLayerMemory(client, mem_dir, period_label="case", e2_mode=True)
    include_builtin = True
    if condition == "react":
        include_builtin = False
        memory.frozen = True
        memory.constitution = REACT_CONSTITUTION
        memory.capability_index = REACT_CAPABILITY_INDEX
    elif condition == "hcfrozen":
        memory.load_frozen(frozen_path)
    _apply_utility_inject_min(memory, task)
    toolbox = adapter.get_toolbox(memory, client, include_builtin=include_builtin)
    agent = HealthClawAgent(memory, toolbox, client, e2_mode=True)
    t0 = time.time()
    n_done = 0
    for i, case in enumerate(cases):
        if case["case_id"] in done_ids:
            continue
        agent.start_day(i, hook_day=False)
        q = _render(adapter, case["payload"], with_tools=True, split=split)
        answer = agent.respond(q)
        sc = adapter.score(answer, case.get("gold"))
        rec = {"case_id": case["case_id"], "reward": float(sc.get("reward", 0.0)),
               "error_hint": sc.get("error_hint"), "answer": answer[:2000],
               "tool_calls": len(toolbox.log)}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        n_done += 1
        if n_done % 20 == 0:
            print(f"  [{condition}] {n_done}/{len(cases)-len(done_ids)} "
                  f"sec/case={(time.time()-t0)/n_done:.0f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--n", type=int, default=0, help="测试 case 数上限；0=全部")
    ap.add_argument("--smoke", action="store_true", help="smoke：n=5")
    ap.add_argument("--split", default="test",
                    help="题目集（默认 test 不变；参照臂传 accumulate 加载积累集）")
    ap.add_argument("--conditions", default="base,react,hcfrozen")
    ap.add_argument("--app-id", default=None, help="泳道独立 AppID（不传则用 .env 默认）")
    ap.add_argument("--channel", default="doubao_e2", help="限速通道名（每泳道独立）")
    ap.add_argument("--model", default=None,
                    help="覆盖底座模型（跨基座实验，如 deepseek-v4.1-flash-baidu）；"
                         "不传则用 Doubao-Seed-2.0-pro，行为与主表完全一致")
    ap.add_argument("--out-root", default=None,
                    help="产物根目录（默认 E2/runs）；对照实验可指向独立 runs 目录")
    ap.add_argument("--tag", default="",
                    help="产物文件名后缀（如 _ds → test_base_ds.jsonl），避免覆盖现有产物")
    ap.add_argument("--frozen-path", default=None,
                    help="覆盖冻结库路径（默认 <DATA_DIR>/frozen_repos/<task>.json）；"
                         "跨任务迁移实验传库 A 的 frozen json，任务 adapter 仍由 --task 指定")
    ap.add_argument("--base-url", default=None,
                    help="自部署 OpenAI 兼容端点（如 sglang http://host:8000/v1）；"
                         "不传则使用 HEALTHJEV_BASE_URL；embedding 可单独配置端点")
    ap.add_argument("--rpm", type=int, default=500,
                    help="限速通道 RPM（本地自部署端点可调高，如 3000）")
    ap.add_argument("--shard", default=None,
                    help="分片 i/n（如 1/3）：把 manifest 按序取模切分后只跑本片，多进程并行加速用；不传则行为不变")
    args = ap.parse_args()
    n_limit = 5 if args.smoke else args.n

    task = args.task
    adapter = load_adapter(task)
    cases = adapter.load_cases(args.split)
    if args.shard:
        try:
            si, sn = (int(x) for x in args.shard.split("/"))
            if sn < 1 or not 0 <= si < sn:
                raise ValueError("expected 0 <= i < n and n >= 1")
        except ValueError as exc:
            ap.error(f"invalid --shard: {exc}")
        cases = [c for j, c in enumerate(cases) if j % sn == si]
        print(f"[shard] {si}/{sn}: 本片 {len(cases)} 题", flush=True)
    if n_limit:
        cases = cases[:n_limit]
    runs_dir = (Path(args.out_root) if args.out_root else E2_DIR / "runs") / task
    runs_dir.mkdir(parents=True, exist_ok=True)
    frozen_path = (Path(args.frozen_path) if args.frozen_path
                   else DATA_DIR / "frozen_repos" / f"{task}.json")

    client = make_doubao_client(rpm=args.rpm, channel=args.channel,
                                app_id=args.app_id or None, model=args.model or None,
                                base_url=args.base_url or None)

    for cond in [c.strip() for c in args.conditions.split(",") if c.strip()]:
        out_path = runs_dir / f"test_{cond}{args.tag}.jsonl"
        done_ids = set()
        if out_path.exists():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                try:
                    done_ids.add(json.loads(line)["case_id"])
                except Exception:
                    pass
        todo = [c for c in cases if c["case_id"] not in done_ids]
        print(f"[test:{task}:{cond}] todo={len(todo)}/{len(cases)}", flush=True)
        if cond == "base":
            run_base(adapter, cases, client, out_path, done_ids, split=args.split)
        elif cond in ("react", "hcfrozen"):
            run_agent_condition(adapter, cases, client, out_path, done_ids,
                                cond, task, frozen_path, split=args.split)
        else:
            raise SystemExit(f"unknown condition {cond}")
        rewards = []
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                rewards.append(json.loads(line)["reward"])
            except Exception:
                pass
        mean = sum(rewards) / len(rewards) if rewards else 0.0
        print(f"[test:{task}:{cond}] n={len(rewards)} mean_reward={mean:.4f}", flush=True)


if __name__ == "__main__":
    main()
