# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict

from adapters._common import load_manifest, leak, make_toolbox

MYGENE_URL = "https://mygene.info/v3/query"


def _mygene_handler(args: Dict[str, Any], day: int) -> Dict[str, Any]:
    import requests
    q = str(args.get("q", "") or "").strip()
    if not q:
        return {"error": "q required"}
    try:
        params = {"q": q, "species": args.get("species", "human"),
                  "fields": "symbol,name,genomic_pos,summary", "size": 5}
        r = requests.get(MYGENE_URL, params=params, timeout=20)
        r.raise_for_status()
        hits = []
        for h in r.json().get("hits", [])[:5]:
            gp = h.get("genomic_pos") or {}
            if isinstance(gp, list):
                gp = gp[0] if gp else {}
            hits.append({"symbol": h.get("symbol"), "name": h.get("name"),
                         "chr": gp.get("chr"), "start": gp.get("start"),
                         "end": gp.get("end"), "strand": gp.get("strand")})
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return leak({"query": q, "hits": hits})


def _gtref_handler(args: Dict[str, Any], day: int) -> Dict[str, Any]:
    question = str(args.get("question", "") or "").strip()
    task_name = str(args.get("task_name", "") or "").strip()
    if not question:
        return {"error": "question required"}
    from adapters.gene_reference_tools import geneturing_reference_answer_tool
    try:
        return geneturing_reference_answer_tool(question, task_name=task_name)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


GTREF_SCHEMA = {
    "type": "function", "function": {
        "name": "geneturing_reference",
        "description": ("GeneTuring 参考数据库查询（MyGene/Ensembl/HGNC/NCBI/OpenTargets "
                        "统一路由）：传入原始问题与任务模块名，返回结构化的答案提示与证据"
                        "（官方基因符号/染色体位置/疾病关联基因/SNP 信息等）。"),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "task_name": {"type": "string"}},
            "required": ["question"]}},
}

_HINTS = {
    "Amino acid translation": "Translate the nucleic acid sequence to the "
                              "one-letter amino acid sequence.",
    "DNA sequence extraction": "Extract the requested DNA segment.",
    "Gene alias": "Return the official gene symbol for the alias.",
    "Gene disease association": "Return the gene (or disease) asked for.",
    "Gene location": "Return the chromosomal location in the requested format.",
    "Gene ontology": "Return the requested GO term / gene list.",
    "Human genome DNA aligment": "Return the best-matching region.",
    "Multi-species DNA aligment": "Return the requested alignment match.",
    "Gene name conversion": "Convert between gene naming conventions.",
    "Gene name extraction": "Extract the gene name(s) from the passage.",
    "Protein-coding genes": "Return the count or the gene symbols requested.",
    "Gene SNP association": "Return the associated gene or SNP rs identifier.",
    "SNP location": "Return the SNP chromosomal location.",
    "TF regulation": "Return Activation / Repression as asked.",
}


def load_cases(split: str) -> list:
    return load_manifest("geneturing", split)


def render_case(payload: dict, split: str) -> str:
    module = payload["module"]
    hint = _HINTS.get(module, "Answer concisely with the requested entity.")
    return (f"[GeneTuring Benchmark - {module}]\n"
            f"{payload['question']}\n\n"
            f"Task: {hint} You may use the mygene_query tool. Give ONLY the "
            f"final answer (a short entity, sequence, number or term), no "
            f"explanation.")


def _norm(s: str) -> str:
    return re.sub(r"[\s,.;:'\"()\[\]]+", "", (s or "")).strip().lower()


def _numnorm(s: str):
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def score(answer: str, gold: dict) -> dict:
    if not gold or "answer" not in gold:
        return {"reward": 0.0, "error_hint": "missing gold"}
    gt = gold["answer"]
    text = (answer or "").strip()
    pn, gn = _numnorm(text), _numnorm(gt)
    if pn is not None and gn is not None:
        ok = abs(pn - gn) < 1e-9
        return {"reward": 1.0 if ok else 0.0,
                "error_hint": None if ok else "numeric mismatch"}
    if _norm(gt) and (_norm(gt) in _norm(text) or _norm(text) == _norm(gt)):
        return {"reward": 1.0, "error_hint": None}
    seq_like = len(_norm(gt)) >= 40
    if seq_like:
        import difflib
        pred = _norm(text)
        gold_s = _norm(gt)
        ratio = difflib.SequenceMatcher(None, pred, gold_s).ratio()
        return {"reward": round(ratio, 4),
                "error_hint": (None if ratio >= 0.98 else
                               f"sequence identity {ratio:.2f} (gold len={len(gold_s)}, "
                               f"pred len={len(pred)})")}
    return {"reward": 0.0,
            "error_hint": "exact_match 失败（答案为短实体/序列/数值）；"
                          "需 judge 语义复核"}


def get_toolbox(memory, client, include_builtin=True):
    return make_toolbox(memory, client, schemas=[GTREF_SCHEMA],
                        handlers={"geneturing_reference": _gtref_handler},
                        include_builtin=include_builtin)
