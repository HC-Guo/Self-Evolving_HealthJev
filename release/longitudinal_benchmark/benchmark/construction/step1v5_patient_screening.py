# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SEED = 20260705
TOP_N = 20
WINDOW_DAYS = 90
MIN_EVENT_DAYS = 15

CHRONIC_POOL = {
    "diabetes":        (["E10", "E11"], ["250"]),
    "hypertension":    (["I10", "I11", "I12", "I13", "I15"], ["401", "402", "403", "404", "405"]),
    "ckd":             (["N18"], ["585", "586"]),
    "copd":            (["J44"], ["491", "492", "496"]),
    "coronary_heart":  (["I20", "I21", "I22", "I23", "I24", "I25"], ["410", "411", "412", "413", "414"]),
    "atrial_fib":      (["I48"], ["4273", "427.3"]),
    "hyperthyroidism": (["E05"], ["242"]),
}

np = None
pd = None
EPOCH = None


def _require_data_libs():
    global np, pd, EPOCH
    try:
        import numpy
        import pandas
    except ImportError as exc:
        raise SystemExit(f"本步骤需要 numpy 与 pandas（当前环境缺失）：{exc}\n"
                         "可先离线查看用法：python3 step1v5_patient_screening.py --help")
    np, pd = numpy, pandas
    EPOCH = np.datetime64("1970-01-01")


def _guard_out_dir(out_dir: Path):
    p = Path(out_dir).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit(f"refusing to write inside the release tree: {p}\n"
                         "构造产物含受保护数据的 subject_id，请指向发布树之外的用户受控目录。")


def dates_to_daynums(dates):
    if not dates:
        return np.array([], dtype=np.int64)
    arr = np.array(sorted(set((pd.Timestamp(d).to_datetime64() - EPOCH).astype("timedelta64[D]").astype(np.int64) for d in dates)))
    return arr


def best_window(daynums, width=WINDOW_DAYS):
    n = len(daynums)
    if n == 0:
        return None, 0
    best_cnt, best_start = 0, int(daynums[0])
    j = 0
    for i in range(n):
        while j < n and daynums[j] < daynums[i] + width:
            j += 1
        cnt = j - i
        if cnt > best_cnt:
            best_cnt = cnt
            best_start = int(daynums[i])
    return best_start, best_cnt


def load_stage_a(hosp):
    print("[Stage A] 加载小表 ...", flush=True)
    patients = pd.read_csv(
        os.path.join(hosp, "patients.csv"),
        usecols=["subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group", "dod"],
    )
    admissions = pd.read_csv(
        os.path.join(hosp, "admissions.csv"),
        usecols=["subject_id", "hadm_id", "admittime", "dischtime", "deathtime", "admission_type"],
        parse_dates=["admittime", "dischtime", "deathtime"],
    )
    diagnoses = pd.read_csv(
        os.path.join(hosp, "diagnoses_icd.csv"),
        usecols=["subject_id", "hadm_id", "icd_code", "icd_version", "seq_num"],
    )
    prescriptions = pd.read_csv(
        os.path.join(hosp, "prescriptions.csv"),
        usecols=["subject_id", "hadm_id", "drug", "starttime"],
        parse_dates=["starttime"],
    )
    print(f"  patients={len(patients):,} admissions={len(admissions):,} "
          f"diagnoses={len(diagnoses):,} prescriptions={len(prescriptions):,}", flush=True)
    return patients, admissions, diagnoses, prescriptions


def main():
    global WINDOW_DAYS, MIN_EVENT_DAYS, TOP_N
    ap = argparse.ArgumentParser(
        description="构造步骤 1：MIMIC-IV 患者筛选（硬标准 A~E + 90 天密度 → Top N 候选）")
    ap.add_argument("--mimic-dir", default=os.environ.get("HEALTHJEV_MIMIC_DIR"),
                    required=os.environ.get("HEALTHJEV_MIMIC_DIR") is None,
                    help="用户授权的 MIMIC-IV 根目录（含 hosp/；也可用环境变量 HEALTHJEV_MIMIC_DIR 提供）")
    ap.add_argument("--out-dir", required=True,
                    help="输出目录（用户受控；产物含 subject_id，不得位于发布树内）")
    ap.add_argument("--top-n", type=int, default=TOP_N,
                    help=f"候选数量（默认 {TOP_N}）")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                    help=f"密度窗口宽度（默认 {WINDOW_DAYS} 天）")
    ap.add_argument("--min-event-days", type=int, default=MIN_EVENT_DAYS,
                    help=f"标准 E 的事件日下限（默认 {MIN_EVENT_DAYS} 天）")
    args = ap.parse_args()

    _require_data_libs()

    mimic_dir = Path(args.mimic_dir)
    hosp = str(mimic_dir / "hosp")
    if not Path(hosp).is_dir():
        raise SystemExit(f"未找到 hosp 目录：{hosp}（--mimic-dir 应指向包含 hosp/ 的 MIMIC-IV 根目录）")
    out_dir = Path(args.out_dir)
    _guard_out_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    WINDOW_DAYS, MIN_EVENT_DAYS, TOP_N = args.window_days, args.min_event_days, args.top_n

    t0 = datetime.now()
    patients, admissions, diagnoses, prescriptions = load_stage_a(hosp)

    print("[Stage A] 慢病命中 ...", flush=True)
    codes = diagnoses["icd_code"].astype(str).str.strip()
    c9 = codes.where(diagnoses["icd_version"].astype(int) == 9).str.replace(".", "", regex=False)
    c10 = codes.where(diagnoses["icd_version"].astype(int) == 10)
    hit_df = pd.DataFrame(index=diagnoses.index)
    for cond, (p10, p9) in CHRONIC_POOL.items():
        mask = pd.Series(False, index=diagnoses.index)
        for p in p10:
            mask |= c10.str.startswith(p, na=False)
        for p in p9:
            mask |= c9.str.startswith(p, na=False)
        hit_df[cond] = mask
    conds = list(CHRONIC_POOL)
    mat = hit_df[conds].to_numpy()
    any_hit = mat.any(axis=1)
    hits = np.full(len(diagnoses), None, dtype=object)
    sub_idx = np.flatnonzero(any_hit)
    for i in sub_idx:
        hits[i] = [conds[j] for j in np.flatnonzero(mat[i])]
    diagnoses = diagnoses.assign(hits=hits)
    dx_chronic = diagnoses[diagnoses["hits"].str.len() > 0]
    patient_conditions = dx_chronic.groupby("subject_id")["hits"].agg(
        lambda s: sorted(set(h for lst in s for h in lst))
    ).rename("conditions")
    pool = patients[patients["subject_id"].isin(patient_conditions.index)].copy()
    pool = pool.merge(patient_conditions, left_on="subject_id", right_index=True)
    print(f"  A 慢病主导: {len(pool):,}", flush=True)

    adm_sorted = admissions.sort_values(["subject_id", "admittime"])
    adm_grp = adm_sorted.groupby("subject_id")
    adm_stats = pd.DataFrame({
        "n_admissions": adm_grp["hadm_id"].nunique(),
        "first_admit": adm_grp["admittime"].min(),
        "last_admit": adm_grp["admittime"].max(),
        "last_disch": adm_grp["dischtime"].max(),
        "n_death_in_hosp": adm_grp["deathtime"].count(),
    }).reset_index()
    adm_stats["admission_span_days"] = (
        adm_stats["last_disch"] - adm_stats["first_admit"]
    ).dt.days.clip(lower=0)

    grp_end = patients["anchor_year_group"].str[-4:].astype(int)
    patients = patients.assign(group_end_year=grp_end)
    win = patients[["subject_id", "anchor_year", "group_end_year"]].merge(
        adm_stats, on="subject_id", how="right"
    )
    win["real_last_year"] = win["last_disch"].dt.year - win["anchor_year"] + win["group_end_year"]
    win["follow_up_window_years"] = 2022 - win["real_last_year"]

    pool = pool.merge(adm_stats, on="subject_id").merge(
        win[["subject_id", "real_last_year", "follow_up_window_years"]], on="subject_id"
    )
    pool = pool.merge(
        prescriptions.groupby("subject_id").agg(
            n_rx_records=("drug", "size"),
            n_rx_drugs=("drug", "nunique"),
            n_rx_hadm=("hadm_id", "nunique"),
        ).reset_index(),
        on="subject_id", how="left",
    )

    before = len(pool)
    pool = pool[
        (pool["n_admissions"] >= 2)
        & (pool["anchor_age"].between(18, 88))
        & (pool["dod"].isna())
        & (pool["n_death_in_hosp"] == 0)
        & (pool["real_last_year"] <= 2021)
        & (pool["n_rx_hadm"] >= 2)
    ]
    print(f"  B+边界+D: {before:,} -> {len(pool):,}", flush=True)

    cands = set(pool["subject_id"])

    print(f"[Stage B] 收集事件日（候选 {len(cands):,} 人）...", flush=True)
    event_days = {sid: set() for sid in cands}
    for row in admissions[admissions["subject_id"].isin(cands)].itertuples(index=False):
        days = event_days.get(row.subject_id)
        if days is None:
            continue
        d0 = row.admittime.date()
        d1 = row.dischtime.date() if pd.notna(row.dischtime) else d0
        span = (d1 - d0).days
        if span < 0 or span > 365:
            span = min(max(span, 0), 365)
        for k in range(span + 1):
            days.add(d0 + timedelta(days=k))
    print(f"  住院日收集完成", flush=True)

    rx = prescriptions[prescriptions["subject_id"].isin(cands)]
    for sid, sub in rx.groupby("subject_id"):
        days = event_days[sid]
        for t in sub["starttime"].dropna():
            days.add(pd.Timestamp(t).date())
    print(f"  用药日收集完成", flush=True)

    lab_stats = {}
    usecols = ["subject_id", "itemid", "charttime"]
    for i, chunk in enumerate(pd.read_csv(
        os.path.join(hosp, "labevents.csv.gz"),
        usecols=usecols, chunksize=10_000_000,
        dtype={"subject_id": "int64", "itemid": "int64"},
    ), 1):
        chunk = chunk[chunk["subject_id"].isin(cands)].dropna(subset=["charttime"])
        if chunk.empty:
            if i % 5 == 0:
                print(f"  lab chunk {i}: 0 命中", flush=True)
            continue
        ct = pd.to_datetime(chunk["charttime"], errors="coerce")
        chunk = chunk.assign(_ct=ct)
        chunk = chunk.dropna(subset=["_ct"])
        g = chunk.groupby("subject_id")
        for sid, sub in g:
            st = lab_stats.setdefault(sid, {"min": None, "max": None, "n": 0, "items": set()})
            cts = sub["_ct"]
            mn, mx = cts.min(), cts.max()
            st["min"] = mn if st["min"] is None else min(st["min"], mn)
            st["max"] = mx if st["max"] is None else max(st["max"], mx)
            st["n"] += len(sub)
            st["items"] |= set(sub["itemid"].unique().tolist())
            days = event_days.get(sid)
            if days is not None:
                for t in cts:
                    days.add(t.date())
        if i % 5 == 0:
            print(f"  lab chunk {i}: 累计 {len(lab_stats):,} 人", flush=True)

    lab_df = pd.DataFrame([
        {
            "subject_id": sid,
            "lab_first": v["min"],
            "lab_last": v["max"],
            "lab_span_days": (v["max"] - v["min"]).days if v["min"] is not None else 0,
            "lab_n_events": v["n"],
            "lab_n_items": len(v["items"]),
        }
        for sid, v in lab_stats.items()
    ])
    pool = pool.merge(lab_df, on="subject_id", how="left")
    pool[["lab_n_events", "lab_n_items"]] = pool[["lab_n_events", "lab_n_items"]].fillna(0)
    before = len(pool)
    pool = pool[pool["lab_span_days"] >= 365]
    print(f"  C 化验跨度>=1年: {before:,} -> {len(pool):,}", flush=True)

    print("[Stage C] 密度滑窗（标准 E）...", flush=True)
    rows = []
    for sid in pool["subject_id"]:
        dn = dates_to_daynums(event_days.get(sid, set()))
        best_start, best_cnt = best_window(dn, width=WINDOW_DAYS)
        rows.append({
            "subject_id": sid,
            "best_window_start": (EPOCH + np.timedelta64(int(best_start), "D")).astype("datetime64[D]").astype(str) if best_start is not None else None,
            "best_window_end": (EPOCH + np.timedelta64(int(best_start) + WINDOW_DAYS - 1, "D")).astype("datetime64[D]").astype(str) if best_start is not None else None,
            "best_window_event_days": best_cnt,
            "n_total_event_days": len(dn),
        })
    dens = pd.DataFrame(rows)
    pool = pool.merge(dens, on="subject_id")

    before = len(pool)
    pool_e = pool[pool["best_window_event_days"] >= MIN_EVENT_DAYS]
    print(f"  E 最佳{WINDOW_DAYS}天窗口>={MIN_EVENT_DAYS}事件日: {before:,} -> {len(pool_e):,}", flush=True)

    for col in ["lab_n_events", "lab_n_items", "lab_span_days", "n_rx_records", "n_admissions"]:
        pool_e = pool_e.copy()
        pool_e["z_" + col] = np.log1p(pool_e[col])
    zcols = ["z_lab_n_events", "z_lab_n_items", "z_lab_span_days", "z_n_rx_records", "z_n_admissions"]
    weights = {
        "z_lab_n_events": 0.30,
        "z_lab_n_items": 0.25,
        "z_lab_span_days": 0.20,
        "z_n_rx_records": 0.15,
        "z_n_admissions": 0.10,
    }
    for c in zcols:
        lo, hi = pool_e[c].min(), pool_e[c].max()
        pool_e[c + "_n"] = (pool_e[c] - lo) / (hi - lo) if hi > lo else 0.0
    pool_e["quality_score"] = sum(pool_e[c + "_n"] * w for c, w in weights.items())

    pool_e = pool_e.sort_values("quality_score", ascending=False).reset_index(drop=True)
    pool_e.insert(0, "rank", pool_e.index + 1)

    out_csv = out_dir / "screening_pool_v5.csv"
    pool_e.to_csv(out_csv, index=False)
    print(f"[OUT] {out_csv}（{len(pool_e):,} 行）", flush=True)

    top = pool_e.head(TOP_N)
    diag_top = diagnoses.sort_values(["hadm_id", "seq_num"]).drop_duplicates("hadm_id", keep="first")
    dx_map = diag_top.set_index("hadm_id")["icd_code"].to_dict()

    def patient_summary(row):
        adm = admissions[admissions["subject_id"] == row.subject_id].sort_values("admittime")
        return {
            "rank": int(row["rank"]),
            "subject_id": int(row.subject_id),
            "gender": row.gender,
            "anchor_age": int(row.anchor_age),
            "conditions": list(row.conditions),
            "n_admissions": int(row.n_admissions),
            "n_rx_records": int(row.n_rx_records),
            "n_rx_drugs": int(row.n_rx_drugs),
            "lab_n_events": int(row.lab_n_events),
            "lab_n_items": int(row.lab_n_items),
            "lab_span_days": int(row.lab_span_days),
            "best_window_start": row.best_window_start,
            "best_window_end": row.best_window_end,
            "best_window_event_days": int(row.best_window_event_days),
            "n_total_event_days": int(row.n_total_event_days),
            "quality_score": round(float(row.quality_score), 4),
            "admissions": [
                {
                    "hadm_id": int(a.hadm_id),
                    "admittime": str(a.admittime),
                    "dischtime": str(a.dischtime),
                    "admission_type": a.admission_type,
                }
                for a in adm.itertuples(index=False)
            ],
        }

    summaries = [patient_summary(r) for r in top.itertuples(index=False)]
    out_json = out_dir / "candidate_patients_20_v5.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "version": "v5",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "seed": SEED,
            "criteria": {
                "A": "慢病主导（CHRONIC_POOL ICD 前缀）",
                "B": ">=2 次住院",
                "C": "化验跨度>=365天",
                "D": "用药覆盖>=2次住院",
                "E": f"最佳{WINDOW_DAYS}天窗口事件日>={MIN_EVENT_DAYS}（事件=化验∪用药∪在院日，两指针确定性滑窗）",
                "boundaries": "年龄18-88 / 无死亡登记 / 真实末次出院<=2021",
            },
            "quality_score_weights": weights,
            "n_pool": int(len(pool_e)),
            "top_n": TOP_N,
            "patients": summaries,
        }, f, ensure_ascii=False, indent=2)
    print(f"[OUT] {out_json}（Top {TOP_N}）", flush=True)

    print(f"\n===== Top {TOP_N} 摘要 =====", flush=True)
    for r in top.itertuples(index=False):
        print(f"  #{r.rank:2d} {r.subject_id} {r.gender} age={r.anchor_age} "
              f"density={r.best_window_event_days:3d}天/{WINDOW_DAYS}天 window={r.best_window_start}~{r.best_window_end} "
              f"score={r.quality_score:.4f}", flush=True)
    print(f"\n耗时: {datetime.now() - t0}", flush=True)


if __name__ == "__main__":
    main()
