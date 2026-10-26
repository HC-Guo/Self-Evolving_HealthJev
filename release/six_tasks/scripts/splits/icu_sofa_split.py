# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_split import SEED, require_exists, source_dir, write_manifest

N_ACCUM, N_TEST = 500, 200
DEMO_PARAMS = {"RecordID", "Age", "Gender", "Height", "Weight", "ICUType"}


def parse_record(path: Path) -> dict:
    series: dict = defaultdict(list)
    demo: dict = {}
    for line in path.read_text(errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        _t, param, val = parts
        try:
            v = float(val)
        except ValueError:
            continue
        if param in DEMO_PARAMS:
            demo[param] = v
        else:
            series[param].append(v)
    meas = {}
    for k, vs in series.items():
        vs = [v for v in vs if v > 0]
        if not vs:
            continue
        meas[k] = {"n": len(vs), "min": round(min(vs), 2),
                   "max": round(max(vs), 2),
                   "mean": round(sum(vs) / len(vs), 2)}
    return {"demo": demo, "measurements": meas}


def main():
    ap = argparse.ArgumentParser(description="icu_sofa 切分")
    ap.add_argument("--src", default=None,
                    help="数据集源目录（默认 $HEALTHJEV_BIOMEDICAL_DATA_DIR/icu_sofa，"
                         "或 env HEALTHJEV_ICU_SOFA_SRC）")
    args = ap.parse_args()
    src = source_dir(args.src, "HEALTHJEV_ICU_SOFA_SRC", "icu_sofa")
    set_a = require_exists(src / "set-a", "set-a records dir (MIMIC-III Clinical Database)")
    outcomes_txt = require_exists(src / "Outcomes-a.txt", "Outcomes-a.txt")
    outcomes = {}
    lines = outcomes_txt.read_text().strip().splitlines()
    header = lines[0].split(",")
    idx = {name: i for i, name in enumerate(header)}
    for line in lines[1:]:
        parts = line.split(",")
        outcomes[int(float(parts[idx["RecordID"]]))] = {
            "SOFA": int(float(parts[idx["SOFA"]])),
            "SAPS-I": int(float(parts[idx["SAPS-I"]])),
            "Length_of_stay": float(parts[idx["Length_of_stay"]]),
            "In-hospital_death": int(float(parts[idx["In-hospital_death"]])),
        }

    files = sorted(set_a.glob("*.txt"))
    print("records:", len(files))
    rows = []
    n_no_gold = 0
    for p in files:
        rid = int(p.stem)
        oc = outcomes.get(rid)
        if oc is None or oc["SOFA"] < 0:
            n_no_gold += 1
            continue
        parsed = parse_record(p)
        rows.append({
            "case_id": f"icu-sofa-{rid}",
            "payload": {"record_id": rid, **parsed},
            "gold": {"sofa": oc["SOFA"],
                     "saps_i": oc["SAPS-I"],
                     "length_of_stay_days": oc["Length_of_stay"],
                     "in_hospital_death": oc["In-hospital_death"]},
            "_iso_field": "record_id",
        })
    print("rows with valid SOFA gold:", len(rows), "skipped:", n_no_gold)

    rng = random.Random(SEED)
    rng.shuffle(rows)
    test_rows = rows[:N_TEST]
    accum_rows = rows[N_TEST:N_TEST + N_ACCUM]
    accum_rows.sort(key=lambda r: r["case_id"])
    test_rows.sort(key=lambda r: r["case_id"])
    write_manifest("icu_sofa", accum_rows, test_rows)
    from collections import Counter
    print("gold sofa dist(accum):",
          Counter(r["gold"]["sofa"] for r in accum_rows).most_common())


if __name__ == "__main__":
    main()
