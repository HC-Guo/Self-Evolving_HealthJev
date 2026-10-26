# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
E2_DIR = HERE.parents[1]
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
sys.path.insert(0, str(E2_DIR / "scripts"))
sys.path.insert(0, str(HERE))

from healthclaw.agent import HealthClawAgent
from healthclaw.memory import E2_CONSTITUTION, E2_CAPABILITY_INDEX
from healthclaw.llm_client import make_doubao_client
from adapters._common import TASKS, load_adapter
from mem0_memory import Mem0Memory
import mem0_memory as _mem0_mod

_CROSS_CASE_EXTRACT_RULE = """

【本次任务特别约束】本任务是批量独立案例（case）评测：每个 case（第 N 个 case）是一位独立的新患者/新用户；case 序号只是时间戳记录，不代表同一位用户的连续对话。不同 case 之间不存在同一用户的信息变更。请仅根据当前这条用户消息独立抽取事实，不要参照、联想或合并其他 case 的信息。"""

_CROSS_CASE_UPDATE_RULE = """

【本次任务特别约束（最高优先级）】本任务是批量独立案例（case）评测：每个 case 是一位独立的新患者/新用户；本任务中不同 case 之间不存在同一用户的信息变更。既有记忆中与新事实相似的条目，极可能来自其他 case（其他患者）的巧合相似，而非同一用户的信息变化：
- 禁止跨 case 的 UPDATE：只有当既有条目与新事实明显来自同一 case（同一位患者的同一条信息）且确实发生信息修正/补充时，才允许 UPDATE；
- 禁止跨 case 的 DELETE；
- 无法确定是否同一 case 时，一律选 ADD（作为新条目）或 NONE，绝不合并；
- case 序号/天数只是时间戳记录，不得据其推断同一用户的时间演进。
示例：既有记忆有"患者每日服用二甲双胍 500mg"（来自 case 3），新 case 5 的患者也服用二甲双胍但剂量 1000mg——这是两位不同患者，必须 ADD 为新条目，禁止 UPDATE 合并。"""

_mem0_mod.FACT_EXTRACTION_PROMPT += _CROSS_CASE_EXTRACT_RULE
_mem0_mod.UPDATE_MEMORY_PROMPT += _CROSS_CASE_UPDATE_RULE
_mem0_mod.DAY_CONTEXT_TMPL = (
    "\n当前条目时间戳：这是第{day}个独立 case（序号仅作时间戳记录，"
    "不是同一位患者的第几天；不同 case 是不同患者）")

_GENETURING_EXTRACT_PROMPT = """你是一名医学与生物学事实整理器（Medical & Biological Fact Organizer），专门从对话中准确抽取可复用的解题事实与科学知识，并把它们整理成相互独立、便于管理的事实条目，供未来同类问题检索与作答使用。

需要抽取的信息类型：
1. 任务指令与解题方法：本题要求做什么任务、正确的做法/流程（如：该题要求将 DNA 序列翻译为蛋白序列，正确做法是按密码子表逐 codon 翻译）；
2. 基因与蛋白实体：基因符号、官方名称、别名、染色体位置、相关疾病关联（如：BRCA1 位于 17 号染色体，与乳腺癌相关）；
3. 序列事实：DNA/RNA/蛋白序列的关键特征、翻译/转录结果、功能域（如：序列 MVLSPADK 的前几个残基对应血红蛋白 alpha 链 N 端）；
4. 物种与数据库事实：涉及的物种、常用数据库/参考工具的结论（如：HGNC/NCBI/MyGene 查询得到的官方结果）；
5. 科学事实：对话中出现的其他可复用的医学/生物学结论与判定标准。

few-shot 示例：
用户: [GeneTuring Benchmark - amino acid translation] Translate the DNA sequence ATGCGT to a protein sequence.
输出: {"facts": ["该题要求将 DNA 序列翻译为蛋白序列，正确做法是按密码子表逐 codon 翻译", "DNA 序列 ATGCGT 翻译为蛋白序列为 MR（ATG=M, CGT=R）"]}
用户: [GeneTuring Benchmark - gene disease association] Which gene is associated with Huntington's disease?
输出: {"facts": ["亨廷顿病（Huntington's disease）相关基因是 HTT"]}

硬性规则：
- 只根据【用户消息】抽取，不得从助手消息或系统消息中取任何内容；
- 检测用户输入语言，事实用同样语言记录（题面为英文时可保留英文实体名）；
- 保留具体数值、序列片段与实体名称（它们是事实的关键部分）；
- 只抽取可复用的通用事实，不要记录仅对单次作答有意义的临时表述；
- 对话里确实没有可抽取内容时才返回空列表；
- 只输出 STRICT JSON：{"facts": ["事实1", "事实2", ...]}。

以下是一次用户与助手的对话，请抽取其中可复用的医学/生物学事实与解题知识："""


def _apply_task_prompt_override(task: str):
    if task == "geneturing":
        _mem0_mod.FACT_EXTRACTION_PROMPT = (_GENETURING_EXTRACT_PROMPT
                                            + _CROSS_CASE_EXTRACT_RULE)
        print(f"[mem0:{task}] 抽取 prompt 已覆写为医学与生物学事实整理器（扩展版）",
              flush=True)


def _read_done_ids(out_path: Path) -> set:
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["case_id"])
            except Exception:
                pass
    return done


class EmbedDiskCache:
    def __init__(self, client, path: Path):
        self._client = client
        self.path = path
        self.cache: Dict[str, List[float]] = {}
        if path.exists():
            try:
                self.cache = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}
        self._new = 0

    def embed(self, text: str) -> List[float]:
        key = str(text)[:500]
        if key in self.cache:
            return self.cache[key]
        vec = self._client.embed(text)
        self.cache[key] = vec
        self._new += 1
        if self._new % 20 == 0:
            self.save()
        return vec

    def ask_chat(self, messages, max_tokens: int = 800, temperature: float = 0.7,
                 json_mode: bool = False) -> str:
        out = ""
        for attempt in range(3):
            out = self._client.ask_chat(messages, max_tokens=max_tokens,
                                        temperature=temperature, json_mode=json_mode)
            if out and out.strip():
                return out
            time.sleep(2 + attempt * 3)
        return out

    def save(self):
        try:
            self.path.write_text(json.dumps(self.cache), encoding="utf-8")
            self._new = 0
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._client, name)


class _ShimIndex:
    def __init__(self, mem0: Mem0Memory):
        self._mem0 = mem0

    def query(self, query: str, current_day: int = 0, top_k: int = 5) -> List[Dict[str, Any]]:
        return self._mem0.search(query, k=top_k)

    def add_chunk(self, *args, **kwargs):
        return None

    def stats(self):
        return {"n_memories": len(self._mem0.memories)}


class Mem0ShimMemory:
    def __init__(self, mem0: Mem0Memory, task: str):
        self.mem0 = mem0
        self.task = task
        self.frozen = False
        self.l2: Dict[str, Any] = {"recent_metrics": {}, "medications": [],
                                   "allergies": [], "history": [], "preferences": []}
        self.index = _ShimIndex(mem0)
        self._current_query: Optional[str] = None
        self._last_hits: List[Dict[str, Any]] = []

    def _hits(self, query: Optional[str]) -> List[Dict[str, Any]]:
        if not query:
            return []
        if query == self._current_query and self._last_hits is not None:
            return self._last_hits
        try:
            hits = self.mem0.search(query, k=3)
        except Exception as exc:
            print(f"    [mem0-search] error: {type(exc).__name__}: {str(exc)[:120]}",
                  flush=True)
            hits = []
        self._current_query = query
        self._last_hits = hits
        return hits

    def system_prompt(self, day: int, question: Optional[str] = None) -> str:
        query = question or self._current_query
        hits = self._hits(query)
        if hits:
            lines = ["【相关记忆】（Mem0 向量检索 top-3 命中，供参考）"]
            for h in hits:
                lines.append(f"- [{h['id']}|{h['score']}] {h['text']}")
        else:
            lines = ["【相关记忆】（本题无命中记忆）"]
        return (f"{E2_CONSTITUTION}\n\n{E2_CAPABILITY_INDEX}\n\n"
                f"当前是第 {day} 个 case（任务：{self.task}）。\n\n" + "\n".join(lines))

    def start_day(self, day: int, hook_day: bool = False, hook_entities=None):
        return None

    def freeze(self):
        self.frozen = True

    def write_profile(self, payload: Dict[str, Any], day: int) -> Dict[str, Any]:
        return {"ok": False, "note": "mem0 baseline: profile write disabled"}

    def add_sop_entries(self, *args, **kwargs):
        return {"actions": []}

    def write_episode(self, *args, **kwargs):
        return None

    def stats(self):
        return {"mode": "mem0", "n_memories": len(self.mem0.memories), "frozen": self.frozen}


def _render_accum(adapter, payload) -> str:
    try:
        return adapter.render_case(payload, split="accumulate", with_tools=True)
    except TypeError:
        return adapter.render_case(payload, split="accumulate")


def _render_test(adapter, payload) -> str:
    try:
        return adapter.render_case(payload, split="test", with_tools=True)
    except TypeError:
        return adapter.render_case(payload, split="test")


def run_accumulate(args, task: str, adapter, client, base_dir: Path):
    task_dir = base_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    mem0 = Mem0Memory(client, task_dir / "mem0_store", top_k=3)
    shim = Mem0ShimMemory(mem0, task)
    toolbox = adapter.get_toolbox(shim, client, include_builtin=True)
    agent = HealthClawAgent(shim, toolbox, client, e2_mode=True)

    state_path = task_dir / "mem0_state.json"
    state = {"done": []}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    done_ids = set(state["done"])
    out_path = task_dir / "accumulate_cases.jsonl"

    cases = adapter.load_cases("accumulate")
    n_limit = 5 if args.smoke else args.n
    todo = [c for c in cases if c["case_id"] not in done_ids]
    if n_limit:
        todo = todo[:max(0, n_limit - len(done_ids))]

    print(f"[mem0-acc:{task}] todo={len(todo)} done={len(done_ids)} "
          f"store={len(mem0.memories)}", flush=True)
    t0 = time.time()
    for i, case in enumerate(todo):
        try:
            idx = len(done_ids) + i
            user_text = _render_accum(adapter, case["payload"])
            shim._current_query = None
            shim._last_hits = []
            hits = shim._hits(user_text)
            agent.start_day(idx, hook_day=False)
            answer = agent.respond(user_text)
            sc = adapter.score(answer, case.get("gold"))
            reward = float(sc.get("reward", 0.0))
            error_hint = sc.get("error_hint")
            transcript = [{"role": "user", "content": user_text},
                          {"role": "assistant", "content": answer}]
            mrec = mem0.add_day(idx, transcript)
            mem_ops = mrec["n_add"] + mrec["n_update"] + mrec["n_delete"]
        except Exception as exc:
            print(f"[mem0-acc:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "idx": idx, "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "mem_ops": mem_ops,
               "mem0": {"n_facts": mrec["n_facts"], "n_add": mrec["n_add"],
                        "n_update": mrec["n_update"], "n_delete": mrec["n_delete"],
                        "n_memories": mrec["n_memories"]},
               "retrieved": [{"id": h["id"], "score": h["score"]} for h in hits],
               "tool_calls": len(toolbox.log), "ts": time.strftime("%H:%M:%S")}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state["done"].append(case["case_id"])
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        if (i + 1) % 5 == 0 or i + 1 == len(todo):
            rate = (time.time() - t0) / max(1, i + 1)
            print(f"[mem0-acc:{task}] {i+1}/{len(todo)} last_reward={reward:.2f} "
                  f"mem_ops={mem_ops} sec/case={rate:.0f} store={len(mem0.memories)}",
                  flush=True)
    if isinstance(client, EmbedDiskCache):
        client.save()
    mem0.save()
    print(f"[mem0-acc:{task}] DONE done={len(state['done'])} "
          f"store={len(mem0.memories)}", flush=True)


def run_test(args, task: str, adapter, client, base_dir: Path):
    task_dir = base_dir / task
    if not (task_dir / "mem0_store" / "memories.json").exists():
        raise SystemExit(f"[mem0-test:{task}] 未找到积累产物 {task_dir}/mem0_store/，"
                         f"请先跑 accumulate 阶段")
    mem0 = Mem0Memory(client, task_dir / "mem0_store", top_k=3)
    shim = Mem0ShimMemory(mem0, task)
    shim.freeze()
    toolbox = adapter.get_toolbox(shim, client, include_builtin=True)
    agent = HealthClawAgent(shim, toolbox, client, e2_mode=True)

    out_path = task_dir / "test_mem0.jsonl"
    done_ids = _read_done_ids(out_path)
    cases = adapter.load_cases("test")
    n_limit = 5 if args.smoke else args.n
    if n_limit:
        cases = cases[:n_limit]
    todo = [c for c in cases if c["case_id"] not in done_ids]
    print(f"[mem0-test:{task}] todo={len(todo)}/{len(cases)} store={len(mem0.memories)}",
          flush=True)
    t0 = time.time()
    n_done = 0
    for i, case in enumerate(cases):
        if case["case_id"] in done_ids:
            continue
        done_ids |= _read_done_ids(out_path)
        if case["case_id"] in done_ids:
            continue
        try:
            user_text = _render_test(adapter, case["payload"])
            shim._current_query = None
            shim._last_hits = []
            hits = shim._hits(user_text)
            agent.start_day(i, hook_day=False)
            answer = agent.respond(user_text)
            sc = adapter.score(answer, case.get("gold"))
            reward = float(sc.get("reward", 0.0))
            error_hint = sc.get("error_hint")
        except Exception as exc:
            print(f"[mem0-test:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "retrieved": [{"id": h["id"], "score": h["score"]} for h in hits],
               "tool_calls": len(toolbox.log), "ts": time.strftime("%H:%M:%S")}
        dup = False
        with open(out_path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                if case["case_id"] in _read_done_ids(out_path):
                    dup = True
                else:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if dup:
            print(f"[mem0-test:{task}] case {case['case_id']} already written by "
                  f"another instance, skipped", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        done_ids.add(case["case_id"])
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        n_done += 1
        if n_done % 20 == 0:
            print(f"[mem0-test:{task}] {n_done}/{len(todo)} "
                  f"sec/case={(time.time()-t0)/n_done:.0f}", flush=True)
    if isinstance(client, EmbedDiskCache):
        client.save()
    rewards = []
    seen: set = set()
    for line in out_path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            cid = obj["case_id"]
            if cid in seen:
                continue
            seen.add(cid)
            rewards.append(obj["reward"])
        except Exception:
            pass
    mean = sum(rewards) / len(rewards) if rewards else 0.0
    print(f"[mem0-test:{task}] n={len(rewards)} mean_reward={mean:.4f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["accumulate", "test"])
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--n", type=int, default=0, help="case 数上限；0=全部")
    ap.add_argument("--smoke", action="store_true", help="smoke：n=5")
    ap.add_argument("--channel", default="doubao_mem0", help="限速通道名（独立于主表/epoch）")
    ap.add_argument("--app-id", default=None, help="独立 AppID（不传则用 .env 默认）")
    args = ap.parse_args()

    task = args.task
    adapter = load_adapter(task)
    _apply_task_prompt_override(task)
    base_dir = HERE
    client = make_doubao_client(rpm=500, channel=args.channel, app_id=args.app_id or None)
    cached = EmbedDiskCache(client, base_dir / task / "mem0_store" / "embed_cache.json")
    print(f"[mem0:{task}:{args.phase}] channel={args.channel} model={client.model}",
          flush=True)
    if args.phase == "accumulate":
        run_accumulate(args, task, adapter, cached, base_dir)
    else:
        run_test(args, task, adapter, cached, base_dir)


if __name__ == "__main__":
    main()
