# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_split import (DATA_DIR, SEED, require_exists, source_dir,
                          stratified_group_split, write_manifest)

N_ACCUM, N_TEST = 500, 200
NOTE_MIN, NOTE_MAX = 500, 4000
TOP_K = 30


def main():
    ap = argparse.ArgumentParser(description="mimic_icd 切分")
    ap.add_argument("--src", default=None,
                    help="数据集源目录（默认 $HEALTHJEV_BIOMEDICAL_DATA_DIR/mimic_icd，"
                         "或 env HEALTHJEV_MIMIC_ICD_SRC；在该目录下找 raw/*.parquet）")
    args = ap.parse_args()
    src = source_dir(args.src, "HEALTHJEV_MIMIC_ICD_SRC", "mimic_icd")
    raw = require_exists(src / "raw", "raw parquet dir (rntc/mimic-icd-visit mirror)")
    out_dir = DATA_DIR / "mimic_icd"
    files = sorted(raw.glob("*.parquet"))
    assert files, f"no parquet under {RAW}"
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    print(f"loaded visits: {len(df)}")

    df["tlen"] = df["cleaned_text"].str.len()
    df = df[(df["tlen"] >= NOTE_MIN) & (df["tlen"] <= NOTE_MAX)]
    df = df[df["icd_code"].apply(
        lambda xs: xs is not None and not isinstance(xs, str)
        and hasattr(xs, "__len__") and len(xs) > 0)]
    print(f"after note-length filter: {len(df)}")

    freq = Counter()
    for codes in df["icd_code"]:
        freq.update({str(c).strip().upper() for c in codes})
    top30 = [c for c, _ in freq.most_common(TOP_K)]
    print("top-30:", top30)

    desc_by_code: dict = defaultdict(Counter)
    for codes, descs in zip(df["icd_code"], df["icd_descriptions"]):
        for c, d in zip(codes, descs):
            cu = str(c).strip().upper()
            if cu in freq and d:
                desc_by_code[cu][str(d).strip()] += 1
    candidates = [{"code": c, "rank": i + 1, "pool_freq": freq[c],
                   "desc": desc_by_code[c].most_common(1)[0][0]
                   if desc_by_code[c] else ""}
                  for i, c in enumerate(top30)]

    rows = []
    for r in df.itertuples(index=False):
        gset = sorted({str(c).strip().upper() for c in r.icd_code} & set(top30),
                      key=lambda c: list(r.icd_code).index(
                          next(x for x in r.icd_code
                               if str(x).strip().upper() == c)))
        if not gset:
            continue
        rows.append({
            "case_id": f"mimic-icd-{int(r.hadm_id)}",
            "payload": {
                "subject_id": str(r.subject_id),
                "hadm_id": int(r.hadm_id),
                "note": str(r.cleaned_text),
                "note_chars": int(r.tlen),
            },
            "gold": {"codes": gset, "n_codes": len(gset)},
            "_iso_field": "subject_id",
            "_subject": str(r.subject_id),
            "_layer": gset[0],
        })
    print(f"candidate cases (gold non-empty in top30): {len(rows)}, "
          f"unique subjects={len({r['_subject'] for r in rows})}")

    accum, test = stratified_group_split(rows, group_key="_subject",
                                         n_accum=N_ACCUM, n_test=N_TEST,
                                         strat_key="_layer", seed=SEED)
    for r in accum + test:
        r.pop("_subject", None)
        r.pop("_layer", None)
    write_manifest("mimic_icd", accum, test)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidates_top30.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=1), encoding="utf-8")

    for split in ("accumulate", "test"):
        p = out_dir / f"manifest_{split}.jsonl"
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        (out_dir / f"manifest_{split}.jsonl.sha256").write_text(
            f"{digest}  manifest_{split}.jsonl\n", encoding="utf-8")
        print(f"[sha256:{split}] {digest[:16]}...")

    for split, rs in (("accumulate", accum), ("test", test)):
        cnt = Counter(r["gold"]["n_codes"] for r in rs)
        print(f"[gold n_codes dist:{split}] {dict(sorted(cnt.items()))}")


if __name__ == "__main__":
    main()
