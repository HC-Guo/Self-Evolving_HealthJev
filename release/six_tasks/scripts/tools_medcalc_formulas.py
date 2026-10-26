# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

HERE = Path(__file__).resolve().parent
E2_DIR = HERE.parent
OFFICIAL_DIR = Path(os.environ.get("HEALTHJEV_MEDCALC_FORMULAS_DIR")
                    or (E2_DIR / "data" / "candidate_datasets"
                        / "medcalc_bench" / "calculator_implementations")).expanduser()
if not OFFICIAL_DIR.is_dir():
    print(f"[medcalc-formulas] official MedCalc-Bench implementations NOT found "
          f"at {OFFICIAL_DIR}; supply the third-party calculator code (with its "
          f"LICENSE) there or set HEALTHJEV_MEDCALC_FORMULAS_DIR. Falling back to "
          f"the bundled handwritten layer / expression evaluator.",
          file=sys.stderr, flush=True)

def round_number(num):
    if num == 0:
        return 0
    if abs(num) >= 1e-4:
        return round(num, 5)
    from math import log10, floor
    sig_digits = -int(floor(log10(abs(num)))) + 5
    return round(num, sig_digits)


def _norm_name(name: str) -> str:
    s = str(name or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _pair(v: Any, default_unit: Optional[str] = None):
    if isinstance(v, (list, tuple)):
        if len(v) >= 2:
            return (float(v[0]), str(v[1]))
        if len(v) == 1:
            return (float(v[0]), default_unit)
    if isinstance(v, dict):
        val = v.get("value", v.get("amount", v.get("val")))
        unit = v.get("unit", v.get("units", default_unit))
        return (float(val), str(unit) if unit is not None else default_unit)
    return (float(v), default_unit)


def _num(v: Any, default: Optional[float] = None) -> float:
    if v is None:
        if default is None:
            raise ValueError("missing numeric parameter")
        return float(default)
    return float(_pair(v, None)[0])


def _flag(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("yes", "y", "true", "present", "1", "positive"):
        return True
    if s in ("no", "n", "false", "absent", "0", "negative", "none"):
        return False
    return bool(s)


def _sex(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("female", "f", "woman", "girl"):
        return "Female"
    if s in ("male", "m", "man", "boy"):
        return "Male"
    raise ValueError(f"unrecognized sex: {v!r}")


def _age_years(v: Any) -> float:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        val = float(v[0]); unit = str(v[1]).lower()
        if "month" in unit:
            return float(int(val) // 12)
        if "week" in unit:
            return float(int(val) // 52)
        if "day" in unit:
            return 0.0
        return val
    if isinstance(v, dict):
        val = float(v.get("value", v.get("amount")))
        unit = str(v.get("unit", "years")).lower()
        return _age_years([val, unit])
    return float(v)


def _to_kg(v: Any) -> float:
    val, unit = _pair(v, "kg")
    u = unit.lower()
    if u.startswith("lb") or u == "pound" or u == "pounds":
        return round_number(val * 0.453592)
    if u == "g" or u == "gram" or u == "grams":
        return val / 1000.0
    return val


def _to_m(v: Any) -> float:
    val, unit = _pair(v, "cm")
    u = unit.lower()
    if u == "m" or u == "meter" or u == "meters":
        return val
    if u == "cm":
        return round_number(val / 100.0)
    if u == "ft" or u == "feet" or u == "foot":
        return round_number(val * 0.3048)
    if u == "in" or u == "inch" or u == "inches":
        return round_number(val * 0.0254)
    raise ValueError(f"unrecognized height unit: {unit!r}")


def _to_cm(v: Any) -> float:
    val, unit = _pair(v, "cm")
    u = unit.lower()
    if u == "m":
        return round_number(val * 100.0)
    if u == "cm":
        return val
    if u == "ft" or u == "feet" or u == "foot":
        return round_number(val * 30.48)
    if u == "in" or u == "inch" or u == "inches":
        return round_number(val * 2.54)
    raise ValueError(f"unrecognized height unit: {unit!r}")


def _to_inches(v: Any) -> float:
    val, unit = _pair(v, "cm")
    u = unit.lower()
    if u == "m":
        return round_number(val * 39.3701)
    if u == "cm":
        return round_number(val * 0.393701)
    if u == "ft" or u == "feet" or u == "foot":
        return round_number(val * 12.0)
    if u == "in" or u == "inch" or u == "inches":
        return val
    raise ValueError(f"unrecognized height unit: {unit!r}")


def _concentration(v: Any, molar_mass: float, tgt: str,
                   valence: Optional[float] = None) -> float:
    val, unit = _pair(v)
    u = str(unit)
    if u == tgt:
        return val
    m_src, v_src = u.split("/")
    m_tgt, v_tgt = tgt.split("/")
    vol = {"L": 1.0, "dL": 0.1, "mL": 0.001, "µL": 1e-6,
           "mm^3": 1e-6, "cm^3": 0.001}
    v_src_c, v_tgt_c = v_src, v_tgt
    for known in vol:
        if v_src.lower() == known.lower():
            v_src_c = known
        if v_tgt.lower() == known.lower():
            v_tgt_c = known
    mol_factors = {"mol": 1.0, "mmol": 1e-3, "µmol": 1e-6, "pmol": 1e-9}
    g_factors = {"kg": 1000.0, "g": 1.0, "mg": 1e-3, "µg": 1e-6}
    ms = m_src
    ms_known = None
    for known in list(mol_factors) + list(g_factors) + ["mEq"]:
        if ms.lower() == known.lower():
            ms_known = known
            break
    if ms_known is None:
        raise ValueError(f"unrecognized mass unit: {m_src!r}")
    if ms_known in mol_factors:
        mols = val * mol_factors[ms_known]
    elif ms_known == "mEq":
        v_ = valence if valence is not None else 1.0
        mols = val / v_ * 1e-3
    else:
        grams = val * g_factors[ms_known]
        mols = grams / molar_mass
    if m_tgt in mol_factors:
        out = mols / mol_factors[m_tgt]
    elif m_tgt in g_factors:
        out = mols * molar_mass / g_factors[m_tgt]
    elif m_tgt == "mEq":
        v_ = valence if valence is not None else 1.0
        out = mols / 1e-3 * v_
    else:
        raise ValueError(f"unrecognized target mass unit: {m_tgt!r}")
    return round_number(out * vol[v_tgt_c] / vol[v_src_c])


def _to_mgdl(v: Any, molar_mass: float, valence: Optional[float] = None) -> float:
    return _concentration(v, molar_mass, "mg/dL", valence)


_PARAM_ALIASES_RAW = {
    "systolic blood pressure": "sys_bp", "systolic bp": "sys_bp",
    "diastolic blood pressure": "dia_bp", "diastolic bp": "dia_bp",
    "heart rate or pulse": "heart_rate", "heart rate": "heart_rate",
    "pulse": "heart_rate",
    "respiratory rate": "respiratory_rate",
    "temperature": "temperature",
    "o saturation percentage": "oxygen_sat",
    "o2 saturation percentage": "oxygen_sat", "oxygen saturation": "oxygen_sat",
    "paco2": "paco2", "pa co2": "paco2",
    "sodium": "sodium", "chloride": "chloride", "bicarbonate": "bicarbonate",
    "hco3": "bicarbonate", "serum creatinine": "creatinine",
    "creatinine": "creatinine", "albumin": "albumin", "calcium": "calcium",
    "blood urea nitrogen bun": "bun", "bun": "bun",
    "serum glucose": "glucose", "blood glucose": "glucose", "glucose": "glucose",
    "white blood cell count": "wbc", "wbc": "wbc",
    "aspartate aminotransferase": "ast", "ast": "ast",
    "alanine aminotransferase": "alt", "alt": "alt",
    "platelet count": "platelet_count", "platelets": "platelet_count",
    "total cholesterol": "total_cholesterol",
    "high density lipoprotein cholesterol": "hdl_cholesterol",
    "hdl cholesterol": "hdl_cholesterol", "hdl": "hdl_cholesterol",
    "triglycerides": "triglycerides", "insulin": "insulin",
    "race": "race",
    "weight": "weight", "height": "height",
    "body mass index bmi": "body_mass_index", "bmi": "body_mass_index",
    "last menstrual period": "menstrual_date", "lmp": "menstrual_date",
    "cycle length": "cycle_length",
    "clinical signs and symptoms of dvt": "clinical_dvt",
    "pe is 1 diagnosis or equally likely": "pe_number_one",
    "pulmonary embolism is 1 diagnosis or equally likely": "pe_number_one",
    "immobilization for at least 3 days": "immobilization_for_3days",
    "immobilization for 3 days": "immobilization_for_3days",
    "surgery in the previous 4 weeks": "surgery_in_past4weeks",
    "surgery in past 4 weeks": "surgery_in_past4weeks",
    "recent surgery or trauma": "recent_surgery_or_trauma",
    "previously documented pulmonary embolism": "previous_pe",
    "previous pe": "previous_pe", "prior pulmonary embolism": "previous_pe",
    "previously documented deep vein thrombosis": "previous_dvt",
    "previous dvt": "previous_dvt", "prior deep vein thrombosis": "previous_dvt",
    "hemoptysis": "hemoptysis",
    "malignancy with treatment within 6 months or palliative":
        "malignancy_with_treatment",
    "malignancy with treatment": "malignancy_with_treatment",
    "unilateral leg swelling": "unilateral_leg_swelling",
    "hormone use": "hormonal_use", "hormonal use": "hormonal_use",
    "congestive heart failure": "chf", "chf": "chf",
    "hypertension history": "hypertension", "hypertension": "hypertension",
    "stroke history": "stroke", "stroke": "stroke",
    "transient ischemic attacks history": "tia", "tia history": "tia",
    "tia": "tia",
    "thromboembolism history": "thromboembolism",
    "thromboembolism": "thromboembolism",
    "vascular disease history": "vascular_disease",
    "vascular disease": "vascular_disease",
    "diabetes mellitus": "diabetes_mellitus", "diabetes history": "diabetes",
    "diabetes": "diabetes",
    "suspicion history": "history", "history": "history",
    "electrocardiogram test": "electrocardiogram", "ekg": "electrocardiogram",
    "electrocardiogram": "electrocardiogram",
    "initial troponin": "initial_troponin",
    "parent or sibling with cardiovascular disease before age 65":
        "family_with_cvd",
    "atherosclerotic disease": "atherosclerotic_disease",
    "qt interval": "qt_interval",
    "confusion": "confusion",
    "cough absent": "cough_absent",
    "tender swollen anterior cervical lymph nodes": "tender_lymph_nodes",
    "exudate or swelling on tonsils": "exudate_swelling_tonsils",
    "purulent tonsils": "purulent_tonsils",
    "symptom onset 3 days": "symptom_onset",
    "symptom onset 3 days or less": "symptom_onset",
    "severe tonsil inflammation": "severe_tonsil_inflammation",
    "fever in past 24 hours": "fever_24_hours",
    "fever 24 hours": "fever_24_hours",
    "absence of cough or coryza": "cough_coryza_absent",
    "cough coryza absent": "cough_coryza_absent",
}

_PARAM_ALIASES = {_norm_name(k): v for k, v in _PARAM_ALIASES_RAW.items()}


def _canonical_params(params: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in (params or {}).items():
        out[_PARAM_ALIASES.get(_norm_name(k), k)] = v
    return out


OFFICIAL_REGISTRY: Dict[str, Dict[str, str]] = {
    "APACHE II Score": {"module": "apache_ii", "func": "apache_ii_explanation"},
    "Adjusted Body Weight": {"module": "adjusted_body_weight", "func": "abw_explanation"},
    "Albumin Corrected Anion Gap": {"module": "albumin_corrected_anion", "func": "compute_albumin_corrected_anion_explanation"},
    "Albumin Corrected Delta Gap": {"module": "albumin_corrected_delta_gap", "func": "compute_albumin_corrected_delta_gap_explanation"},
    "Albumin Corrected Delta Ratio": {"module": "albumin_delta_ratio", "func": "compute_albumin_delta_ratio_explanation"},
    "Anion Gap": {"module": "anion_gap", "func": "compute_anion_gap_explanation"},
    "Body Mass Index (BMI)": {"module": "bmi_calculator", "func": "bmi_calculator_explanation"},
    "Body Surface Area Calculator": {"module": "bsa_calculator", "func": "bsa_calculator_explaination"},
    "CHA2DS2-VASc Score for Atrial Fibrillation Stroke Risk": {"module": "cha2ds2_vasc_score", "func": "generate_cha2ds2_vasc_explanation"},
    "CKD-EPI Equations for Glomerular Filtration Rate": {"module": "ckd-epi_2021_creatinine", "func": "ckd_epi_2021_explanation"},
    "CURB-65 Score for Pneumonia Severity": {"module": "curb_65", "func": "curb_65_explanation"},
    "Calcium Correction for Hypoalbuminemia": {"module": "calcium_correction", "func": "calculate_corrected_calcium_explanation"},
    "Caprini Score for Venous Thromboembolism (2005)": {"module": "caprini_score", "func": "caprini_score_explanation"},
    "Centor Score (Modified/McIsaac) for Strep Pharyngitis": {"module": "centor_score", "func": "compute_centor_score_explanation"},
    "Charlson Comorbidity Index (CCI)": {"module": "cci", "func": "compute_cci_explanation"},
    "Child-Pugh Score for Cirrhosis Mortality": {"module": "child_pugh_score", "func": "compute_child_pugh_score_explanation"},
    "Creatinine Clearance (Cockcroft-Gault Equation)": {"module": "creatinine_clearance", "func": "generate_cockcroft_gault_explanation"},
    "Delta Gap": {"module": "delta_gap", "func": "compute_delta_gap_explanation"},
    "Delta Ratio": {"module": "delta_ratio", "func": "compute_delta_ratio_explanation"},
    "Estimated Due Date": {"module": "estimated_due_date", "func": "add_40_weeks_explanation"},
    "Estimated Gestational Age": {"module": "estimated_gestational_age", "func": "compute_gestational_age_explanation"},
    "Estimated of Conception": {"module": "estimated_conception_date", "func": "add_2_weeks_explanation"},
    "FeverPAIN Score for Strep Pharyngitis": {"module": "feverpain", "func": "compute_fever_pain_explanation"},
    "Fibrosis-4 (FIB-4) Index for Liver Fibrosis": {"module": "fibrosis_4", "func": "compute_fib4_explanation"},
    "Fractional Excretion of Sodium (FENa)": {"module": "compute_fena", "func": "compute_fena_explanation"},
    "Framingham Risk Score for Hard Coronary Heart Disease": {"module": "framingham_risk_score", "func": "framingham_risk_score_explanation"},
    "Free Water Deficit": {"module": "free_water_deficit", "func": "free_water_deficit_explanation"},
    "Glasgow Coma Score (GCS)": {"module": "glasgow_coma_score", "func": "compute_glasgow_coma_score_explanation"},
    "Glasgow-Blatchford Bleeding Score (GBS)": {"module": "glasgow_bleeding_score", "func": "glasgow_bleeding_score_explanation"},
    "HAS-BLED Score for Major Bleeding Risk": {"module": "has_bled_score", "func": "compute_has_bled_score_explanation"},
    "HEART Score for Major Cardiac Events": {"module": "heart_score", "func": "compute_heart_score_explanation"},
    "HOMA-IR (Homeostatic Model Assessment for Insulin Resistance)": {"module": "homa_ir", "func": "compute_homa_ir_explanation"},
    "Ideal Body Weight": {"module": "ideal_body_weight", "func": "ibw_explanation"},
    "LDL Calculated": {"module": "ldl_calculated", "func": "compute_ldl_explanation"},
    "MDRD GFR Equation": {"module": "mdrd_gfr", "func": "mrdr_gfr_explanation"},
    "MELD Na (UNOS/OPTN)": {"module": "meldna", "func": "compute_meldna_explanation"},
    "Maintenance Fluids Calculations": {"module": "maintenance_fluid_calc", "func": "maintenance_fluid_explanation"},
    "Mean Arterial Pressure (MAP)": {"module": "mean_arterial_pressure", "func": "mean_arterial_pressure_explanation"},
    "Morphine Milligram Equivalents (MME) Calculator": {"module": "mme", "func": "mme_explanation"},
    "PERC Rule for Pulmonary Embolism": {"module": "perc_rule", "func": "compute_perc_rule_explanation"},
    "PSI Score: Pneumonia Severity Index for CAP": {"module": "psi_score", "func": "psi_score_explanation"},
    "QTc Bazett Calculator": {"module": "qt_calculator_bazett", "func": "bazett_calculator_explanation"},
    "QTc Framingham Calculator": {"module": "qt_calculator_framingham", "func": "framingham_calculator_explanation"},
    "QTc Fridericia Calculator": {"module": "qt_calculator_fredericia", "func": "fredericia_calculator_explanation"},
    "QTc Hodges Calculator": {"module": "qt_calculator_hodges", "func": "hodges_calculator_explanation"},
    "QTc Rautaharju Calculator": {"module": "qt_calculator_rautaharju", "func": "rautaharju_calculator_explanation"},
    "Revised Cardiac Risk Index for Pre-Operative Risk": {"module": "cardiac_risk_index", "func": "compute_cardiac_index_explanation"},
    "SIRS Criteria": {"module": "sirs_criteria", "func": "sirs_criteria_explanation"},
    "Sequential Organ Failure Assessment (SOFA) Score": {"module": "sofa", "func": "compute_sofa_explanation"},
    "Serum Osmolality": {"module": "sOsm", "func": "compute_serum_osmolality_explanation"},
    "Sodium Correction for Hyperglycemia": {"module": "sodium_correction_hyperglycemia", "func": "compute_sodium_correction_hyperglycemia_explanation"},
    "Steroid Conversion Calculator": {"module": "steroid_conversion_calculator", "func": "compute_steroid_conversion_explanation"},
    "Target weight": {"module": "target_weight", "func": "targetweight_explanation"},
    "Wells' Criteria for DVT": {"module": "wells_criteria_dvt", "func": "compute_wells_criteria_dvt_explanation"},
    "Wells' Criteria for Pulmonary Embolism": {"module": "wells_criteria_pe", "func": "calculate_pe_wells_explanation"},
}

_NORM2OFFICIAL: Dict[str, Dict[str, str]] = {}
for _disp, _e in OFFICIAL_REGISTRY.items():
    _e2 = dict(_e)
    _e2["__display__"] = _disp
    _NORM2OFFICIAL[_norm_name(_disp)] = _e2

for _disp, _e in list(OFFICIAL_REGISTRY.items()):
    _short = _norm_name(re.sub(r"\([^)]*\)", "", _disp))
    if _short and _short not in _NORM2OFFICIAL:
        _e2 = dict(_e)
        _e2["__display__"] = _disp
        _NORM2OFFICIAL[_short] = _e2

_official_cache: Dict[str, Optional[Callable]] = {}


def _official_fn(display_name: str) -> Optional[Callable]:
    if display_name in _official_cache:
        return _official_cache[display_name]
    fn: Optional[Callable] = None
    entry = OFFICIAL_REGISTRY.get(display_name)
    if entry is not None:
        try:
            if str(OFFICIAL_DIR) not in sys.path:
                sys.path.insert(0, str(OFFICIAL_DIR))
            mod = importlib.import_module(entry["module"])
            cand = getattr(mod, entry["func"], None)
            if callable(cand):
                fn = cand
        except Exception:
            fn = None
    _official_cache[display_name] = fn
    return fn


def _hw_mean_arterial_pressure(p: Dict[str, Any]):
    sys_bp = _num(p.get("sys_bp"))
    dia_bp = _num(p.get("dia_bp"))
    return round_number(sys_bp / 3 + 2 * dia_bp / 3)


def _hw_bsa(p: Dict[str, Any]):
    height = _to_cm(p.get("height"))
    weight = _to_kg(p.get("weight"))
    return round_number(math.sqrt(weight * height / 3600.0))


def _hw_maintenance_fluids(p: Dict[str, Any]):
    weight = _to_kg(p.get("weight"))
    if weight < 10:
        return round_number(weight * 4)
    if 10 <= weight <= 20:
        return round_number(40 + 2 * (weight - 10))
    return round_number(60 + (weight - 20))


def _hw_cha2ds2_vasc(p: Dict[str, Any]):
    age = _age_years(p.get("age"))
    score = 0
    if age >= 75:
        score += 2
    elif age >= 65:
        score += 1
    if _sex(p.get("sex")) == "Female":
        score += 1
    if _flag(p.get("chf")):
        score += 1
    if _flag(p.get("hypertension")):
        score += 1
    if (_flag(p.get("stroke")) or _flag(p.get("tia"))
            or _flag(p.get("thromboembolism"))):
        score += 2
    if _flag(p.get("vascular_disease")):
        score += 1
    if _flag(p.get("diabetes")) or _flag(p.get("diabetes_mellitus")):
        score += 1
    return score


def _hw_ibw(p: Dict[str, Any]):
    height = _to_inches(p.get("height"))
    gender = _sex(p.get("sex"))
    if gender == "Male":
        return round_number(50 + 2.3 * (height - 60))
    return round_number(45.5 + 2.3 * (height - 60))


def _hw_bmi(p: Dict[str, Any]):
    height = _to_m(p.get("height"))
    weight = _to_kg(p.get("weight"))
    return round_number(weight / (height * height))


def _hw_ckd_epi_2021(p: Dict[str, Any]):
    age = _age_years(p.get("age"))
    gender = _sex(p.get("sex"))
    creatinine_val = _to_mgdl(p.get("creatinine"), 113.12)
    gender_coefficient = 1.012 if gender == "Female" else 1.000
    if creatinine_val <= 0.7 and gender == "Female":
        a, b = 0.7, -0.241
    elif creatinine_val <= 0.9 and gender == "Male":
        a, b = 0.7, -0.302
    elif creatinine_val > 0.7 and gender == "Female":
        a, b = 0.7, -1.2
    elif creatinine_val > 0.9 and gender == "Male":
        a, b = 0.9, -1.2
    else:
        a, b = 0.9, -1.2
    return round_number(142 * (creatinine_val / a) ** b * 0.9938 ** age
                        * gender_coefficient)


def _hw_wells_pe(p: Dict[str, Any]):
    score = 0
    if _flag(p.get("clinical_dvt")):
        score += 3
    if _flag(p.get("pe_number_one")):
        score += 3
    if _num(p.get("heart_rate")) > 100:
        score += 1.5
    if (_flag(p.get("immobilization_for_3days"))
            or _flag(p.get("surgery_in_past4weeks"))):
        score += 1.5
    if _flag(p.get("previous_pe")) or _flag(p.get("previous_dvt")):
        score += 1.5
    if _flag(p.get("hemoptysis")):
        score += 1
    if _flag(p.get("malignancy_with_treatment")):
        score += 1
    return score


def _hw_serum_osmolality(p: Dict[str, Any]):
    sodium = _concentration(p.get("sodium"), 22.99, "mmol/L", valence=1)
    bun = _to_mgdl(p.get("bun"), 28.02)
    glucose = _to_mgdl(p.get("glucose"), 180.16)
    return round_number(2 * sodium + (bun / 2.8) + (glucose / 18))


def _hw_adjusted_body_weight(p: Dict[str, Any]):
    weight = _to_kg(p.get("weight"))
    ibw = _hw_ibw(p)
    return round_number(ibw + 0.4 * (weight - ibw))


def _hw_mdrd_gfr(p: Dict[str, Any]):
    age = _age_years(p.get("age"))
    creatinine_conc = _to_mgdl(p.get("creatinine"), 113.12)
    race_coefficient = 1.212 if str(p.get("race", "")).lower() == "black" else 1
    gender_coefficient = 0.742 if _sex(p.get("sex")) == "Female" else 1
    return round_number(175 * math.exp(math.log(creatinine_conc) * -1.154)
                        * math.exp(math.log(age) * -0.203)
                        * race_coefficient * gender_coefficient)


def _anion_gap_val(p: Dict[str, Any]) -> float:
    sodium = _concentration(p.get("sodium"), 22.99, "mEq/L", valence=1)
    chloride = _concentration(p.get("chloride"), 35.45, "mEq/L", valence=1)
    bicarbonate = _concentration(p.get("bicarbonate"), 61.02, "mEq/L", valence=1)
    return round_number(sodium - (chloride + bicarbonate))


def _hw_delta_gap(p: Dict[str, Any]):
    return round_number(_anion_gap_val(p) - 12.0)


def _hw_delta_ratio(p: Dict[str, Any]):
    bicarbonate = _concentration(p.get("bicarbonate"), 61.02, "mEq/L", valence=1)
    delta_gap_val = _hw_delta_gap(p)
    return round_number(delta_gap_val / (24 - bicarbonate))


def _hw_free_water_deficit(p: Dict[str, Any]):
    age = _age_years(p.get("age"))
    gender = _sex(p.get("sex"))
    if 0 <= age < 18:
        tbw = 0.6
    elif 18 <= age < 65 and gender == "Male":
        tbw = 0.6
    elif 18 <= age < 65 and gender == "Female":
        tbw = 0.5
    elif age >= 65 and gender == "Male":
        tbw = 0.5
    else:
        tbw = 0.45
    weight = _to_kg(p.get("weight"))
    sodium = _concentration(p.get("sodium"), 22.99, "mmol/L", valence=1)
    return round_number(tbw * weight * (sodium / 140 - 1))


def _hw_sodium_correction(p: Dict[str, Any]):
    sodium = _concentration(p.get("sodium"), 22.99, "mEq/L", valence=1)
    glucose = _to_mgdl(p.get("glucose"), 180.16)
    return round_number(sodium + 0.024 * (glucose - 100))


def _hw_sirs(p: Dict[str, Any]):
    criteria = 0
    temp, unit = _pair(p.get("temperature"), "degrees celsius")
    temperature = temp if "celsius" in unit.lower() else \
        round_number((temp - 32) * 5 / 9)
    if temperature > 38 or temperature < 36:
        criteria += 1
    if _num(p.get("heart_rate")) > 90:
        criteria += 1
    wbc = _num(p.get("wbc"))
    if wbc > 12000 or wbc < 4000:
        criteria += 1
    resp_met = False
    if p.get("respiratory_rate") is not None:
        resp_met = _num(p.get("respiratory_rate")) > 20
    paco2_met = False
    if p.get("paco2") is not None:
        paco2_met = _num(p.get("paco2")) < 32
    if resp_met or paco2_met:
        criteria += 1
    return criteria


def _hw_calcium_correction(p: Dict[str, Any]):
    normal_albumin = 4.0
    albumin_gdl = _concentration(p.get("albumin"), 66500, "g/dL")
    calcium = _to_mgdl(p.get("calcium"), 40.08, valence=2)
    return round_number(0.8 * (normal_albumin - albumin_gdl) + calcium)


def _hw_heart_score(p: Dict[str, Any]):
    total = 0
    history = str(p.get("history", "Slightly suspicious"))
    total += {"slightly suspicious": 0, "moderately suspicious": 1,
              "highly suspicious": 2}.get(history.lower(), 0)
    ekg = str(p.get("electrocardiogram", "Normal"))
    total += {"normal": 0,
              "non-specific repolarization disturbance": 1,
              "non specific repolarization disturbance": 1,
              "significant st deviation": 2}.get(ekg.lower(), 0)
    age = _age_years(p.get("age"))
    if age < 45:
        pass
    elif age < 65:
        total += 1
    else:
        total += 2
    factors = ["hypertension", "hypercholesterolemia", "diabetes_mellitus",
               "diabetes", "obesity", "smoking", "family_with_cvd",
               "atherosclerotic_disease"]
    count = sum(1 for f in factors if f in p and _flag(p.get(f)))
    if count == 0:
        pass
    elif count <= 2:
        total += 1
    else:
        total += 2
    troponin = str(p.get("initial_troponin",
                         "less than or equal to normal limit")).lower()
    total += {"less than or equal to normal limit": 0,
              "between the normal limit or up to three times the normal limit": 1,
              "greater than three times normal limit": 2}.get(troponin, 0)
    return total


def _hw_cockcroft_gault(p: Dict[str, Any]):
    weight = _to_kg(p.get("weight"))
    height_m = _to_m(p.get("height"))
    age = _age_years(p.get("age"))
    is_male = _sex(p.get("sex")) == "Male"
    serum_creatinine = _to_mgdl(p.get("creatinine"), 113.12)
    bmi = round_number(weight / (height_m * height_m))
    if bmi < 18.5:
        adjusted_weight = weight
    elif bmi <= 24.9:
        adjusted_weight = min(_hw_ibw(p), weight)
    else:
        adjusted_weight = _hw_adjusted_body_weight(p)
    constant = 1 if is_male else 0.85
    return round_number(((140 - age) * adjusted_weight * constant)
                        / (serum_creatinine * 72))


def _hw_perc_rule(p: Dict[str, Any]):
    count = 0
    if _age_years(p.get("age")) >= 50:
        count += 1
    if _num(p.get("heart_rate")) >= 100:
        count += 1
    if _num(p.get("oxygen_sat")) < 95:
        count += 1
    if _flag(p.get("unilateral_leg_swelling")):
        count += 1
    if _flag(p.get("hemoptysis")):
        count += 1
    if _flag(p.get("recent_surgery_or_trauma")):
        count += 1
    if _flag(p.get("previous_pe")) or _flag(p.get("previous_dvt")):
        count += 1
    if _flag(p.get("hormonal_use")):
        count += 1
    return count


HANDWRITTEN_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _reg(display_name: str, params: str, fn: Callable) -> None:
    HANDWRITTEN_REGISTRY[_norm_name(display_name)] = {
        "name": display_name, "params": params, "fn": fn}


_reg("Mean Arterial Pressure (MAP)",
     "sys_bp(mmHg), dia_bp(mmHg)", _hw_mean_arterial_pressure)
_reg("Body Surface Area Calculator",
     "weight(kg), height(cm)", _hw_bsa)
_reg("Maintenance Fluids Calculations",
     "weight(kg)", _hw_maintenance_fluids)
_reg("CHA2DS2-VASc Score for Atrial Fibrillation Stroke Risk",
     "age(years), sex, chf, hypertension, stroke, tia, thromboembolism, "
     "vascular_disease, diabetes", _hw_cha2ds2_vasc)
_reg("Ideal Body Weight", "sex, height", _hw_ibw)
_reg("Body Mass Index (BMI)", "weight(kg), height(m)", _hw_bmi)
_reg("CKD-EPI Equations for Glomerular Filtration Rate",
     "age(years), sex, creatinine(mg/dL)", _hw_ckd_epi_2021)
_reg("Wells' Criteria for Pulmonary Embolism",
     "heart_rate, clinical_dvt, pe_number_one, immobilization_for_3days, "
     "surgery_in_past4weeks, previous_pe, previous_dvt, hemoptysis, "
     "malignancy_with_treatment", _hw_wells_pe)
_reg("Serum Osmolality",
     "sodium(mmol/L), bun(mg/dL), glucose(mg/dL)", _hw_serum_osmolality)
_reg("Adjusted Body Weight", "sex, height, weight(kg)", _hw_adjusted_body_weight)
_reg("MDRD GFR Equation",
     "sex, age(years), creatinine(mg/dL), race", _hw_mdrd_gfr)
_reg("Delta Ratio",
     "sodium, chloride, bicarbonate (mEq/L)", _hw_delta_ratio)
_reg("Free Water Deficit",
     "age(years), sex, weight(kg), sodium(mmol/L)", _hw_free_water_deficit)
_reg("Sodium Correction for Hyperglycemia",
     "sodium(mEq/L), glucose(mg/dL)", _hw_sodium_correction)
_reg("SIRS Criteria",
     "temperature, heart_rate, wbc, respiratory_rate, paco2", _hw_sirs)
_reg("Delta Gap", "sodium, chloride, bicarbonate (mEq/L)", _hw_delta_gap)
_reg("Calcium Correction for Hypoalbuminemia",
     "albumin(g/dL), calcium(mg/dL)", _hw_calcium_correction)
_reg("HEART Score for Major Cardiac Events",
     "history, electrocardiogram, age(years), risk factors "
     "(hypertension/hypercholesterolemia/diabetes_mellitus/obesity/smoking/"
     "family_with_cvd/atherosclerotic_disease), initial_troponin",
     _hw_heart_score)
_reg("Creatinine Clearance (Cockcroft-Gault Equation)",
     "sex, age(years), weight(kg), height, creatinine(mg/dL)",
     _hw_cockcroft_gault)
_reg("PERC Rule for Pulmonary Embolism",
     "age(years), heart_rate, oxygen_sat(%), unilateral_leg_swelling, "
     "hemoptysis, recent_surgery_or_trauma, previous_pe, previous_dvt, "
     "hormonal_use", _hw_perc_rule)

HANDWRITTEN_TOP20 = [v["name"] for v in HANDWRITTEN_REGISTRY.values()]


def _wrap_official(fn: Callable) -> Callable:
    def call(params: Dict[str, Any]):
        res = fn(_canonical_params(dict(params or {})))
        if isinstance(res, dict) and "Answer" in res:
            return res["Answer"]
        return res
    return call


def _wrap_hw(fn: Callable) -> Callable:
    def call(params: Dict[str, Any]):
        return fn(_canonical_params(dict(params or {})))
    return call


def lookup(calculator_name: str) -> Optional[Callable]:
    key = _norm_name(calculator_name)
    if not key:
        return None
    entry = _NORM2OFFICIAL.get(key)
    if entry is not None:
        fn = _official_fn(entry["__display__"])
        if fn is not None:
            return _wrap_official(fn)
    hw = HANDWRITTEN_REGISTRY.get(key)
    if hw is not None:
        return _wrap_hw(hw["fn"])
    return None


def official_display_name(calculator_name: str) -> Optional[str]:
    entry = _NORM2OFFICIAL.get(_norm_name(calculator_name))
    return entry["__display__"] if entry else None


def implementation_source(calculator_name: str) -> str:
    key = _norm_name(calculator_name)
    entry = _NORM2OFFICIAL.get(key)
    if entry is not None and _official_fn(entry["__display__"]) is not None:
        return "official"
    if key in HANDWRITTEN_REGISTRY:
        return "handwritten"
    return "none"


def supported_formulas() -> list:
    return list(OFFICIAL_REGISTRY.keys())


def description_text() -> str:
    top = "; ".join(f"{v['name']}: {v['params']}"
                    for v in HANDWRITTEN_REGISTRY.values())
    others = [n for n in OFFICIAL_REGISTRY
              if _norm_name(n) not in HANDWRITTEN_REGISTRY]
    return ("Supported formulas (MedCalc-Bench official, "
            f"{len(OFFICIAL_REGISTRY)} total): "
            + ", ".join(OFFICIAL_REGISTRY)
            + ". Top-frequency formulas and parameters: " + top + ". "
            + "Other formulas: " + ", ".join(others) + ". "
            + "Concentration parameters may be given as [value, unit] pairs "
              "(e.g. [1.4, 'mg/dL']); flags accept true/false or "
              "present/absent; dates use MM/DD/YYYY; sex is Male/Female.")
