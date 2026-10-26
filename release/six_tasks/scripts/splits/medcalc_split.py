# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_split import SEED, require_exists, source_dir, stratified_take, write_manifest

N_ACCUM = 1000
N_TEST = 200


def _rows(mb: Path):
    train_zip = require_exists(mb / "train_data.csv.zip", "train_data.csv.zip")
    test_csv = require_exists(mb / "test_data.csv", "test_data.csv")
    z = zipfile.ZipFile(train_zip)
    with z.open("train_data.csv") as fh:
        train = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")))
    with open(test_csv, encoding="utf-8") as fh:
        test = list(csv.DictReader(fh))
    return train, test


def _case(r: dict, src: str) -> dict:
    return {
        "case_id": f"medcalc-{src}-{r['Note ID']}-{r['Row Number']}",
        "payload": {
            "note_id": r["Note ID"],
            "note_type": r["Note Type"],
            "calculator_name": r["Calculator Name"],
            "calculator_id": r["Calculator ID"],
            "category": r["Category"],
            "output_type": r["Output Type"],
            "patient_note": r["Patient Note"],
            "question": r["Question"],
        },
        "gold": {
            "answer": r["Ground Truth Answer"],
            "lower_limit": r["Lower Limit"],
            "upper_limit": r["Upper Limit"],
            "output_type": r["Output Type"],
            "category": r["Category"],
        },
        "_iso_field": "note_id",
    }


def main():
    ap = argparse.ArgumentParser(description="medcalc 切分")
    ap.add_argument("--src", default=None,
                    help="数据集源目录（指向 medcalc_bench，默认 "
                         "$HEALTHJEV_BIOMEDICAL_DATA_DIR/candidate_datasets/medcalc_bench，"
                         "或 env HEALTHJEV_MEDCALC_SRC）")
    args = ap.parse_args()
    mb = source_dir(args.src, "HEALTHJEV_MEDCALC_SRC",
                    "candidate_datasets/medcalc_bench") / "datasets"
    rng = random.Random(SEED)
    train, test = _rows(mb)
    test_ids = {r["Note ID"] for r in test}
    train_clean = [r for r in train if r["Note ID"] not in test_ids]
    print(f"train={len(train)} test={len(test)} overlap_note_ids={len(test_ids)} "
          f"train_after_drop={len(train_clean)}")

    accum_rows = [ _case(r, "acc") for r in
                   stratified_take(train_clean, lambda r: r["Category"], N_ACCUM, rng) ]
    test_rows = [ _case(r, "test") for r in
                  stratified_take(test, lambda r: r["Category"], N_TEST, rng) ]

    write_manifest("medcalc", accum_rows, test_rows)
    from collections import Counter
    print("accumulate categories:", Counter(r["gold"]["category"] for r in accum_rows).most_common())
    print("test categories:", Counter(r["gold"]["category"] for r in test_rows).most_common())


if __name__ == "__main__":
    main()
