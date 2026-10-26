# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from adapters._common import load_manifest, make_toolbox

_GENDER = {0: "female", 1: "male"}
_ICU_TYPE = {1: "coronary care unit", 2: "cardiac surgery recovery unit",
             3: "medical ICU", 4: "surgical ICU"}


def load_cases(split: str) -> list:
    return load_manifest("icu_sofa", split)


def render_case(payload: dict, split: str) -> str:
    demo = payload["demo"]
    g = _GENDER.get(int(demo.get("Gender", -1)), "unknown")
    t = _ICU_TYPE.get(int(demo.get("ICUType", -1)), "unknown")
    lines = []
    for k, s in sorted(payload["measurements"].items()):
        lines.append(f"  {k}: n={s['n']} min={s['min']} max={s['max']} "
                     f"mean={s['mean']}")
    meas = "\n".join(lines) if lines else "  (no valid measurements)"
    return (f"[ICU SOFA Score Estimation]\n"
            f"Record {payload['record_id']}: {int(demo.get('Age', 0))}-year-old "
            f"{g}, ICU type: {t}, height={demo.get('Height')} cm, "
            f"weight={demo.get('Weight')} kg.\n"
            f"Measurement summary during the ICU stay (abnormal/missing values "
            f"were excluded; -1 means unavailable):\n{meas}\n\n"
            f"Task: estimate the patient's SOFA (Sequential Organ Failure "
            f"Assessment) total score (0-24) from these measurements. Reason "
            f"organ system by organ system (respiration, coagulation, liver, "
            f"cardiovascular, CNS, renal) using the summary statistics, then "
            f"give the total. End with exactly one line: 'Answer: <integer>'.")


def score(answer: str, gold: dict) -> dict:
    if not gold or "sofa" not in gold:
        return {"reward": 0.0, "error_hint": "missing gold"}
    nums = re.findall(r"-?\d+", (answer or "").replace(",", ""))
    if not nums:
        return {"reward": 0.0, "error_hint": "no number found in answer"}
    try:
        pred = int(nums[-1])
    except ValueError:
        return {"reward": 0.0, "error_hint": "unparsable integer"}
    gt = int(gold["sofa"])
    if pred == gt:
        return {"reward": 1.0, "error_hint": None}
    if abs(pred - gt) == 1:
        return {"reward": 0.5,
                "error_hint": "off-by-one SOFA estimate; recheck the weakest "
                              "organ subsystem"}
    return {"reward": 0.0,
            "error_hint": f"SOFA mismatch (pred={pred}, |diff|>=2); "
                          f"re-derive per-organ subscores"}


def get_toolbox(memory, client, include_builtin=True):
    return make_toolbox(memory, client, schemas=[], handlers={},
                        include_builtin=include_builtin)
