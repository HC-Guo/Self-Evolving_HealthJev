# -*- coding: utf-8 -*-
from __future__ import annotations

from adapters._common import make_toolbox, load_manifest

LABELS = ["normal", "cataract", "glaucoma", "AMD", "hypertensive retinopathy",
          "myopia", "pathological myopia", "other abnormality"]


def load_cases(split: str) -> list:
    return load_manifest("odir5k", split)


def render_case(payload: dict, split: str, with_tools: bool = True) -> str:
    return (f"[Ocular Fundus Diagnosis]\n"
            f"Patient: {payload['age']}-year-old {payload['sex']}. "
            f"The fundus photograph is attached to this message: [[IMG:{payload['image_path']}]]\n\n"
            f"Task: carefully examine the fundus image yourself and diagnose it "
            f"into exactly one of: {'; '.join(LABELS)}. "
            f"Reason step by step about what you observe (optic disc, cup, vessels, "
            f"macula, hemorrhages/exudates, drusen etc.), then "
            f"end with exactly one line: 'Answer: <label>'.")


def score(answer: str, gold: dict) -> dict:
    if not gold or "label" not in gold:
        return {"reward": 0.0, "error_hint": "missing gold"}
    text = (answer or "").strip()
    gt = str(gold["label"]).lower()
    code = gold.get("label_code")
    from adapters._common import extract_answer_pred
    pred = extract_answer_pred(text)
    if pred is None:
        return {"reward": 0.0,
                "error_hint": "no ANSWER line (未按格式作答); 需 judge 语义复核"}
    ok = gt in pred.lower()
    if not ok and code is not None:
        ok = bool(__import__("re").search(rf"\b{code}\b", pred))
    return {"reward": 1.0 if ok else 0.0,
            "error_hint": None if ok else
            f"diagnosis mismatch (pred={pred[:60]!r}); review lesion signs; "
            f"需 judge 语义复核"}


def get_toolbox(memory, client, include_builtin=True):
    return make_toolbox(memory, client, schemas=[], handlers={},
                        include_builtin=include_builtin)
