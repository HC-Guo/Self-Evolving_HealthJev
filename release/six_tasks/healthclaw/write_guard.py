# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

UNIT_CONV_FACTORS = (18.0, 88.4, 88.57, 59.48, 38.67, 100.0, 1000.0)

REF_RANGE_PAT = re.compile(
    r"(正常参考范围|正常范围|参考范围|参考区间|正常值|参考值|范围为|正常|区间|范围|控制在|保持在|维持在|目标)\s*[:：]?\s*"
    r"[\d.]+\s*[~～\-—–至到]\s*[\d.]+")
PAREN_RANGE_PAT = re.compile(r"[（(]\s*[\d.]+\s*[~～\-—–至到]\s*[\d.]+[^）)]*[）)]")
RANGE_TAIL_PAT = re.compile(r"\s*[~～\-—–至到]\s*\d")


def _num_matches(v: float, tv: float) -> bool:
    if tv == 0:
        return abs(v) < 1e-9
    r = v / tv
    if 0.95 <= r <= 1.05:
        return True
    for f in UNIT_CONV_FACTORS:
        if abs(r * f - 1) < 0.05 or abs(r / f - 1) < 0.05:
            return True
    return False


def strip_reference_fragments(text: str) -> str:
    t = REF_RANGE_PAT.sub(" ", text or "")
    t = PAREN_RANGE_PAT.sub(" ", t)
    return t


def extract_claimed_values(text: str) -> List[Dict[str, Any]]:
    t = strip_reference_fragments(text)
    claims = []
    if not t:
        return claims
    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z]{2,12})[^\d。！？!?\n；;]{0,15}?(\d+(?:\.\d+)?)", t):
        name, num = m.group(1), m.group(2)
        seg = t[max(0, m.start(2) - 16): m.end(2) + 8]
        if RANGE_TAIL_PAT.match(t[m.end(2): m.end(2) + 6]):
            continue
        claims.append({"name": name, "value": float(num), "context": seg[:60]})
    return claims


class WriteGuard:
    def __init__(self, day_sources: List[Dict[str, str]]):
        self.sources = []
        for s in day_sources or []:
            m = re.search(r"\d+(?:\.\d+)?", str(s.get("value", "")))
            if m:
                self.sources.append({"name": str(s.get("name", "")),
                                     "value": str(s.get("value", "")),
                                     "truth": float(m.group()),
                                     "source": s.get("source", "goals_fact")})

    def check_claim(self, name: str, value: float) -> Optional[Dict[str, Any]]:
        name_norm = re.sub(r"[\s，。、（）()]", "", str(name))
        for s in self.sources:
            src_norm = re.sub(r"[\s，。、（）()]", "", s["name"])
            if not src_norm:
                continue
            if src_norm in name_norm or name_norm in src_norm:
                if not _num_matches(value, s["truth"]):
                    return {"status": "conflict", "item": s["name"],
                            "truth": s["value"], "claimed": value, "source": s["source"]}
                return {"status": "ok", "item": s["name"], "truth": s["value"]}
        return None

    def check_write_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        conflicts, approved = [], []
        for name, mv in (payload.get("recent_metrics") or {}).items():
            val = mv.get("value") if isinstance(mv, dict) else mv
            m = re.search(r"\d+(?:\.\d+)?", str(val))
            if not m:
                approved.append(str(name))
                continue
            res = self.check_claim(name, float(m.group()))
            if res and res["status"] == "conflict":
                conflicts.append(res)
            else:
                approved.append(str(name))
        for med in payload.get("medications") or []:
            drug = med.get("drug", "") if isinstance(med, dict) else str(med)
            dose = med.get("dose", "") if isinstance(med, dict) else ""
            m = re.search(r"\d+(?:\.\d+)?", str(dose))
            if m and drug:
                res = self.check_claim(drug, float(m.group()))
                if res and res["status"] == "conflict":
                    conflicts.append(res)
                else:
                    approved.append(str(drug))
            elif drug:
                approved.append(str(drug))
        return {"approved": approved, "conflicts": conflicts}

    def check_text_against_truth(self, text: str) -> List[Dict[str, Any]]:
        t = strip_reference_fragments(text)
        conflicts = []
        seen = set()
        for s in self.sources:
            if not s["name"]:
                continue
            pat = re.compile(re.escape(s["name"]) + r"[^\d。！？!?\n]{0,15}?(\d+(?:\.\d+)?)")
            for m in pat.finditer(t):
                if RANGE_TAIL_PAT.match(t[m.end(1): m.end(1) + 6]):
                    continue
                seg = t[max(0, m.start(1) - 16): m.end(1) + 8]
                if REF_RANGE_PAT.search(seg) or PAREN_RANGE_PAT.search(seg):
                    continue
                v = float(m.group(1))
                if not _num_matches(v, s["truth"]):
                    key = (s["name"], str(v))
                    if key in seen:
                        continue
                    seen.add(key)
                    conflicts.append({"item": s["name"], "truth": s["value"][:60],
                                      "said_value": str(v), "source": s["source"]})
        return conflicts[:10]
