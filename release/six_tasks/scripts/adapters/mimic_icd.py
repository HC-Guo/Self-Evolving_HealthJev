# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from adapters._common import DATA_DIR, extract_answer_pred, leak, load_manifest, make_toolbox

_CAND_CACHE: Dict[str, Any] = {}


def _candidates() -> Dict[str, Any]:
    if not _CAND_CACHE:
        p = DATA_DIR / "mimic_icd" / "candidates_top30.json"
        lst = json.loads(p.read_text(encoding="utf-8"))
        _CAND_CACHE["by_code"] = {c["code"]: c for c in lst}
        _CAND_CACHE["list"] = lst
    return _CAND_CACHE


def _norm_code(s: str) -> str:
    return str(s).strip().upper().replace(".", "").replace(" ", "")


def _icd_lookup_handler(args: Dict[str, Any], day: int) -> Dict[str, Any]:
    code = _norm_code(str(args.get("code", "") or ""))
    if not code:
        return {"error": "code required, e.g. 'I10'"}
    cand = _candidates()
    by_code = cand["by_code"]
    if code not in by_code:
        near = [c["code"] for c in cand["list"] if c["code"].startswith(code)]
        return {"error": f"code {code} not in top-30 candidate set",
                "near_matches": near[:5] if near else []}
    c = by_code[code]
    return leak({"code": c["code"], "description": c["desc"],
                 "frequency_rank": c["rank"],
                 "note": "static dictionary lookup, measurement reference only"})


ICD_LOOKUP_SCHEMA = {
    "type": "function", "function": {
        "name": "icd_lookup",
        "description": ("查询候选 ICD-10 编码的名称/常见对应诊断描述（静态字典，"
                        "确定性）。args: code（如 'I10'）"),
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}}, "required": ["code"]}},
}


def load_cases(split: str) -> list:
    return load_manifest("mimic_icd", split)


def _render_candidates() -> str:
    cand = _candidates()["list"]
    return "\n".join(f"  {c['code']}: {c['desc']}" for c in cand)


def render_case(payload: dict, split: str, with_tools: bool = False) -> str:
    tools_hint = (" You may use icd_lookup to check the exact meaning of any "
                  "candidate code before deciding." if with_tools else "")
    return (f"[Discharge Summary ICD Coding]\n"
            f"Patient admission {payload['hadm_id']} (subject {payload['subject_id']}).\n"
            f"Below is the de-identified discharge summary "
            f"({payload['note_chars']} chars):\n\n"
            f"{payload['note']}\n\n"
            f"Candidate ICD-10 codes (top-30, with short descriptions):\n"
            f"{_render_candidates()}\n\n"
            f"Task: predict which of the candidate codes ACTUALLY APPLY to this "
            f"admission (multi-label subset; codes outside the candidate list are "
            f"out of scope and must NOT be predicted).{tools_hint}\n"
            f"End your reply with exactly one line: 'Answer: ICD1,ICD2,...' "
            f"(comma-separated, in any order). If none apply, write 'Answer: NONE'.")


def score(answer: str, gold: dict) -> dict:
    if not gold or "codes" not in gold:
        return {"reward": 0.0, "error_hint": "missing gold"}
    raw = extract_answer_pred(answer or "", prefix="answer")
    if raw is None:
        return {"reward": 0.0,
                "error_hint": ("no ANSWER line found (output was ignored; "
                               "must end with 'Answer: ICD1,ICD2,...')")}
    pred = set() if _norm_code(raw) in ("NONE", "NA", "N/A", "") else \
        {_norm_code(c) for c in raw.split(",") if c.strip()}
    gt = {_norm_code(c) for c in gold["codes"]}
    inter = len(pred & gt)
    denom = len(pred) + len(gt)
    f1 = (2.0 * inter / denom) if denom else 0.0
    hint = None
    if f1 < 1.0:
        miss = sorted(gt - pred)
        extra = sorted(pred - gt)
        hint = (f"ICD set mismatch: missed={miss[:6]}, "
                f"wrong={extra[:6]}; reread the discharge summary section by "
                f"section (admission dx, hospital course, discharge dx)")
    return {"reward": round(f1, 4), "error_hint": hint}


def get_toolbox(memory, client, include_builtin=True):
    return make_toolbox(memory, client,
                        schemas=[ICD_LOOKUP_SCHEMA],
                        handlers={"icd_lookup": _icd_lookup_handler},
                        include_builtin=include_builtin)
