# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

THRESHOLDS: Dict[str, float] = {
    "diabetes_readmission": 0.88,
    "geneturing": 0.38,
    "odir5k": 0.90,
    "icu_sofa": 0.50,
    "mimic_icd": 0.52,
    "medcalc": 0.30,
}
DEFAULT_THRESHOLD = 0.5
INSTR = ("Does `experience` qualify as a reusable method rule worth storing for future "
         "cases of this task? Judge from `experience` and `case_brief` only.")
CRIT = {"true": "A condition→action rule with concrete elements (formula/unit/precision/tool param/label rule/output format) that generalizes beyond the current case; a lesson must match the recorded error.",
        "false": "Is vague methodology with no concrete action, contradicts the recorded error, fabricates steps that did not happen, or requires character-level operations a text LLM cannot do."}
QUESTION_MAX = 9500
GOLD_MAX = 1000
ANSWER_MAX = 2000
HINT_MAX = 200

import re as _re

_SERVICE_PAT = _re.compile(
    r"服务.{0,8}(异常|不可用|故障)|连接异常|请检查服务|服务状态|无法完成本|"
    r"无法获取|暂时无法|service[\w ]{0,16}(unavailable|error|abnormal|fail)", _re.I)


class JeVFatalError(RuntimeError):
    pass


class JeVJudgeClient:
    def __init__(self, task: str, host: Optional[str] = None, port: Optional[int] = None,
                 model: str = "kev-latest", ledger_dir: Optional[Path] = None,
                 timeout: float = 90.0, retries: int = 3):
        self.task = task
        self.host = host or os.environ.get("E2_JEV_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("E2_JEV_PORT", "8023"))
        self.model = model
        self.timeout = timeout
        self.retries = max(1, retries)
        self.threshold = float(THRESHOLDS.get(task, DEFAULT_THRESHOLD))
        if task not in THRESHOLDS:
            print(f"[jev:{task}] no fitted task threshold; using {DEFAULT_THRESHOLD}", flush=True)
        self.url = f"http://{self.host}:{self.port}/v1/systemone"
        self._session = requests.Session()
        self._session.trust_env = False
        self._ledger_path: Optional[Path] = None
        if ledger_dir is not None:
            ledger_dir = Path(ledger_dir)
            ledger_dir.mkdir(parents=True, exist_ok=True)
            self._ledger_path = ledger_dir / f"jev_decisions_{task}.jsonl"
        self.n_calls = 0
        self.n_approved = 0
        self.n_rejected = 0
        self.n_quality_gate = 0
        self.latency_ms_sum = 0.0
        self.tokens_in_sum = 0
        self.tokens_out_sum = 0

    def _post_once(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = self._session.post(self.url, json=payload, timeout=self.timeout,
                               headers={"Content-Type": "application/json"},
                               proxies={"http": None, "https": None})
        r.raise_for_status()
        return r.json()

    def _ask_serve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"state": state, "model": self.model,
                   "questions": {"pass": {"type": "noul", "instructions": INSTR,
                                          "criteria": CRIT}}}
        last_err: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                return self._post_once(payload)
            except Exception as exc:
                last_err = exc
                time.sleep(3 + attempt * 4)
        raise JeVFatalError(
            f"jev serve unreachable ({self.url}) after {self.retries} retries: "
            f"{type(last_err).__name__}: {str(last_err)[:200]}")

    def _ledger(self, rec: Dict[str, Any]) -> None:
        if self._ledger_path is None:
            return
        try:
            with open(self._ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[jev:{self.task}] ledger write failed: {exc}", flush=True)

    def verify_experience(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(ctx.get("kind") or "per_case")
        _hit = _SERVICE_PAT.search((ctx.get("points") or "") + " " + (ctx.get("scope") or ""))
        if _hit:
            self.n_calls += 0
            self.n_rejected += 1
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": self.task,
                   "kind": kind, "case_idx": ctx.get("case_idx"),
                   "scope": (ctx.get("scope") or "")[:60],
                   "points": (ctx.get("points") or "")[:160],
                   "reward": ctx.get("reward"), "p": None, "threshold": self.threshold,
                   "approved": False, "hard_rule": f"service_phrase:{_hit.group(0)}",
                   "latency_ms": 0, "serve_latency_ms": 0,
                   "input_tokens": 0, "output_tokens": 0}
            self._ledger(rec)
            return {"approved": False,
                    "reason": f"hard_rule: service-copium pattern ({_hit.group(0)})",
                    "raw": json.dumps(rec, ensure_ascii=False), "unverified": False,
                    "skip_write": True,
                    "jev": {"p": None, "threshold": self.threshold,
                            "latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0}}
        state = build_state(task=self.task,
                            reward=ctx.get("reward"),
                            error_hint=ctx.get("error_hint"),
                            agent_answer=ctx.get("answer_full") or ctx.get("answer") or "",
                            tool_calls=ctx.get("tool_calls"),
                            question=ctx.get("question") or "",
                            gold=ctx.get("gold") or "",
                            scope=ctx.get("scope") or "",
                            points=ctx.get("points") or "")
        t0 = time.time()
        data = self._ask_serve(state)
        latency = (time.time() - t0) * 1000.0
        try:
            p = float(data["answers"]["pass"]["noul"])
        except Exception as exc:
            raise JeVFatalError(f"jev serve bad response: {str(data)[:200]} ({exc})")
        usage = data.get("usage") or {}
        lat_serve = float(data.get("latency_ms") or 0.0)
        approved = p >= self.threshold
        self.n_calls += 1
        self.n_approved += 1 if approved else 0
        self.n_rejected += 0 if approved else 1
        self.latency_ms_sum += latency
        self.tokens_in_sum += int(usage.get("input_tokens") or 0)
        self.tokens_out_sum += int(usage.get("output_tokens") or 0)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": self.task, "kind": kind,
               "case_idx": ctx.get("case_idx"), "scope": (ctx.get("scope") or "")[:60],
               "points": (ctx.get("points") or "")[:160],
               "reward": ctx.get("reward"), "p": round(p, 4),
               "threshold": self.threshold, "approved": approved,
               "latency_ms": round(latency, 1), "serve_latency_ms": round(lat_serve, 1),
               "input_tokens": int(usage.get("input_tokens") or 0),
               "output_tokens": int(usage.get("output_tokens") or 0)}
        self._ledger(rec)
        reason = (f"jev p={p:.3f} {'>=' if approved else '<'} t={self.threshold:.2f} [{kind}]")
        return {"approved": approved, "reason": reason, "raw": json.dumps(rec, ensure_ascii=False),
                "unverified": False, "skip_write": not approved,
                "jev": {"p": p, "threshold": self.threshold, "latency_ms": latency,
                        "input_tokens": rec["input_tokens"], "output_tokens": rec["output_tokens"]}}

    def summary(self) -> str:
        avg_lat = self.latency_ms_sum / self.n_calls if self.n_calls else 0.0
        return (f"calls={self.n_calls} approved={self.n_approved} rejected={self.n_rejected} "
                f"qgate={self.n_quality_gate} avg_latency_ms={avg_lat:.0f} "
                f"tokens_in={self.tokens_in_sum} tokens_out={self.tokens_out_sum} "
                f"luna_calls=0")


def build_state(task: str, reward, error_hint, agent_answer, tool_calls,
                scope: str, points: str, question: str = "", gold: str = "") -> Dict[str, Any]:
    try:
        reward_val = float(reward) if reward is not None else None
    except (TypeError, ValueError):
        reward_val = None
    return {"task": task,
            "case_brief": {"reward": reward_val,
                           "error_hint": (error_hint or "")[:HINT_MAX],
                           "agent_answer": (agent_answer or "")[:ANSWER_MAX],
                           "question": (question or "")[:QUESTION_MAX],
                           "gold": (gold or "")[:GOLD_MAX],
                           "tool_calls": int(tool_calls or 0)},
            "experience": {"scope": scope, "points": points}}


def make_jev_judge_client(task: str, ledger_dir: Optional[Path] = None,
                          port: Optional[int] = None) -> JeVJudgeClient:
    return JeVJudgeClient(task=task, ledger_dir=ledger_dir, port=port)


def ping(host: Optional[str] = None, port: Optional[int] = None,
         task: str = "ping", timeout: float = 60.0) -> Dict[str, Any]:
    c = JeVJudgeClient(task=task, host=host, port=port, timeout=timeout, retries=1)
    state = build_state(task="t", reward=1.0, error_hint="", agent_answer="",
                        tool_calls=0, scope="s", points="p")
    return c._ask_serve(state)
