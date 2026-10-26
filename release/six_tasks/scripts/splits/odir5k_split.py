# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_split import (DATA_DIR, SEED, require_exists, source_dir,
                          stratified_group_split, write_manifest)

N_ACCUM, N_TEST = 500, 200


def main():
    ap = argparse.ArgumentParser(description="odir5k 切分")
    ap.add_argument("--src", default=None,
                    help="数据集源目录（默认 $HEALTHJEV_BIOMEDICAL_DATA_DIR/odir5k，"
                         "或 env HEALTHJEV_ODIR5K_SRC）")
    args = ap.parse_args()
    src = source_dir(args.src, "HEALTHJEV_ODIR5K_SRC", "odir5k")
    pq = require_exists(src / "huggingface.co" / "datasets" / "bumbledeep"
                        / "odir" / "data", "ODIR train parquet dir")
    img_out = DATA_DIR / "odir5k" / "images"
    files = sorted(pq.glob("train-*.parquet"))
    df = pd.concat([pd.read_parquet(f, columns=["patient_id", "age", "sex",
                                                "label", "label_code", "image"])
                    for f in files], ignore_index=True)
    print("total rows:", len(df), "unique patients:", df["patient_id"].nunique())
    print("label dist:", df["label"].value_counts().to_dict())

    rows = []
    for i, r in enumerate(df.itertuples(index=False)):
        rows.append({
            "case_id": f"odir-p{r.patient_id}-i{i}",
            "payload": {"patient_id": int(r.patient_id), "age": int(r.age),
                        "sex": str(r.sex),
                        "modality": "fundus photography"},
            "gold": {"label": str(r.label), "label_code": int(r.label_code)},
            "_iso_field": "patient_id",
            "_subject": str(r.patient_id),
            "_layer": str(r.label),
            "_row": i,
        })
    accum, test = stratified_group_split(rows, group_key="_subject",
                                         n_accum=N_ACCUM, n_test=N_TEST,
                                         strat_key="_layer", seed=SEED)
    img_out.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for r in accum + test:
        img_path = img_out / f"{r['case_id']}.jpg"
        r["payload"]["image_path"] = str(img_path)
        r.pop("_subject", None)
        r.pop("_layer", None)
        if img_path.exists():
            continue
        raw = df.iloc[r.pop("_row")]["image"]
        data = raw["bytes"] if isinstance(raw, dict) else raw
        img_path.write_bytes(data)
        n_new += 1
    print(f"images extracted (new): {n_new} -> {img_out}")
    write_manifest("odir5k", accum, test)


if __name__ == "__main__":
    main()
