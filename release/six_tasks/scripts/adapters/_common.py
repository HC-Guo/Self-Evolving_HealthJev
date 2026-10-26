# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
E2_DIR = SCRIPTS_DIR.parent
DATA_DIR = Path(os.environ.get("HEALTHJEV_BIOMEDICAL_DATA_DIR")
                or str(E2_DIR / "data")).expanduser()
SPLITS = ("accumulate", "test")

TASKS = ("diabetes_readmission", "geneturing", "icu_sofa",
         "medcalc", "mimic_icd", "odir5k")
ADAPTER_REQUIRED_FNS = ("load_cases", "render_case", "score", "get_toolbox")


def load_adapter(task: str):
    if task not in TASKS:
        raise SystemExit(f"unknown task {task!r}; expected one of: {', '.join(TASKS)}")
    try:
        mod = __import__(f"adapters.{task}", fromlist=["*"])
    except ImportError as exc:
        raise SystemExit(f"adapter module 'adapters.{task}' not found: {exc}") from exc
    for fn in ADAPTER_REQUIRED_FNS:
        if not hasattr(mod, fn):
            raise SystemExit(f"adapter {task} missing {fn}()")
    return mod


def load_manifest(task: str, split: str) -> List[Dict[str, Any]]:
    assert split in SPLITS, f"unknown split {split}"
    path = DATA_DIR / task / f"manifest_{split}.jsonl"
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cases.append({"case_id": rec["case_id"],
                          "payload": rec.get("payload", {}),
                          "gold": rec.get("gold", {})})
    return cases


def leak(result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(result)
    out["input_visible_only"] = True
    out["gold_label_used"] = False
    out["future_cases_used"] = False
    return out


def make_toolbox(memory, client, schemas: Optional[List[Dict]] = None,
                 handlers: Optional[Dict[str, Any]] = None,
                 include_builtin: bool = True):
    from healthclaw.tools import ToolBox
    return ToolBox(memory, client, web_client=client,
                   include_builtin=include_builtin,
                   extra_schemas=list(schemas or []),
                   extra_handlers=dict(handlers or {}))


import re as _re

_TOOL_CALL_RE = _re.compile(r"<\|FunctionCallBegin\|>.*?<\|FunctionCallEnd\|>", _re.S)


def strip_tool_call_blocks(text: str) -> str:
    t = _TOOL_CALL_RE.sub("", text or "")
    if "<|FunctionCallBegin|>" in t:
        t = t.split("<|FunctionCallBegin|>")[0]
    return t


def extract_answer_pred(text: str, prefix: str = "answer") -> Optional[str]:
    pred = None
    for line in strip_tool_call_blocks(text).splitlines():
        s = line.strip()
        if s.lower().startswith(prefix):
            pred = s.split(":", 1)[-1].strip()
    return pred
