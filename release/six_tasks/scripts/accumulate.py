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
from healthclaw.memory import FiveLayerMemory, cosine
from healthclaw.tools import ToolBox
from healthclaw.llm_client import make_doubao_client, make_e2_judge_client
from healthclaw import distill
from healthclaw.jev import JeVFatalError
from adapters._common import DATA_DIR, TASKS, load_adapter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--n", type=int, default=0, help="积累 case 数上限；0=全部")
    ap.add_argument("--smoke", action="store_true", help="smoke 模式：n=10")
    ap.add_argument("--app-id", default=None, help="泳道独立 AppID（不传则用 .env 默认）")
    ap.add_argument("--channel", default="doubao_e2", help="限速通道名（每泳道独立）")
    ap.add_argument("--rpm", type=int, default=500,
                    help="LLM 通道 RPM（多进程共享同一 AppID 时建议调低，如 240）")
    args = ap.parse_args()
    n_limit = 10 if args.smoke else args.n

    task = args.task
    adapter = load_adapter(task)
    runs_dir = E2_DIR / "runs" / task
    logs_dir = E2_DIR / "logs" / task
    runs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    client = make_doubao_client(rpm=args.rpm, channel=args.channel,
                                app_id=args.app_id or None)
    judge_mode = os.environ.get("E2_JUDGE_MODE", "jev").strip().lower()
    if judge_mode == "jev":
        from healthclaw.jev import make_jev_judge_client
        judge_client = make_jev_judge_client(task, ledger_dir=runs_dir / "jev_ledger")
        print(f"[judge:{task}] mode=jev（本地 Jev 自主裁判，零外部 API）", flush=True)
    elif judge_mode == "luna":
        judge_client = make_e2_judge_client(rpm=500)
        print(f"[judge:{task}] mode=luna（外部打标/对照口径，显式 opt-in）", flush=True)
    else:
        raise SystemExit(f"unknown E2_JUDGE_MODE {judge_mode!r} "
                         "(expected 'jev' or 'luna')")

    memory = FiveLayerMemory(client, runs_dir / "memory", period_label="case", e2_mode=True,
                             inject_mode="retrieve")
    toolbox = adapter.get_toolbox(memory, client, include_builtin=True)
    agent = HealthClawAgent(memory, toolbox, client, e2_mode=True)

    state_path = runs_dir / "accumulate_state.json"
    state = {"done": []}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    done_ids = set(state["done"])
    out_path = runs_dir / "accumulate_cases.jsonl"

    cases = adapter.load_cases("accumulate")
    todo = [c for c in cases if c["case_id"] not in done_ids]
    if n_limit:
        todo = todo[:max(0, n_limit - len(done_ids))]

    print(f"[accumulate:{task}] todo={len(todo)} done={len(done_ids)}", flush=True)
    t0 = time.time()
    n_skip = 0
    for i, case in enumerate(todo):
        try:
            idx = len(done_ids) + i
            agent.start_day(idx, hook_day=False)
            user_text = adapter.render_case(case["payload"], split="accumulate")
            answer = agent.respond(user_text)
            sc = adapter.score(answer, case.get("gold"))
            reward = float(sc.get("reward", 0.0))
            error_hint = sc.get("error_hint")
            settle = agent.settle_case(idx, question_brief=user_text[:9500],
                                       answer_brief=answer[:2000],
                                       reward=reward, error_hint=error_hint,
                                       used_tools=any(t["tool"] in toolbox.extra_handlers
                                                      for t in toolbox.log),
                                       judge_client=judge_client,
                                       gold=str(case.get("gold"))[:1000],
                                       tool_calls=len(toolbox.log))
        except JeVFatalError:
            raise
        except Exception as exc:
            n_skip += 1
            print(f"[accumulate:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "idx": idx, "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "settle": {"settled": settle.get("settled"),
                          "worth": settle.get("worth"),
                          "judge": settle.get("judge"),
                          "judge_rejected": settle.get("judge_rejected", False),
                          "actions": settle.get("actions", [])},
               "tool_calls": len(toolbox.log), "ts": time.strftime("%H:%M:%S")}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state["done"].append(case["case_id"])
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        if (i + 1) % 5 == 0 or i + 1 == len(todo):
            rate = (time.time() - t0) / max(1, i + 1)
            print(f"[accumulate:{task}] {i+1}/{len(todo)} last_reward={reward:.2f} "
                  f"sec/case={rate:.0f} l3={len(memory.l3)}", flush=True)
        if distill.should_consolidate(idx):
            res = distill.consolidate(memory, idx, client, judge_client=judge_client,
                                      log_dir=logs_dir)
            n_unv = sum(1 for a in res.get("approved", []) if a.get("unverified"))
            print(f"[consolidate:{task}] C{idx} approved={len(res.get('approved', []))} "
                  f"(unverified={n_unv}) rejected={len(res.get('rejected', []))}", flush=True)

    try:
        _evaluate_utility(memory, adapter, sample=150)
    except Exception as exc:
        print(f"[utility:{task}] 评估失败（条目保持默认 utility）: "
              f"{type(exc).__name__}: {str(exc)[:120]}", flush=True)

    frozen_path = DATA_DIR / "frozen_repos" / f"{task}.json"
    meta = {"task": task, "n_accumulate": len(state["done"]),
            "pipeline": "healthjev_final",
            "accumulate_manifest": str(DATA_DIR / task / "manifest_accumulate.jsonl")}
    res = memory.export_frozen(frozen_path, meta=meta)
    print(f"[frozen:{task}] path={res['path']} entries={res['n_entries']} "
          f"skipped_cases={n_skip}", flush=True)


def _evaluate_utility(memory, adapter, sample: int = 150):
    cases = adapter.load_cases("accumulate")
    questions = []
    for c in cases:
        try:
            q = adapter.render_case(c["payload"], split="accumulate")
        except TypeError:
            q = adapter.render_case(c["payload"], split="test")
        questions.append(q)
    ep_rewards = {}
    for day in range(len(cases)):
        ep = memory.load_episode(day)
        if ep is not None and isinstance(ep.get("reward"), (int, float)):
            ep_rewards[day] = float(ep["reward"])
    valid = [(i, r) for i, r in ep_rewards.items() if i < len(questions)]
    if len(valid) < 30:
        print("[utility] 积累 reward 样本不足，跳过评估", flush=True)
        return
    overall = sum(r for _, r in valid) / len(valid)
    idxs = list(range(len(questions)))
    if sample and sample < len(idxs):
        step = len(idxs) / sample
        idxs = [int(i * step) for i in range(sample)]
    q_embs = []
    for i in idxs:
        try:
            q_embs.append((i, memory.index._embed(questions[i][:512])))
        except Exception:
            pass
    print(f"[utility] 全题均分={overall:.3f} 比对题={len(q_embs)}", flush=True)
    n_adj = 0
    for e in memory.l3:
        if e.get("kind") != "distilled":
            continue
        if float(e.get("utility", 0.5)) < 0:
            continue
        try:
            e_emb = memory.index._embed(
                f"管家经验（适用：{e.get('scope')}）：{e.get('points')}")
        except Exception:
            continue
        sims = sorted(((cosine(e_emb, qe), i) for i, qe in q_embs),
                      key=lambda x: -x[0])
        top = [(s, i) for s, i in sims[:8] if s >= 0.25 and i in ep_rewards]
        if len(top) < 3:
            continue
        m = sum(ep_rewards[i] for _, i in top) / len(top)
        e["utility"] = max(0.05, min(0.9, 0.5 + (m - overall) * 1.5))
        n_adj += 1
    memory._save_l3()
    print(f"[utility] 调整条目 {n_adj} 条（降权不封杀，下限 0.05）", flush=True)


if __name__ == "__main__":
    main()
