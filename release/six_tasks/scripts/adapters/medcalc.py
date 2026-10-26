# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import operator
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import tools_medcalc_formulas as _formulas
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    import tools_medcalc_formulas as _formulas

from adapters._common import E2_DIR, load_manifest, leak, make_toolbox

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}
_FUNCS = {"sqrt": lambda x: x ** 0.5, "abs": abs, "log": __import__("math").log,
          "log10": __import__("math").log10, "exp": __import__("math").exp}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and len(node.args) == 1:
        return _FUNCS[node.func.id](_safe_eval(node.args[0]))
    raise ValueError("unsupported expression")


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _parse_date_any(s: Any) -> Optional[Tuple[int, int, int]]:
    if s is None:
        return None
    t = str(s).strip()
    m = re.search(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})", t)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 12 and b <= 12:
                mo, d = b, a
            else:
                mo, d = a, b
        else:
            m = re.search(r"([A-Za-z]{3,9})\s*\.?\s*(\d{1,2})\s*,?\s*(\d{4})", t)
            if m and m.group(1).lower()[:3] in _MONTHS:
                mo, d, y = _MONTHS[m.group(1).lower()[:3]], int(m.group(2)), int(m.group(3))
            else:
                m = re.search(r"(\d{1,2})\s*\.?\s+([A-Za-z]{3,9})\s*\.?\s+(\d{4})", t)
                if m and m.group(2).lower()[:3] in _MONTHS:
                    d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()[:3]], int(m.group(3))
                else:
                    return None
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return None
    return (y, mo, d)


_DATE_TOKEN_RE = re.compile(
    r"(?:\d{4}\s*[年/\-.]\s*\d{1,2}\s*[月/\-.]\s*\d{1,2})"
    r"|(?:\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{4})"
    r"|(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
    r"|(?:\d{1,2}\.?\s+[A-Za-z]{3,9}\.?\s+\d{4})"
    r"|(?:\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?)")


def _last_date_in_text(text: str) -> Optional[Tuple[int, int, int]]:
    for m in reversed(list(_DATE_TOKEN_RE.finditer(text or ""))):
        d = _parse_date_any(m.group(0))
        if d is not None:
            return d
    return None


_WEEKS_DAYS_PAIR_RES = [
    re.compile(r"(\d+)\s*(?:weeks?|w)\s*(?:and|\+|,|of|\s)+\s*(\d+)\s*(?:days?|d)\b",
               re.I),
    re.compile(r"(\d+)\s*w(?:eek)?s?\s*,?\s*(\d+)\s*d(?:ay)?s?\b", re.I),
    re.compile(r"(\d{1,2})\+(\d)(?!\d)"),
]


def _parse_gestational(text: str) -> Optional[Tuple[int, Optional[int]]]:
    text = text or ""
    for pat in _WEEKS_DAYS_PAIR_RES:
        ms = list(pat.finditer(text))
        if ms:
            w, d = ms[-1].group(1), ms[-1].group(2)
            return (int(w), int(d))
    ms = list(re.finditer(r"(\d+)\s*weeks?\b", text, re.I))
    if ms:
        return (int(ms[-1].group(1)), None)
    ms = list(re.finditer(r"(\d+)\s*days?\b", text, re.I))
    if ms:
        return (None, int(ms[-1].group(1)))
    return None


def _is_gestational_gold(gold: dict) -> bool:
    ans = str(gold.get("answer", ""))
    return bool(re.search(r"\d+\s*weeks?", ans, re.I)
                and re.search(r"\d+\s*days?", ans, re.I))


_PHYS_RANGES = {
    "creatinine": (0.1, 50), "scr": (0.1, 50),
    "sodium": (95, 190), "potassium": (1.0, 12),
    "chloride": (50, 160), "bicarbonate": (2, 60),
    "glucose": (10, 2000),
    "hemoglobin": (1.0, 30), "hematocrit": (5, 80),
    "wbc": (0.05, 500), "platelet": (1.0, 3000),
    "heart_rate": (20, 260), "oxygen_sat": (30, 100),
    "sys_bp": (30, 300), "systolic": (30, 300),
    "dia_bp": (10, 200), "diastolic": (10, 200),
    "temperature": (25, 46),
    "albumin": (0.5, 10), "bilirubin": (0.05, 75), "inr": (0.3, 20),
    "calcium": (2, 25), "bun": (1, 500), "urea": (1, 500),
    "age": (0, 130), "weight": (0.3, 700), "height": (25, 260),
}


def _numeric_of(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (list, tuple)) and v and not isinstance(v[0], (list, tuple)):
        try:
            return float(v[0])
        except (TypeError, ValueError):
            return None
    if isinstance(v, dict):
        return _numeric_of(v.get("value", v.get("amount")))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _physiology_warnings(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for k, v in (params or {}).items():
        kl = str(k).lower()
        for frag, (lo, hi) in _PHYS_RANGES.items():
            if frag in kl:
                x = _numeric_of(v)
                if x is not None and not (lo <= x <= hi):
                    out.append({"parameter": k, "value": x,
                                "plausible_range": [lo, hi],
                                "message": (f"parameter {k}={x} is outside the "
                                            f"plausible range [{lo}, {hi}]; "
                                            f"check units or extraction errors")})
                break
    return out


def _flag_any(params: Dict[str, Any], *keys: str):
    for k in keys:
        if k in params:
            return _formulas._flag(params[k]), True
    return False, False


def _graded(points: int, detail: str) -> Dict[str, Any]:
    return {"item": "", "hit": points > 0, "points": points, "detail": detail,
            "type": "graded"}


def _score_breakdown(display: str, params: Dict[str, Any]) \
        -> Optional[List[Dict[str, Any]]]:
    p = params or {}
    items: List[Dict[str, Any]] = []
    name_l = (display or "").lower()

    if "cha2ds2" in name_l:
        for keys, label, pts in [(("chf",), "CHF history", 1),
                                 (("hypertension",), "Hypertension history", 1),
                                 (("diabetes",), "Diabetes history", 1),
                                 (("vascular_disease",),
                                  "Vascular disease (MI/PAD/aortic plaque)", 1)]:
            hit, spec = _flag_any(p, *keys)
            items.append({"item": label, "hit": hit, "points": pts if hit else 0,
                          "specified": spec})
        hit, spec = _flag_any(p, "stroke", "tia", "thromboembolism")
        items.append({"item": "Stroke/TIA/thromboembolism history", "hit": hit,
                      "points": 2 if hit else 0, "specified": spec})
        try:
            age = _formulas._age_years(p.get("age"))
            pts = 2 if age >= 75 else (1 if age >= 65 else 0)
            items.append({"item": "Age (>=75: 2, 65-74: 1, <65: 0)", "hit": pts > 0,
                          "points": pts, "value": age})
        except (TypeError, ValueError):
            pass
        try:
            items.append({"item": "Female sex", "hit": _formulas._sex(
                p.get("sex")) == "Female", "points": 1 if
                _formulas._sex(p.get("sex")) == "Female" else 0,
                "value": _formulas._sex(p.get("sex"))})
        except (TypeError, ValueError):
            pass
        return items

    if "wells" in name_l and "pulmonary" in name_l:
        for keys, label, pts in [(("clinical_dvt",),
                                  "Clinical signs/symptoms of DVT", 3),
                                 (("pe_number_one",),
                                  "PE #1 diagnosis or equally likely", 3)]:
            hit, spec = _flag_any(p, *keys)
            items.append({"item": label, "hit": hit, "points": pts if hit else 0,
                          "specified": spec})
        for keys, label, pts in [(("heart_rate",), "Heart rate >= 100 bpm", 1.5),
                                 (("immobilization_for_3days",
                                   "surgery_in_past4weeks",),
                                  "Immobilization >=3d or surgery within 4wk", 1.5),
                                 (("previous_pe", "previous_dvt"),
                                  "Previous PE or DVT", 1.5),
                                 (("hemoptysis",), "Hemoptysis", 1),
                                 (("malignancy_with_treatment",),
                                  "Malignancy with treatment", 1)]:
            if keys == ("heart_rate",):
                try:
                    hr = _formulas._num(p.get("heart_rate"))
                    hit = hr >= 100
                    items.append({"item": label, "hit": hit,
                                  "points": pts if hit else 0, "value": hr})
                except (TypeError, ValueError):
                    pass
                continue
            hit, spec = _flag_any(p, *keys)
            items.append({"item": label, "hit": hit, "points": pts if hit else 0,
                          "specified": spec})
        return items

    if "child-pugh" in name_l or "child pugh" in name_l:
        specs = [("bilirubin", "Bilirubin total (mg/dL)",
                  lambda x: 1 if x < 2 else (2 if x <= 3 else 3)),
                 ("albumin", "Albumin (g/dL)",
                  lambda x: 1 if x > 3.5 else (2 if x >= 2.8 else 3)),
                 ("inr", "INR",
                  lambda x: 1 if x < 1.7 else (2 if x <= 2.3 else 3))]
        for key, label, fn_pts in specs:
            try:
                x = _formulas._num(p.get(key))
                items.append({"item": label, "hit": True, "points": fn_pts(x),
                              "value": x})
            except (TypeError, ValueError):
                pass
        for key, label, mapping in [
                ("ascites", "Ascites",
                 {"absent": 1, "none": 1, "slight": 2, "mild": 2,
                  "moderate": 3}),
                ("encephalopathy", "Encephalopathy",
                 {"absent": 1, "none": 1, "no": 1, "grade 1": 2, "grade 2": 2,
                  "grade 1-2": 2, "grade 3": 3, "grade 4": 3, "grade 3-4": 3})]:
            v = p.get(key)
            if v is None:
                continue
            pts = mapping.get(str(v).strip().lower())
            if pts is None:
                pts = 3 if any(g in str(v).lower() for g in ("3", "4")) else 2
            items.append({"item": label, "hit": True, "points": pts, "value": str(v)})
        return items or None

    if "perc" in name_l:
        rows = [(("unilateral_leg_swelling",), "Unilateral leg swelling", None),
                (("hemoptysis",), "Hemoptysis", None),
                (("recent_surgery_or_trauma",), "Surgery/trauma within 4wk", None),
                (("previous_pe", "previous_dvt"), "Previous PE/DVT", None),
                (("hormonal_use",), "Hormone use", None)]
        for keys, label, _ in rows:
            hit, spec = _flag_any(p, *keys)
            items.append({"item": label, "hit": hit, "points": 1 if hit else 0,
                          "specified": spec})
        try:
            age = _formulas._age_years(p.get("age"))
            items.append({"item": "Age >= 50", "hit": age >= 50,
                          "points": 1 if age >= 50 else 0, "value": age})
        except (TypeError, ValueError):
            pass
        for key, label, lo, hi in [("heart_rate", "Heart rate >= 100", 100, None),
                                   ("oxygen_sat", "O2 sat < 95%", None, 95)]:
            try:
                x = _formulas._num(p.get(key))
                hit = (x >= lo) if lo is not None else (x < hi)
                items.append({"item": label, "hit": hit, "points": 1 if hit else 0,
                              "value": x})
            except (TypeError, ValueError):
                pass
        return items

    if name_l.startswith("sirs"):
        try:
            t = _formulas._num(p.get("temperature"))
            hit = t >= 38 or t < 36
            items.append({"item": "Temperature >=38 or <36 C", "hit": hit,
                          "points": 1 if hit else 0, "value": t})
        except (TypeError, ValueError):
            pass
        try:
            hr = _formulas._num(p.get("heart_rate"))
            hit = hr > 90
            items.append({"item": "Heart rate > 90", "hit": hit,
                          "points": 1 if hit else 0, "value": hr})
        except (TypeError, ValueError):
            pass
        try:
            wbc = _formulas._num(p.get("wbc"))
            hit = wbc > 12 or wbc < 4
            items.append({"item": "WBC >12 or <4 x10^9/L", "hit": hit,
                          "points": 1 if hit else 0, "value": wbc})
        except (TypeError, ValueError):
            pass
        try:
            rr = _formulas._num(p.get("respiratory_rate"))
            paco2 = _formulas._num(p.get("paco2"))
            hit = rr > 20 or (paco2 is not None and paco2 < 32)
            items.append({"item": "RR >20 or PaCO2 <32 mmHg", "hit": hit,
                          "points": 1 if hit else 0,
                          "value": {"respiratory_rate": rr, "paco2": paco2}})
        except (TypeError, ValueError):
            pass
        return items or None

    return None


def _format_result(raw: Any, ot: str) -> Any:
    if ot == "date":
        d = _parse_date_any(raw)
        return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}" if d else str(raw)
    if isinstance(raw, str):
        x = None
        try:
            x = float(raw)
        except (TypeError, ValueError):
            return raw
        raw = x
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return raw
    if ot in ("integer", "score"):
        return int(round(float(raw)))
    if ot == "decimal":
        return f"{float(raw):.2f}"
    if isinstance(raw, int):
        return int(raw)
    return f"{float(raw):.2f}"


def _infer_output_type(raw: Any) -> str:
    if isinstance(raw, str):
        return "date" if _parse_date_any(raw) else "string"
    if isinstance(raw, bool):
        return "string"
    if isinstance(raw, int):
        return "integer"
    return "decimal"


_PARAM_ZH = {
    "creatinine": "血肌酐", "age": "年龄", "sex": "性别", "weight": "体重",
    "height": "身高", "sys_bp": "收缩压", "dia_bp": "舒张压",
    "heart_rate": "心率", "sodium": "血钠", "glucose": "血糖",
    "albumin": "白蛋白", "bilirubin": "总胆红素", "inr": "INR",
}


def _calculator_handler(args: Dict[str, Any], day: int) -> Dict[str, Any]:
    calc_name = str(args.get("calculator_name", args.get("formula", "")) or "").strip()
    params = args.get("parameters") or args.get("params") or {}
    ot_arg = str(args.get("output_type", "") or "").strip().lower()
    warnings = _physiology_warnings(params if isinstance(params, dict) else {})

    if calc_name and isinstance(params, dict):
        fn = _formulas.lookup(calc_name)
        if fn is not None:
            display = _formulas.official_display_name(calc_name) or calc_name
            try:
                val = fn(params)
                if isinstance(val, dict) and "Answer" in val:
                    val = val["Answer"]
            except Exception as exc:
                return {"error": f"formula {calc_name!r} failed: "
                                  f"{type(exc).__name__}: {exc}"}
            ot = ot_arg or _infer_output_type(val)
            out: Dict[str, Any] = {"result": _format_result(val, ot),
                                   "result_raw": val,
                                   "output_type": ot,
                                   "formula": display,
                                   "source": "MedCalc-Bench official"}
            steps = _steps_summary(display, params, val)
            if steps:
                out["steps"] = steps
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                breakdown = _score_breakdown(display, params)
                if breakdown:
                    out["item_breakdown"] = breakdown
                    out["item_breakdown_total"] = \
                        sum(float(it.get("points", 0)) for it in breakdown)
                    out["item_breakdown_note"] = ("per-item evidence and points; "
                                                  "unspecified items assumed "
                                                  "absent (official semantics)")
            if warnings:
                out["warnings"] = warnings
            return leak(out)
        if args.get("expression") in (None, ""):
            return {"error": f"formula {calc_name!r} not supported; "
                             f"provide 'expression' instead"}
    expr = str(args.get("expression", "") or "")
    if not expr.strip():
        return {"error": "calculator_name (with parameters) or expression required"}
    try:
        val = _safe_eval(ast.parse(expr, mode="eval"))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    ot = ot_arg or _infer_output_type(val)
    out = {"expression": expr, "result": _format_result(val, ot),
           "result_raw": val, "output_type": ot,
           "steps": f"expression evaluation: {expr} = {val}"}
    if warnings:
        out["warnings"] = warnings
    return leak(out)


def _steps_summary(display: str, params: Dict[str, Any], val: Any) -> str:
    try:
        entry = _formulas.OFFICIAL_REGISTRY.get(display)
        if entry is not None:
            import importlib
            import sys as _sys
            if str(_formulas.OFFICIAL_DIR) not in _sys.path:
                _sys.path.insert(0, str(_formulas.OFFICIAL_DIR))
            mod = importlib.import_module(entry["module"])
            fn = getattr(mod, entry["func"], None)
            if callable(fn):
                res = fn(_formulas._canonical_params(dict(params or {})))
                if isinstance(res, dict) and res.get("Explanation"):
                    text = re.sub(r"\s+", " ", str(res["Explanation"])).strip()
                    return text[:900] + ("..." if len(text) > 900 else "")
    except Exception:
        pass
    ptxt = "; ".join(f"{k}={v!r}" for k, v in sorted((params or {}).items()))
    return f"formula={display}; parameters: {ptxt}; raw result: {val}"


_CALCULATOR_SCHEMA = {
    "type": "function", "function": {
        "name": "calculator",
        "description": (
            "医学计算器：优先按 calculator_name 调用 MedCalc-Bench 官方逐公式实现，"
            "也可对纯数学表达式（+ - * / ** % 括号、sqrt/abs/log/log10/exp）求值。"
            "输出包含：公式名、中间步骤摘要（steps）、按 output_type 格式化的最终"
            "结果（decimal→2 位小数、integer→取整、date→YYYY-MM-DD）；评分类公式"
            "（CHA2DS2-VASc/Wells/Child-Pugh/PERC/SIRS 等）额外返回逐项分值明细"
            "（item_breakdown：每项证据命中与否+分值）；生理参数超合理范围时返回 "
            "warnings。用法一（推荐）：args: calculator_name, parameters（dict，"
            "键为参数名，值可为数值/布尔/字符串或 [数值, 单位] 对）, output_type"
            "（可选，decimal/integer/date）。"
            "已支持公式清单：" + _formulas.description_text() +
            " 用法二：args: expression（数学表达式字符串）。"),
        "parameters": {"type": "object", "properties": {
            "calculator_name": {"type": "string",
                                 "description": "公式名，见描述中的支持清单"},
            "parameters": {"type": "object",
                            "description": "公式参数（数值/布尔/[值,单位]对）"},
            "output_type": {"type": "string",
                             "description": "期望输出类型：decimal/integer/date"},
            "expression": {"type": "string",
                            "description": "回退用：纯数学表达式"}},
            "required": []}},
}


def load_cases(split: str) -> list:
    return load_manifest("medcalc", split)


def render_case(payload: dict, split: str) -> str:
    q = payload["question"].strip()
    note = payload["patient_note"].strip()
    ot = payload.get("output_type", "")
    unit_hint = ("Reply with the numeric result only"
                 if ot in ("decimal", "integer") else
                 "Reply with the date in YYYY-MM-DD format")
    return (f"[Medical Calculation]\n"
            f"Calculator: {payload['calculator_name']} (category: {payload['category']})\n\n"
            f"Patient Note:\n{note}\n\n"
            f"Question: {q}\n\n"
            f"Read the note carefully, extract the relevant entities, compute step by step "
            f"(you may use the calculator tool), and give the final answer. {unit_hint}.")


def _score_date(answer: str, gold: dict) -> dict:
    gold_ans = str(gold.get("answer", ""))
    if _is_gestational_gold(gold):
        gw = re.search(r"(\d+)\s*weeks?", gold_ans, re.I)
        gd = re.search(r"(\d+)\s*days?", gold_ans, re.I)
        gold_w = int(gw.group(1)) if gw else None
        gold_d = int(gd.group(1)) if gd else 0
        pred = _parse_gestational(answer)
        if pred is None or pred[0] is None:
            return {"reward": 0.0,
                    "error_hint": "gestational age not found in answer "
                                  "(expected '<N> weeks <M> days')"}
        pw, pd = pred
        ok = (pw == gold_w) and (gold_d == 0 if pd is None else pd == gold_d)
        return {"reward": 1.0 if ok else 0.0,
                "error_hint": None if ok else
                f"gestational age mismatch (gold={gold_w}w{gold_d}d, "
                f"pred={pw}w{pd}d)"}
    gdate = _parse_date_any(gold_ans)
    if gdate is None:
        gnorm = re.sub(r"[年月/.]", "-", gold_ans).rstrip("-")
        anorm = re.sub(r"[年月/.]", "-", answer).rstrip("-")
        ok = anorm.endswith(gnorm) or gnorm in anorm
        return {"reward": 1.0 if ok else 0.0,
                "error_hint": None if ok else "date mismatch (fallback)"}
    pdate = _last_date_in_text(answer)
    if pdate is None:
        return {"reward": 0.0, "error_hint": "no parseable date found in answer"}
    ok = pdate == gdate
    return {"reward": 1.0 if ok else 0.0,
            "error_hint": None if ok else
            f"date mismatch (gold={gdate[0]:04d}-{gdate[1]:02d}-{gdate[2]:02d}, "
            f"pred={pdate[0]:04d}-{pdate[1]:02d}-{pdate[2]:02d})"}


def score(answer: str, gold: dict) -> dict:
    if not gold:
        return {"reward": 0.0, "error_hint": "missing gold"}
    ot = gold.get("output_type", "decimal")
    text = (answer or "").strip()
    if ot == "date":
        return _score_date(text, gold)
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not nums:
        return {"reward": 0.0, "error_hint": "no number found in answer"}
    try:
        pred = float(nums[-1])
    except ValueError:
        return {"reward": 0.0, "error_hint": "unparsable number"}
    try:
        lo = float(gold.get("lower_limit"))
        hi = float(gold.get("upper_limit"))
    except (TypeError, ValueError):
        lo = hi = None
    if lo is not None and hi is not None:
        ok = lo <= pred <= hi
    else:
        try:
            gt = float(gold["answer"])
        except (TypeError, ValueError):
            gt = None
        ok = gt is not None and abs(pred - gt) <= max(0.01 * abs(gt), 1e-3)
    return {"reward": 1.0 if ok else 0.0,
            "error_hint": None if ok else
            f"numeric out of accepted range (pred={pred})"}


def get_toolbox(memory, client, include_builtin=True):
    return make_toolbox(memory, client,
                        schemas=[_CALCULATOR_SCHEMA],
                        handlers={"calculator": _calculator_handler},
                        include_builtin=include_builtin)
