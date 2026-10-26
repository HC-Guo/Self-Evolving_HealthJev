# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LAB_WHITELIST = {
    50852: "HbA1c", 50931: "血糖", 50809: "血糖(末梢)",
    50912: "肌酐", 51006: "尿素氮", 51097: "尿酸",
    50995: "LDL-C", 50967: "HDL-C", 51000: "甘油三酯", 50900: "总胆固醇",
    51222: "血红蛋白", 51301: "白细胞", 51265: "血小板",
    50971: "血钾", 50983: "血钠",
    50893: "TSH", 51237: "INR",
    50861: "ALT", 50878: "AST", 50885: "总胆红素", 50862: "白蛋白",
}

pd = None


def _require_data_libs():
    global pd
    try:
        import pandas
    except ImportError as exc:
        raise SystemExit(f"本步骤需要 pandas（当前环境缺失）：{exc}\n"
                         "可先离线查看用法：python3 step2c_day0_archive.py --help")
    pd = pandas


def _guard_out_path(out_path: Path):
    p = Path(out_path).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit(f"refusing to write inside the release tree: {p}\n"
                         "构造产物含受保护数据标识与临床内容，请指向发布树之外的用户受控路径。")


def main():
    ap = argparse.ArgumentParser(
        description="构造步骤 2c：Day0 档案构建（窗口前基线化验 + 窗口起始用药 + 既往史）")
    ap.add_argument("--candidates", required=True,
                    help="候选患者 JSON（candidate_patients_20_v5.json，用户受控）")
    ap.add_argument("--ledger-summary", required=True, help="ledger_v5_summary.json 路径（用户受控）")
    ap.add_argument("--mimic-dir", default=os.environ.get("HEALTHJEV_MIMIC_DIR"),
                    required=os.environ.get("HEALTHJEV_MIMIC_DIR") is None,
                    help="用户授权的 MIMIC-IV 根目录（含 hosp/；也可用环境变量 HEALTHJEV_MIMIC_DIR）")
    ap.add_argument("--out", required=True,
                    help="输出 day0_archives_v5.json 路径（用户受控；不得位于发布树内）")
    args = ap.parse_args()

    _require_data_libs()

    hosp = Path(args.mimic_dir) / "hosp"
    if not hosp.is_dir():
        raise SystemExit(f"未找到 hosp 目录：{hosp}（--mimic-dir 应指向包含 hosp/ 的 MIMIC-IV 根目录）")
    out_path = Path(args.out)
    _guard_out_path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = datetime.now()
    cand = json.load(open(args.candidates, encoding="utf-8"))
    summary = json.load(open(args.ledger_summary, encoding="utf-8"))
    pre = summary["pre_window_summaries"]

    subj_set = set()
    window_map = {}
    for p in cand["patients"]:
        sid = p["subject_id"]
        subj_set.add(sid)
        window_map[sid] = (pd.Timestamp(p["best_window_start"]).normalize(),
                           pd.Timestamp(p["best_window_end"]).normalize())
    print(f"[Load] {len(subj_set)} patients", flush=True)

    pre_labs = {}
    usecols = ["subject_id", "itemid", "charttime", "valuenum", "valueuom"]
    for i, chunk in enumerate(pd.read_csv(
        hosp / "labevents.csv.gz", usecols=usecols,
        chunksize=10_000_000, dtype={"subject_id": "int64", "itemid": "int64"},
    ), 1):
        chunk = chunk[chunk["subject_id"].isin(subj_set) & chunk["itemid"].isin(LAB_WHITELIST)]
        chunk = chunk.dropna(subset=["charttime", "valuenum"])
        for sid, itemid, ct, vn, uom in chunk.itertuples(index=False):
            d = pd.to_datetime(ct).normalize()
            ws, _ = window_map[int(sid)]
            if d >= ws:
                continue
            name = LAB_WHITELIST[int(itemid)]
            cur = pre_labs.setdefault(int(sid), {})
            if name not in cur or d > cur[name][0]:
                u = f" {uom}" if isinstance(uom, str) and uom.strip() else ""
                cur[name] = (d, f"{float(vn):g}{u}")
        print(f"  chunk-{i}", flush=True)

    archives = {}
    for sid in sorted(subj_set):
        p = pre[str(sid)]
        ws, _ = window_map[sid]
        labs = pre_labs.get(sid, {})
        baseline_checkups = [
            {"date": str(labs[k][0].date()), "name": k, "value": labs[k][1]}
            for k in sorted(labs)
        ]
        baseline_checkups = [c for c in baseline_checkups
                             if (ws - pd.Timestamp(c["date"])).days <= 365 * 5]
        archives[str(sid)] = {
            "subject_id": sid,
            "window_start": str(ws.date()),
            "basic": {
                "gender": p["gender"],
                "age": p["age_at_window_start"],
            },
            "chronic_conditions": p["chronic_conditions"],
            "top_pre_window_diagnoses": p["top_pre_window_diagnoses"],
            "n_pre_window_admissions": p["n_pre_window_admissions"],
            "n_pre_window_procedures": p["n_pre_window_procedures"],
            "medications_at_window_start": p["medications_active_at_window_start"],
            "baseline_checkups": baseline_checkups,
        }
        print(f"  {sid}: baseline_labs={len(baseline_checkups)} "
              f"active_meds={len(p['medications_active_at_window_start'])}", flush=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().isoformat(), "archives": archives},
                  f, ensure_ascii=False, indent=2)
    print(f"[Done] -> {out_path}，用时 {datetime.now() - t0}", flush=True)


if __name__ == "__main__":
    main()
