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
from memp_memory_e2 import MempMemory


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
    def __init__(self, memp: MempMemory):
        self._memp = memp

    def query(self, query: str, current_day: int = 0, top_k: int = 5) -> List[Dict[str, Any]]:
        docs = self._memp.retrieve(query, top_k=top_k)
        return [{"id": d["id"], "text": d.get("workflow", ""),
                 "score": d.get("last_hit_score", 0.0)} for d in docs]

    def add_chunk(self, *args, **kwargs):
        return None

    def stats(self):
        return {"n_workflows": len(self._memp.documents)}


class MempShimMemory:
    def __init__(self, memp: MempMemory, task: str):
        self.memp = memp
        self.task = task
        self.frozen = False
        self.l2: Dict[str, Any] = {"recent_metrics": {}, "medications": [],
                                   "allergies": [], "history": [], "preferences": []}
        self.index = _ShimIndex(memp)
        self._current_query: Optional[str] = None
        self._last_hits: List[Dict[str, Any]] = []

    def _hits(self, query: Optional[str]) -> List[Dict[str, Any]]:
        if not query:
            return []
        if query == self._current_query and self._last_hits is not None:
            return self._last_hits
        try:
            docs = self.memp.retrieve(query, top_k=3)
        except Exception as exc:
            print(f"    [memp-retrieve] error: {type(exc).__name__}: {str(exc)[:120]}",
                  flush=True)
            docs = []
        self._current_query = query
        self._last_hits = docs
        return docs

    def system_prompt(self, day: int, question: Optional[str] = None) -> str:
        query = question or self._current_query
        docs = self._hits(query)
        if docs:
            lines = ["【相关经验】（Memp workflow 检索 top-3 命中，供参考）"]
            for d in docs:
                lines.append(f"- [{d['id']}|{d.get('last_hit_score', 0.0)}] "
                             f"{d.get('workflow', '')[:600]}")
        else:
            lines = ["【相关经验】（本题无命中经验）"]
        return (f"{E2_CONSTITUTION}\n\n{E2_CAPABILITY_INDEX}\n\n"
                f"当前是第 {day} 个 case（任务：{self.task}）。\n\n" + "\n".join(lines))

    def start_day(self, day: int, hook_day: bool = False, hook_entities=None):
        return None

    def freeze(self):
        self.frozen = True

    def write_profile(self, payload: Dict[str, Any], day: int) -> Dict[str, Any]:
        return {"ok": False, "note": "memp baseline: profile write disabled"}

    def add_sop_entries(self, *args, **kwargs):
        return {"actions": []}

    def write_episode(self, *args, **kwargs):
        return None

    def stats(self):
        return {"mode": "memp", "n_docs": len(self.memp.documents), "frozen": self.frozen}


def _render_trajectory(messages: List[Dict[str, Any]], max_chars: int = 6000) -> str:
    lines: List[str] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            content = str(m.get("content", ""))[:300]
            lines.append(json.dumps({"step_role": "user", "thought": content},
                                    ensure_ascii=False))
        elif role == "assistant":
            tcs = m.get("tool_calls") or []
            if tcs:
                thought = str(m.get("content") or "")[:200]
                for tc in tcs:
                    fn = tc.get("function", {}) or {}
                    action = (f"{fn.get('name', '')}"
                              f"({str(fn.get('arguments', ''))[:200]})")
                    lines.append(json.dumps({"step_role": "assistant",
                                             "thought": thought, "action": action},
                                            ensure_ascii=False))
            elif m.get("content"):
                lines.append(json.dumps(
                    {"step_role": "assistant", "action": "Reply to user",
                     "observation": str(m["content"])[:300]}, ensure_ascii=False))
        elif role == "tool":
            lines.append(json.dumps(
                {"step_role": "tool",
                 "observation": str(m.get("content", ""))[:300]}, ensure_ascii=False))
    return "\n".join(lines)[:max_chars]


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
    memp = MempMemory(client, task_dir / "memp_store")
    shim = MempShimMemory(memp, task)
    toolbox = adapter.get_toolbox(shim, client, include_builtin=True)
    agent = HealthClawAgent(shim, toolbox, client, e2_mode=True)

    state_path = task_dir / "memp_state.json"
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

    print(f"[memp-acc:{task}] todo={len(todo)} done={len(done_ids)} "
          f"docs={len(memp.documents)}", flush=True)
    t0 = time.time()
    for i, case in enumerate(todo):
        try:
            idx = len(done_ids) + i
            user_text = _render_accum(adapter, case["payload"])
            shim._current_query = None
            shim._last_hits = []
            docs = shim._hits(user_text)
            agent.start_day(idx, hook_day=False)
            answer = agent.respond(user_text)
            sc = adapter.score(answer, case.get("gold"))
            reward = float(sc.get("reward", 0.0))
            error_hint = sc.get("error_hint")
            trajectory = _render_trajectory(agent.messages)
            drec = memp.update_vanilla(user_text, trajectory, idx)
            mem_ops = 1 if drec else 0
        except Exception as exc:
            print(f"[memp-acc:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "idx": idx, "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "mem_ops": mem_ops,
               "memp": {"n_docs": len(memp.documents)},
               "retrieved": [{"id": d["id"], "score": d.get("last_hit_score", 0.0)}
                             for d in docs],
               "tool_calls": len(toolbox.log), "ts": time.strftime("%H:%M:%S")}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state["done"].append(case["case_id"])
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        if (i + 1) % 5 == 0 or i + 1 == len(todo):
            rate = (time.time() - t0) / max(1, i + 1)
            print(f"[memp-acc:{task}] {i+1}/{len(todo)} last_reward={reward:.2f} "
                  f"mem_ops={mem_ops} sec/case={rate:.0f} docs={len(memp.documents)}",
                  flush=True)
    if isinstance(client, EmbedDiskCache):
        client.save()
    print(f"[memp-acc:{task}] DONE done={len(state['done'])} "
          f"docs={len(memp.documents)}", flush=True)


def run_test(args, task: str, adapter, client, base_dir: Path):
    task_dir = base_dir / task
    if not (task_dir / "memp_store" / "documents.json").exists():
        raise SystemExit(f"[memp-test:{task}] 未找到积累产物 {task_dir}/memp_store/，"
                         f"请先跑 accumulate 阶段")
    memp = MempMemory(client, task_dir / "memp_store")
    shim = MempShimMemory(memp, task)
    shim.freeze()
    toolbox = adapter.get_toolbox(shim, client, include_builtin=True)
    agent = HealthClawAgent(shim, toolbox, client, e2_mode=True)

    out_path = task_dir / "test_memp.jsonl"
    done_ids = _read_done_ids(out_path)
    cases = adapter.load_cases("test")
    n_limit = 5 if args.smoke else args.n
    if n_limit:
        cases = cases[:n_limit]
    todo = [c for c in cases if c["case_id"] not in done_ids]
    print(f"[memp-test:{task}] todo={len(todo)}/{len(cases)} docs={len(memp.documents)}",
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
            docs = shim._hits(user_text)
            agent.start_day(i, hook_day=False)
            answer = agent.respond(user_text)
            sc = adapter.score(answer, case.get("gold"))
            reward = float(sc.get("reward", 0.0))
            error_hint = sc.get("error_hint")
        except Exception as exc:
            print(f"[memp-test:{task}] case {case['case_id']} failed, skipped: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        rec = {"case_id": case["case_id"], "reward": reward,
               "error_hint": error_hint, "answer": answer[:2000],
               "retrieved": [{"id": d["id"], "score": d.get("last_hit_score", 0.0)}
                             for d in docs],
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
            print(f"[memp-test:{task}] case {case['case_id']} already written by "
                  f"another instance, skipped", flush=True)
            toolbox.log.clear()
            toolbox.recalled_texts.clear()
            continue
        done_ids.add(case["case_id"])
        toolbox.log.clear()
        toolbox.recalled_texts.clear()
        n_done += 1
        if n_done % 20 == 0:
            print(f"[memp-test:{task}] {n_done}/{len(todo)} "
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
    print(f"[memp-test:{task}] n={len(rewards)} mean_reward={mean:.4f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["accumulate", "test"])
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--n", type=int, default=0, help="case 数上限；0=全部")
    ap.add_argument("--smoke", action="store_true", help="smoke：n=5")
    ap.add_argument("--channel", default="doubao_memp", help="限速通道名（独立于主表/epoch/mem0）")
    ap.add_argument("--app-id", default=None, help="独立 AppID（不传则用 .env 默认）")
    args = ap.parse_args()

    task = args.task
    adapter = load_adapter(task)
    base_dir = HERE
    client = make_doubao_client(rpm=500, channel=args.channel, app_id=args.app_id or None)
    cached = EmbedDiskCache(client, base_dir / task / "memp_store" / "embed_cache.json")
    print(f"[memp:{task}:{args.phase}] channel={args.channel} model={client.model}",
          flush=True)
    if args.phase == "accumulate":
        run_accumulate(args, task, adapter, cached, base_dir)
    else:
        run_test(args, task, adapter, cached, base_dir)


if __name__ == "__main__":
    main()
