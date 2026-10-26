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
from memorybank_memory_e2 import MemoryBankMemory


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
    def __init__(self, mb: MemoryBankMemory):
        self._mb = mb

    def query(self, query: str, current_day: int = 0, top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self._mb.retrieve(query, day=max(0, current_day), k=top_k)
        return [{"id": h["id"], "text": h["text"], "score": h["score"]} for h in hits]

    def add_chunk(self, *args, **kwargs):
        return None

    def stats(self):
        return self._mb.stats()


class MemoryBankShimMemory:
    def __init__(self, mb: MemoryBankMemory, task: str):
        self.mb = mb
        self.task = task
        self.frozen = mb.frozen
        self.l2: Dict[str, Any] = {"recent_metrics": {}, "medications": [],
                                   "allergies": [], "history": [], "preferences": []}
        self.index = _ShimIndex(mb)
        self._current_query: Optional[str] = None
        self._current_day: int = 0
        self._last_hits: List[Dict[str, Any]] = []

    def _hits(self, query: Optional[str]) -> List[Dict[str, Any]]:
        if not query:
            return []
        if query == self._current_query and self._last_hits is not None:
            return self._last_hits
        try:
            hits = self.mb.retrieve(query, day=self._current_day, k=5)
        except Exception as exc:
            print(f"    [mb-retrieve] error: {type(exc).__name__}: {str(exc)[:120]}",
                  flush=True)
            hits = []
        self._current_query = query
        self._last_hits = hits
        return hits

    def system_prompt(self, day: int, question: Optional[str] = None) -> str:
        self._current_day = day
        query = question or self._current_query
        hits = self._hits(query)
        recent = [d for d in self.mb.daily_summaries if day - 7 <= d["day"] < day][-7:]
        sum_block = "\n".join(f"- 第{d['day']}天摘要: {d['text']}" for d in recent) \
            or "（暂无历史摘要）"
        portrait = self.mb.personality_global or "（画像形成中）"
        lines = [f"{E2_CONSTITUTION}\n\n{E2_CAPABILITY_INDEX}",
                 f"当前是第 {day} 个 case（任务：{self.task}）。",
                 "", "【用户画像】（MemoryBank 机制：跨 case 共性模式聚合）",
                 portrait, "",
                 "【强度加权记忆召回】（艾宾浩斯保留率加权 top-5，供参考）"]
        if hits:
            for h in hits:
                lines.append(f"- [{h['id']}|score={h['score']}|R={h.get('retention', 0)}] "
                             f"{h['text'][:400]}")
        else:
            lines.append("（本题无命中记忆）")
        lines += ["", "【近期 case 摘要】（官方机制：近 7 日事件摘要常驻）",
                  sum_block]
        return "\n".join(lines)

    def start_day(self, day: int, hook_day: bool = False, hook_entities=None):
        return None

    def freeze(self):
        self.frozen = True

    def write_profile(self, payload: Dict[str, Any], day: int) -> Dict[str, Any]:
        return {"ok": False, "note": "memorybank baseline: profile write disabled"}

    def add_sop_entries(self, *args, **kwargs):
        return {"actions": []}

    def write_episode(self, *args, **kwargs):
        return None

    def stats(self):
        return dict(self.mb.stats(), frozen=self.frozen)


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
    mb = MemoryBankMemory(client, task_dir / "memorybank_store")
    shim = MemoryBankShimMemory(mb, task)
    toolbox = adapter.get_toolbox(shim, client, include_builtin=True)
    agent = HealthClawAgent(shim, toolbox, client, e2_mode=True)

    state_path = task_dir / "memorybank_state.json"
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

    print(f"[mb-acc:{task}] todo={len(todo)} done={len(done_ids)} {mb.stats()}",
          flush=True)
    t0 = time.time()
    for i, case in enumerate(todo):
        try:
            idx = len(done_ids) + i
            user_text = _render_accum(adapter, case["payload"])
            shim._current_query = None
            shim._current_day = idx
            shim._last_hits = []
            hits = shim._hits(user_text)
            agent.start_day(idx, hook_day=False)
            answer = agent.respond(user_text)
            sc = adapter.score(answer, case.get("gold"))
            reward = float(sc.get("reward", 0.0))
            error_hint = sc.get("error_hint")
            mb.add_turn(idx, user_text, answer)
            mb.day_end(idx, [{"role": "user", "content": user_text},
                             {"role": "assistant", "content": answer}])
            st = mb.stats()
        except Exception as exc:
            print(f"[mb-acc:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "idx": idx, "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "mb": {"n_pieces": st["n_pieces_total"], "n_live": st["n_pieces_live"],
                      "n_faded": st["n_faded"], "n_summaries": st["n_daily_summaries"],
                      "has_portrait": st["has_portrait"]},
               "retrieved": [{"id": h["id"], "score": h["score"],
                              "retention": h.get("retention")} for h in hits],
               "tool_calls": len(toolbox.log), "ts": time.strftime("%H:%M:%S")}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state["done"].append(case["case_id"])
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        if (i + 1) % 5 == 0 or i + 1 == len(todo):
            rate = (time.time() - t0) / max(1, i + 1)
            print(f"[mb-acc:{task}] {i+1}/{len(todo)} last_reward={reward:.2f} "
                  f"sec/case={rate:.0f} pieces={st['n_pieces_total']}"
                  f"(live={st['n_pieces_live']},faded={st['n_faded']})",
                  flush=True)
    if isinstance(client, EmbedDiskCache):
        client.save()
    mb.save()
    print(f"[mb-acc:{task}] DONE done={len(state['done'])} {mb.stats()}", flush=True)


def run_test(args, task: str, adapter, client, base_dir: Path):
    task_dir = base_dir / task
    if not (task_dir / "memorybank_store" / "memorybank.json").exists():
        raise SystemExit(f"[mb-test:{task}] 未找到积累产物 {task_dir}/memorybank_store/，"
                         f"请先跑 accumulate 阶段")
    mb = MemoryBankMemory(client, task_dir / "memorybank_store", frozen=True)
    shim = MemoryBankShimMemory(mb, task)
    shim.freeze()
    base_day = max((p.get("last_day", 0) for p in mb.pieces), default=0) + 1
    toolbox = adapter.get_toolbox(shim, client, include_builtin=True)
    agent = HealthClawAgent(shim, toolbox, client, e2_mode=True)

    out_path = task_dir / "test_memorybank.jsonl"
    done_ids = _read_done_ids(out_path)
    cases = adapter.load_cases("test")
    n_limit = 5 if args.smoke else args.n
    if n_limit:
        cases = cases[:n_limit]
    todo = [c for c in cases if c["case_id"] not in done_ids]
    print(f"[mb-test:{task}] todo={len(todo)}/{len(cases)} {mb.stats()}",
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
            shim._current_day = base_day + i
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
            print(f"[mb-test:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "retrieved": [{"id": h["id"], "score": h["score"],
                              "retention": h.get("retention")} for h in hits],
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
            print(f"[mb-test:{task}] case {case['case_id']} already written by "
                  f"another instance, skipped", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        done_ids.add(case["case_id"])
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        n_done += 1
        if n_done % 20 == 0:
            print(f"[mb-test:{task}] {n_done}/{len(todo)} "
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
    print(f"[mb-test:{task}] n={len(rewards)} mean_reward={mean:.4f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["accumulate", "test"])
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--n", type=int, default=0, help="case 数上限；0=全部")
    ap.add_argument("--smoke", action="store_true", help="smoke：n=5")
    ap.add_argument("--channel", default="doubao_memorybank",
                    help="限速通道名（独立于主表/epoch/mem0/memp）")
    ap.add_argument("--app-id", default=None, help="独立 AppID（不传则用 .env 默认）")
    args = ap.parse_args()

    task = args.task
    adapter = load_adapter(task)
    base_dir = HERE
    client = make_doubao_client(rpm=500, channel=args.channel, app_id=args.app_id or None)
    cached = EmbedDiskCache(client, base_dir / task / "memorybank_store" / "embed_cache.json")
    print(f"[mb:{task}:{args.phase}] channel={args.channel} model={client.model}",
          flush=True)
    if args.phase == "accumulate":
        run_accumulate(args, task, adapter, cached, base_dir)
    else:
        run_test(args, task, adapter, cached, base_dir)


if __name__ == "__main__":
    main()
