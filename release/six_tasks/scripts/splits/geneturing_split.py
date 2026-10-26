# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_split import SEED, require_exists, source_dir, stratified_take, write_manifest

N_ACCUM, N_TEST = 300, 150


def main():
    ap = argparse.ArgumentParser(description="geneturing 切分")
    ap.add_argument("--src", default=None,
                    help="数据集源目录（默认 $HEALTHJEV_BIOMEDICAL_DATA_DIR/geneturing，"
                         "或 env HEALTHJEV_GENETURING_SRC）")
    args = ap.parse_args()
    src = source_dir(args.src, "HEALTHJEV_GENETURING_SRC", "geneturing")
    csv_path = require_exists(
        src / "GeneTuring-main" / "GeneGPT" / "Q&A_dataset.csv",
        "Q&A_dataset.csv (GeneTuring dataset)")
    rng = random.Random(SEED)
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    items = []
    for r in rows[1:]:
        if len(r) < 4 or not r[2].strip() or not r[3].strip():
            continue
        module = r[1].strip()
        q = r[2].strip()
        a = r[3].strip()
        items.append({
            "case_id": f"geneturing-{module.lower().replace(' ', '-')}"
                       f"-{len(items):04d}",
            "payload": {"module": module, "question": q},
            "gold": {"answer": a, "module": module},
        })
    print("total QA:", len(items),
          "modules:", len({i["payload"]["module"] for i in items}))

    accum = stratified_take(items, lambda r: r["payload"]["module"], N_ACCUM, rng)
    used = {r["case_id"] for r in accum}
    rest = [r for r in items if r["case_id"] not in used]
    test = stratified_take(rest, lambda r: r["payload"]["module"], N_TEST, rng)
    write_manifest("geneturing", accum, test)
    from collections import Counter
    print("module dist(accum):",
          Counter(r["payload"]["module"] for r in accum).most_common(4), "...")
    print("module dist(test):",
          Counter(r["payload"]["module"] for r in test).most_common(4), "...")


if __name__ == "__main__":
    main()
