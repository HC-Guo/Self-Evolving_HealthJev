# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

TOP_N = 20
WINDOW_DAYS = 90
MIN_EVENT_DAYS = 15

pd = None


def _require_data_libs():
    global pd
    try:
        import pandas
    except ImportError as exc:
        raise SystemExit(f"本步骤需要 pandas（当前环境缺失）：{exc}\n"
                         "可先离线查看用法：python3 step1v5_make_top20.py --help")
    pd = pandas


def _guard_out_path(out_path: Path):
    p = Path(out_path).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit(f"refusing to write inside the release tree: {p}\n"
                         "构造产物含受保护数据的 subject_id，请指向发布树之外的用户受控路径。")


def main():
    ap = argparse.ArgumentParser(
        description="构造步骤 1b：从筛选池 CSV 生成 Top N 候选摘要 JSON")
    ap.add_argument("--pool", required=True,
                    help="筛选池 CSV（screening_pool_v5.csv，用户受控）")
    ap.add_argument("--mimic-dir", default=os.environ.get("HEALTHJEV_MIMIC_DIR"),
                    required=os.environ.get("HEALTHJEV_MIMIC_DIR") is None,
                    help="用户授权的 MIMIC-IV 根目录（含 hosp/；也可用环境变量 HEALTHJEV_MIMIC_DIR）")
    ap.add_argument("--out", required=True,
                    help="输出 JSON 路径（用户受控；不得位于发布树内）")
    ap.add_argument("--top-n", type=int, default=TOP_N, help=f"候选数量（默认 {TOP_N}）")
    args = ap.parse_args()

    _require_data_libs()

    hosp = Path(args.mimic_dir) / "hosp"
    if not hosp.is_dir():
        raise SystemExit(f"未找到 hosp 目录：{hosp}（--mimic-dir 应指向包含 hosp/ 的 MIMIC-IV 根目录）")
    out_path = Path(args.out)
    _guard_out_path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pool = pd.read_csv(args.pool)
    top = pool.head(args.top_n)

    admissions = pd.read_csv(
        hosp / "admissions.csv",
        usecols=["subject_id", "hadm_id", "admittime", "dischtime", "admission_type"],
    )

    summaries = []
    for _, row in top.iterrows():
        adm = admissions[admissions["subject_id"] == row["subject_id"]].sort_values("admittime")
        summaries.append({
            "rank": int(row["rank"]),
            "subject_id": int(row["subject_id"]),
            "gender": row["gender"],
            "anchor_age": int(row["anchor_age"]),
            "conditions": ast.literal_eval(row["conditions"]) if isinstance(row["conditions"], str) else [],
            "n_admissions": int(row["n_admissions"]),
            "n_rx_records": int(row["n_rx_records"]),
            "n_rx_drugs": int(row["n_rx_drugs"]),
            "lab_n_events": int(row["lab_n_events"]),
            "lab_n_items": int(row["lab_n_items"]),
            "lab_span_days": int(row["lab_span_days"]),
            "best_window_start": row["best_window_start"],
            "best_window_end": row["best_window_end"],
            "best_window_event_days": int(row["best_window_event_days"]),
            "n_total_event_days": int(row["n_total_event_days"]),
            "quality_score": round(float(row["quality_score"]), 4),
            "admissions": [
                {
                    "hadm_id": int(a["hadm_id"]),
                    "admittime": str(a["admittime"]),
                    "dischtime": str(a["dischtime"]),
                    "admission_type": a["admission_type"],
                }
                for _, a in adm.iterrows()
            ],
        })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "v5",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "seed": 20260705,
            "criteria": {
                "A": "慢病主导（CHRONIC_POOL ICD 前缀）",
                "B": ">=2 次住院",
                "C": "化验跨度>=365天",
                "D": "用药覆盖>=2次住院",
                "E": f"最佳{WINDOW_DAYS}天窗口事件日>={MIN_EVENT_DAYS}（事件=化验∪用药∪在院日，两指针确定性滑窗）",
                "boundaries": "年龄18-88 / 无死亡登记 / 真实末次出院<=2021",
            },
            "quality_score_weights": {
                "z_lab_n_events": 0.30, "z_lab_n_items": 0.25, "z_lab_span_days": 0.20,
                "z_n_rx_records": 0.15, "z_n_admissions": 0.10,
            },
            "n_pool": int(len(pool)),
            "n_pass_E": int((pool["best_window_event_days"] >= MIN_EVENT_DAYS).sum()),
            "top_n": args.top_n,
            "patients": summaries,
        }, f, ensure_ascii=False, indent=2)
    print(f"[OUT] {out_path}")
    print(f"\n===== Top {args.top_n} 摘要 =====")
    for _, r in top.iterrows():
        print(f"  #{int(r['rank']):2d} {int(r['subject_id'])} {r['gender']} age={int(r['anchor_age'])} "
              f"density={int(r['best_window_event_days']):3d}天/{WINDOW_DAYS}天 window={r['best_window_start']}~{r['best_window_end']} "
              f"score={r['quality_score']:.4f}")


if __name__ == "__main__":
    main()
