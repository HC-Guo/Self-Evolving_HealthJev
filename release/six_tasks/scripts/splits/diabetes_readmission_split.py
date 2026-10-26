# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_split import (SEED, require_exists, source_dir, stratified_group_split,
                          write_manifest)

USECOLS = ["encounter_id", "patient_nbr", "race", "gender", "age",
           "admission_type_id", "discharge_disposition_id",
           "time_in_hospital", "num_lab_procedures", "num_procedures",
           "num_medications", "number_diagnoses", "diag_1", "diag_2",
           "diag_3", "max_glu_serum", "A1Cresult", "insulin", "change",
           "diabetesMed", "readmitted"]

N_ACCUM, N_TEST = 500, 200
LABEL = {"NO": 0.0, ">30": 0.5, "<30": 1.0}


def _icd_group(code: str) -> str:
    c = str(code).strip().upper()
    if not c or c == "?":
        return "unknown"
    if c[0] == "E":
        return "E-code"
    if c[0] == "V":
        return "V-code"
    try:
        n = int(float(c))
    except ValueError:
        return "unknown"
    bounds = [(139, "infectious"), (239, "neoplasms"), (279, "endocrine"),
              (289, "blood"), (319, "mental"), (389, "nervous"),
              (459, "circulatory"), (519, "respiratory"), (579, "digestive"),
              (629, "genitourinary"), (679, "pregnancy"), (709, "skin"),
              (739, "musculoskeletal"), (759, "congenital"), (779, "perinatal"),
              (799, "symptoms"), (9999, "injury")]
    for hi, name in bounds:
        if n <= hi:
            return name
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description="diabetes_readmission 切分")
    ap.add_argument("--src", default=None,
                    help="数据集源目录（默认 $HEALTHJEV_BIOMEDICAL_DATA_DIR/"
                         "diabetes_readmission，或 env HEALTHJEV_DIABETES_READMISSION_SRC）")
    args = ap.parse_args()
    src = source_dir(args.src, "HEALTHJEV_DIABETES_READMISSION_SRC",
                     "diabetes_readmission")
    csv_path = require_exists(
        src / "github.com" / "lugooe" / "Diabetes.git" / "diabetic_data.csv",
        "diabetic_data.csv (diabetes 130-US dataset)")
    df = pd.read_csv(csv_path, usecols=USECOLS)
    print("total encounters:", len(df),
          "unique patients:", df["patient_nbr"].nunique())
    rows = []
    for r in df.itertuples(index=False):
        rows.append({
            "case_id": f"dia-enc-{int(r.encounter_id)}",
            "payload": {
                "patient_nbr": str(r.patient_nbr),
                "encounter_id": int(r.encounter_id),
                "age_bracket": str(r.age),
                "gender": str(r.gender),
                "race": str(r.race),
                "admission_type_id": int(r.admission_type_id),
                "discharge_disposition_id": int(r.discharge_disposition_id),
                "time_in_hospital_days": int(r.time_in_hospital),
                "num_lab_procedures": int(r.num_lab_procedures),
                "num_procedures": int(r.num_procedures),
                "num_medications": int(r.num_medications),
                "number_diagnoses": int(r.number_diagnoses),
                "diagnoses": {"diag_1": str(r.diag_1), "diag_2": str(r.diag_2),
                              "diag_3": str(r.diag_3)},
                "labs_summary": {
                    "max_glu_serum": str(r.max_glu_serum),
                    "A1Cresult": str(r.A1Cresult),
                    "insulin_dose_change": str(r.insulin),
                    "medication_change": str(r.change),
                    "diabetesMed_prescribed": str(r.diabetesMed),
                },
            },
            "gold": {"readmitted": str(r.readmitted),
                     "label": LABEL[str(r.readmitted)]},
            "_iso_field": "patient_nbr",
            "_subject": str(r.patient_nbr),
            "_layer": str(r.readmitted),
        })
    print("readmitted dist:", df["readmitted"].value_counts().to_dict())

    accum, test = stratified_group_split(rows, group_key="_subject",
                                         n_accum=N_ACCUM, n_test=N_TEST,
                                         strat_key="_layer", seed=SEED)
    for r in accum + test:
        r.pop("_subject", None)
        r.pop("_layer", None)
    write_manifest("diabetes_readmission", accum, test)


if __name__ == "__main__":
    main()
