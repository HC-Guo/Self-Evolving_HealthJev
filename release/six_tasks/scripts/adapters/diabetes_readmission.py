# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any, Dict

from adapters._common import DATA_DIR, load_manifest, leak, make_toolbox

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_PRIOR_CACHE: Dict[str, Any] = {}


def _pubmed_handler(args: Dict[str, Any], day: int) -> Dict[str, Any]:
    import requests
    term = str(args.get("query", "") or "").strip()
    if not term:
        return {"error": "query required"}
    try:
        r = requests.get(f"{EUTILS}/esearch.fcgi",
                         params={"db": "pubmed", "term": term, "retmax": 3,
                                 "retmode": "json"}, timeout=20)
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", []) or []
        arts = []
        if ids:
            r2 = requests.get(f"{EUTILS}/esummary.fcgi",
                              params={"db": "pubmed", "id": ",".join(ids),
                                      "retmode": "json"}, timeout=20)
            r2.raise_for_status()
            for pid in ids:
                doc = r2.json().get("result", {}).get(pid, {})
                arts.append({"pmid": pid,
                             "title": doc.get("title", "")[:300],
                             "journal": doc.get("source", ""),
                             "year": doc.get("pubdate", "")[:4]})
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return leak({"query": term, "articles": arts})


PUBMED_SCHEMA = {
    "type": "function", "function": {
        "name": "pubmed_search",
        "description": ("检索 PubMed 文献（标题/期刊/年份），用于查证糖尿病再入院"
                        "的风险因素与判读准则。args: query"),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}},
}


def _prior_table() -> Dict[str, Any]:
    if not _PRIOR_CACHE:
        p = DATA_DIR / "diabetes_readmission" / "stats_prior.json"
        _PRIOR_CACHE.update(json.loads(p.read_text(encoding="utf-8")))
    return _PRIOR_CACHE


def _time_bucket(days: Any) -> str:
    d = int(days or 0)
    return "<=3" if d <= 3 else ("4-7" if d <= 7 else ">=8")


def _med_bucket(n: Any) -> str:
    n = int(n or 0)
    if n <= 10:
        return "<=10"
    if n <= 20:
        return "11-20"
    if n <= 30:
        return "21-30"
    return ">30"


def _diag_group(code: Any) -> str:
    s = str(code).strip()
    if s in ("", "nan", "None", "?"):
        return "missing"
    if s and s[0] in "EV":
        return "E/V-external"
    try:
        i = int(float(s))
    except ValueError:
        return "other"
    for lo, hi, name in [(1, 139, "001-139-infectious"), (140, 239, "140-239-neoplasm"),
                         (240, 279, "240-279-endocrine"), (280, 289, "280-289-blood"),
                         (290, 319, "290-319-mental"), (320, 389, "320-389-nervous"),
                         (390, 459, "390-459-circulatory"), (460, 519, "460-519-respiratory"),
                         (520, 579, "520-579-digestive"), (580, 629, "580-629-genitourinary"),
                         (630, 679, "630-679-pregnancy"), (680, 709, "680-709-skin"),
                         (710, 739, "710-739-musculoskeletal"), (740, 759, "740-759-congenital"),
                         (760, 779, "760-779-perinatal"), (780, 799, "780-799-symptoms"),
                         (800, 999, "800-999-injury")]:
        if lo <= i <= hi:
            return name
    return "other"


def _prior_bucket_values(payload: dict) -> Dict[str, str]:
    labs = payload.get("labs_summary", {})
    dg = payload.get("diagnoses", {})
    return {
        "age_bracket": str(payload.get("age_bracket")),
        "gender": str(payload.get("gender")),
        "race": str(payload.get("race")),
        "admission_type_id": str(payload.get("admission_type_id")),
        "discharge_disposition_id": str(payload.get("discharge_disposition_id")),
        "A1Cresult": str(labs.get("A1Cresult", "nan")),
        "insulin": str(labs.get("insulin_dose_change", "nan")),
        "time_in_hospital": _time_bucket(payload.get("time_in_hospital_days", 0)),
        "num_medications": _med_bucket(payload.get("num_medications", 0)),
        "diag_1_group": _diag_group(dg.get("diag_1")),
    }


def _readmit_prior_handler(args: Dict[str, Any], day: int) -> Dict[str, Any]:
    table = _prior_table()
    feats = args.get("features") or {}
    if not isinstance(feats, dict) or not feats:
        return {"error": "features kv required, e.g. {'age_bracket': '[70-80)'}"}
    out_feats = {}
    for k, v in feats.items():
        e = (table.get("conditional", {}).get(str(k), {}) or {}).get(str(v))
        if not e:
            out_feats[str(k)] = {"value": str(v), "note": "no bucket"}
            continue
        item = {"value": str(v), "n": e.get("n", 0)}
        if "p" in e:
            item["p_readmitted"] = e["p"]
        else:
            item["note"] = f"bucket n<{table.get('min_count')}, use prior only"
        out_feats[str(k)] = item
    return leak({"note": "prior reference only, NOT the answer; use to calibrate base rates",
                 "prior": table.get("prior"), "features": out_feats})


READMIT_PRIOR_SCHEMA = {
    "type": "function", "function": {
        "name": "readmit_prior_lookup",
        "description": ("查询糖尿病再入院的历史统计先验（仅由积累集 500 例构建，"
                        "不含任何测试信息）。先验参考，非答案；用于校准基率——"
                        "本任务中临床高危特征普遍存在而 <30 再入院率仅约 12%。"
                        "args: features（kv，如 age_bracket/[70-80)、A1Cresult/None）"),
        "parameters": {"type": "object", "properties": {
            "features": {"type": "object", "description": "特征 kv"}},
            "required": ["features"]}},
}


def load_cases(split: str) -> list:
    return load_manifest("diabetes_readmission", split)


def render_case(payload: dict, split: str) -> str:
    labs = payload["labs_summary"]
    return (f"[Diabetes 30-day Readmission Prediction]\n"
            f"Encounter summary:\n"
            f"- age bracket: {payload['age_bracket']}; gender: {payload['gender']}; "
            f"race: {payload['race']}\n"
            f"- admission_type_id: {payload['admission_type_id']}; "
            f"discharge_disposition_id: {payload['discharge_disposition_id']}\n"
            f"- time_in_hospital: {payload['time_in_hospital_days']} days; "
            f"num_lab_procedures: {payload['num_lab_procedures']}; "
            f"num_procedures: {payload['num_procedures']}; "
            f"num_medications: {payload['num_medications']}; "
            f"number_diagnoses: {payload['number_diagnoses']}\n"
            f"- diagnoses (ICD9): diag_1={payload['diagnoses']['diag_1']}, "
            f"diag_2={payload['diagnoses']['diag_2']}, "
            f"diag_3={payload['diagnoses']['diag_3']}\n"
            f"- labs/meds summary: max_glu_serum={labs['max_glu_serum']}; "
            f"A1Cresult={labs['A1Cresult']}; insulin={labs['insulin_dose_change']}; "
            f"change={labs['medication_change']}; diabetesMed={labs['diabetesMed_prescribed']}\n\n"
            f"Task: predict whether this patient will be readmitted within 30 "
            f"days (<30), after 30 days (>30), or not readmitted (NO). You may "
            f"use pubmed_search to check risk factors. End your reply with "
            f"exactly one line: 'Answer: <30' or 'Answer: >30' or 'Answer: NO'.")


def score(answer: str, gold: dict) -> dict:
    if not gold or "readmitted" not in gold:
        return {"reward": 0.0, "error_hint": "missing gold"}
    text = (answer or "").lower()
    m = re.findall(r"answer\s*[:=]?\s*(<\s*30|>\s*30|no\b)", text)
    pred = None
    if m:
        pred = m[-1].replace(" ", "")
        if pred == "no":
            pred = "NO"
    else:
        if "<30" in text:
            pred = "<30"
        elif ">30" in text:
            pred = ">30"
        elif re.search(r"\bno\b", text):
            pred = "NO"
    gt = gold["readmitted"]
    ok = pred == gt
    return {"reward": 1.0 if ok else 0.0,
            "error_hint": None if ok else
            (f"readmitted class mismatch (pred={pred}); reconsider discharge "
             f"disposition, utilization history proxies and labs; 需 judge 语义复核")}


def get_toolbox(memory, client, include_builtin=True):
    return make_toolbox(memory, client,
                        schemas=[PUBMED_SCHEMA, READMIT_PRIOR_SCHEMA],
                        handlers={"pubmed_search": _pubmed_handler,
                                  "readmit_prior_lookup": _readmit_prior_handler},
                        include_builtin=include_builtin)
