# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

WINDOW_DAYS = 90
TOP_N = 20

CHRONIC_POOL = {
    "diabetes":        (["E10", "E11"], ["250"]),
    "hypertension":    (["I10", "I11", "I12", "I13", "I15"], ["401", "402", "403", "404", "405"]),
    "ckd":             (["N18"], ["585", "586"]),
    "copd":            (["J44"], ["491", "492", "496"]),
    "coronary_heart":  (["I20", "I21", "I22", "I23", "I24", "I25"], ["410", "411", "412", "413", "414"]),
    "atrial_fib":      (["I48"], ["4273", "427.3"]),
    "hyperthyroidism": (["E05"], ["242"]),
}

LAB_WHITELIST = {
    50852: "HbA1c", 50931: "血糖", 50809: "血糖(末梢)",
    50912: "肌酐", 51006: "尿素氮", 51097: "尿酸",
    50995: "LDL-C", 50967: "HDL-C", 51000: "甘油三酯", 50900: "总胆固醇",
    51222: "血红蛋白", 51301: "白细胞", 51265: "血小板",
    50971: "血钾", 50983: "血钠",
    50893: "TSH", 51237: "INR",
    50861: "ALT", 50878: "AST", 50885: "总胆红素", 50862: "白蛋白",
}

OMR_WHITELIST = {"Blood Pressure": "血压", "Weight (Lbs)": "体重磅", "Weight": "体重",
                 "Pulse": "脉搏", "BMI": "BMI", "Heighth": "身高"}

pd = None


def _require_data_libs():
    global pd
    try:
        import pandas
    except ImportError as exc:
        raise SystemExit(f"本步骤需要 pandas（当前环境缺失）：{exc}\n"
                         "可先离线查看用法：python3 step2v5_build_ledger.py --help")
    pd = pandas


def _guard_out_dir(out_dir: Path):
    p = Path(out_dir).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit(f"refusing to write inside the release tree: {p}\n"
                         "构造产物含受保护数据标识与临床内容，请指向发布树之外的用户受控目录。")


class FactWriter:
    def __init__(self, subject_id):
        self.subject_id = subject_id
        self.n = 0
        self.rows = []

    def add(self, day_index, date, category, name, value, memory_level, state,
            disclosure_rule="clinical", source_ref=None, original_evidence="",
            supersedes=None):
        self.n += 1
        fid = f"F{self.subject_id}-{self.n:05d}"
        self.rows.append({
            "fact_id": fid,
            "subject_id": self.subject_id,
            "day_index": int(day_index),
            "date": None if date is None else str(pd.Timestamp(date).date()),
            "category": category,
            "name": name,
            "value": value,
            "memory_level": memory_level,
            "state": state,
            "supersedes": supersedes,
            "superseded_by": None,
            "disclosure_rule": disclosure_rule,
            "source_ref": source_ref or {},
            "original_evidence": str(original_evidence)[:300],
        })
        return fid


def to_day_index(d, window_start, window_end):
    if d is None or pd.isna(d):
        return None
    if d < window_start:
        return 0
    if d > window_end:
        return None
    return (d - window_start).days + 1


def match_chronic(code, version):
    hits = []
    if pd.isna(code):
        return hits
    code = str(code).strip()
    if version == 9:
        code = code.replace(".", "")
        for cond, (_, p9) in CHRONIC_POOL.items():
            if any(code.startswith(p) for p in p9):
                hits.append(cond)
    else:
        for cond, (p10, _) in CHRONIC_POOL.items():
            if any(code.startswith(p) for p in p10):
                hits.append(cond)
    return hits


DOSE_RE = re.compile(r"^(?P<base>[a-z][a-z \-]*?)(?:\s+[\d.]+.*)?$")


def normalize_drug(drug: str):
    d = re.sub(r"\s+", " ", str(drug).strip().lower())
    m = DOSE_RE.match(d)
    return (m.group("base") if m else d).strip()


def build_regimen_facts(rx: "pd.DataFrame", fw: FactWriter, window_start, window_end):
    rx = rx.dropna(subset=["drug"]).copy()
    rx["norm"] = rx["drug"].map(normalize_drug)
    rx["starttime"] = pd.to_datetime(rx["starttime"], errors="coerce")
    rx["stoptime"] = pd.to_datetime(rx["stoptime"], errors="coerce")
    rx = rx[~((rx["starttime"] > window_end) | rx["starttime"].isna())]
    latest_fact = {}
    for norm, grp in rx.groupby("norm"):
        grp = grp.sort_values("starttime")
        segs = []
        for _, r in grp.iterrows():
            dose = f"{r.get('dose_val_rx') if pd.notna(r.get('dose_val_rx')) else ''}" \
                   f"{r.get('dose_unit_rx') if pd.notna(r.get('dose_unit_rx')) else ''}".strip()
            route = str(r.get("route")).strip() if pd.notna(r.get("route")) else ""
            key = (dose, route)
            if segs and segs[-1]["key"] == key and pd.notna(segs[-1]["end"]) and pd.notna(r["starttime"]) \
                    and 0 <= (r["starttime"] - segs[-1]["end"]).days <= 30:
                segs[-1]["end"] = r["stoptime"] if pd.notna(r["stoptime"]) else segs[-1]["end"]
                segs[-1]["rows"].append(r)
            else:
                segs.append({"key": key, "start": r["starttime"], "end": r["stoptime"], "rows": [r]})
        for seg in segs:
            rows = seg["rows"]
            if seg["start"] is None or pd.isna(seg["start"]):
                continue
            if seg["end"] is not None and pd.notna(seg["end"]) and seg["end"] < window_start:
                continue
            start_day = to_day_index(seg["start"], window_start, window_end)
            if start_day is None:
                continue
            if start_day == 0:
                start_day = 1
            dose, route = seg["key"]
            ev = "; ".join(sorted({
                f"{r['drug']} {r.get('dose_val_rx') if pd.notna(r.get('dose_val_rx')) else ''}"
                f"{r.get('dose_unit_rx') if pd.notna(r.get('dose_unit_rx')) else ''} {r.get('route') if pd.notna(r.get('route')) else ''}".strip()
                for r in rows}))[:280]
            val = str(rows[0]["drug"]).strip()
            if dose:
                val += f" {dose}"
            if route:
                val += f" {route}"
            if seg["end"] is not None and pd.notna(seg["end"]):
                val += f"，自 {seg['start'].date()} 起使用，末次记录 {seg['end'].date()}"
            else:
                val += f"，自 {seg['start'].date()} 起使用，无停药记录"
            supersedes = latest_fact.get(norm)
            fid = fw.add(
                day_index=start_day,
                date=seg["start"],
                category="用药记录",
                name=f"{norm} 当前方案",
                value=val,
                memory_level="L4",
                state="current",
                source_ref={"table": "prescriptions",
                            "hadm_ids": sorted({int(r["hadm_id"]) for r in rows if pd.notna(r["hadm_id"])})[:10]},
                original_evidence=ev,
                supersedes=supersedes,
            )
            if supersedes:
                prev = next(x for x in fw.rows if x["fact_id"] == supersedes)
                prev["superseded_by"] = fid
                prev["state"] = "superseded"
            latest_fact[norm] = fid


def main():
    ap = argparse.ArgumentParser(
        description="构造步骤 2：Hidden Ledger 构建（90 天密度窗口 + 窗口前病史 + supersession）")
    ap.add_argument("--candidates", required=True,
                    help="候选患者 JSON（candidate_patients_20_v5.json，用户受控）")
    ap.add_argument("--mimic-dir", default=os.environ.get("HEALTHJEV_MIMIC_DIR"),
                    required=os.environ.get("HEALTHJEV_MIMIC_DIR") is None,
                    help="用户授权的 MIMIC-IV 根目录（含 hosp/；也可用环境变量 HEALTHJEV_MIMIC_DIR）")
    ap.add_argument("--out-dir", required=True,
                    help="输出目录（用户受控；产物含 subject_id 与临床事实，不得位于发布树内）")
    ap.add_argument("--top-n", type=int, default=TOP_N, help=f"参与构建的候选数（默认 {TOP_N}）")
    args = ap.parse_args()

    _require_data_libs()

    hosp = Path(args.mimic_dir) / "hosp"
    if not hosp.is_dir():
        raise SystemExit(f"未找到 hosp 目录：{hosp}（--mimic-dir 应指向包含 hosp/ 的 MIMIC-IV 根目录）")
    out_dir = Path(args.out_dir)
    _guard_out_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_out = out_dir / "hidden_ledger_v5.jsonl"
    summary_out = out_dir / "ledger_v5_summary.json"

    t0 = datetime.now()
    with open(args.candidates, encoding="utf-8") as f:
        cand = json.load(f)
    subjects = [p["subject_id"] for p in cand["patients"]][:args.top_n]
    subj_set = set(subjects)
    print(f"[Load] Top {len(subjects)} 患者", flush=True)

    window_map = {}
    for p in cand["patients"]:
        sid = p["subject_id"]
        if sid in subj_set:
            window_map[sid] = (
                pd.Timestamp(p["best_window_start"]).normalize(),
                pd.Timestamp(p["best_window_end"]).normalize(),
            )
    assert len(window_map) == len(subjects), "窗口信息缺失"

    patients = pd.read_csv(hosp / "patients.csv",
                           usecols=["subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group"])
    patients = patients[patients["subject_id"].isin(subj_set)]

    admissions = pd.read_csv(hosp / "admissions.csv",
                             usecols=["subject_id", "hadm_id", "admittime", "dischtime", "admission_type"],
                             parse_dates=["admittime", "dischtime"])
    admissions = admissions[admissions["subject_id"].isin(subj_set)]

    diagnoses = pd.read_csv(hosp / "diagnoses_icd.csv",
                            usecols=["subject_id", "hadm_id", "icd_code", "icd_version", "seq_num"])
    diagnoses = diagnoses[diagnoses["subject_id"].isin(subj_set)]
    d_icd = pd.read_csv(hosp / "d_icd_diagnoses.csv",
                        usecols=["icd_code", "icd_version", "long_title"])
    d_icd["key"] = d_icd["icd_version"].astype(str) + "|" + d_icd["icd_code"].astype(str)
    icd_map = dict(zip(d_icd["key"], d_icd["long_title"]))

    d_pro = pd.read_csv(hosp / "d_icd_procedures.csv",
                        usecols=["icd_code", "icd_version", "long_title"])
    d_pro["key"] = d_pro["icd_version"].astype(str) + "|" + d_pro["icd_code"].astype(str)
    pro_map = dict(zip(d_pro["key"], d_pro["long_title"]))

    procedures = pd.read_csv(hosp / "procedures_icd.csv",
                             usecols=["subject_id", "hadm_id", "chartdate", "icd_code", "icd_version"],
                             parse_dates=["chartdate"])
    procedures = procedures[procedures["subject_id"].isin(subj_set)]

    rx_cols = ["subject_id", "hadm_id", "starttime", "stoptime", "drug", "dose_val_rx", "dose_unit_rx", "route"]
    prescriptions = pd.read_csv(hosp / "prescriptions.csv", usecols=rx_cols)
    prescriptions = prescriptions[prescriptions["subject_id"].isin(subj_set)]
    print(f"[Load] admissions={len(admissions)} dx={len(diagnoses)} rx={len(prescriptions)}", flush=True)

    omr = pd.read_csv(hosp / "omr.csv",
                      usecols=["subject_id", "chartdate", "result_name", "result_value"],
                      parse_dates=["chartdate"])
    omr = omr[omr["subject_id"].isin(subj_set)]
    print(f"[Load] omr={len(omr)}", flush=True)

    per_pat_labs = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    print("[Scan] labevents 白名单窗口内取值扫描 ...", flush=True)
    usecols = ["subject_id", "itemid", "charttime", "valuenum", "valueuom"]
    for i, chunk in enumerate(pd.read_csv(
        hosp / "labevents.csv.gz", usecols=usecols,
        chunksize=10_000_000, dtype={"subject_id": "int64", "itemid": "int64"},
    ), 1):
        chunk = chunk[chunk["subject_id"].isin(subj_set) & chunk["itemid"].isin(LAB_WHITELIST)]
        chunk = chunk.dropna(subset=["charttime", "valuenum"])
        for sid, itemid, ct, vn, uom in chunk.itertuples(index=False):
            d = pd.to_datetime(ct).normalize()
            ws, we = window_map[int(sid)]
            if ws <= d <= we:
                per_pat_labs[int(sid)][d][int(itemid)].append((float(vn), uom))
        print(f"  chunk-{i}", flush=True)

    all_rows = []
    summary = []
    pre_window_summaries = {}
    for sid in subjects:
        ws, we = window_map[sid]
        fw = FactWriter(sid)

        prow = patients[patients["subject_id"] == sid].iloc[0]
        age_at_ws = int(prow["anchor_age"]) + (ws.year - int(prow["anchor_year"]))
        fw.add(0, ws, "人口学", "基本信息",
               f"{'女' if prow['gender'] == 'F' else '男'}性，窗口起始约 {age_at_ws} 岁",
               "L2", "stable", disclosure_rule="proxy",
               source_ref={"table": "patients"},
               original_evidence=f"gender={prow['gender']} anchor_age={prow['anchor_age']}")

        dxp = diagnoses[diagnoses["subject_id"] == sid]
        cond_hits = {}
        for _, r in dxp.iterrows():
            for cond in match_chronic(r["icd_code"], r["icd_version"]):
                cond_hits.setdefault(cond, []).append(r)
        for cond, rows in cond_hits.items():
            fw.add(0, None, "疾病状况", f"慢性病：{cond}",
                   f"病史多年，历次记录反复出现（命中 {len(rows)} 条诊断记录）",
                   "L2", "stable",
                   source_ref={"table": "diagnoses_icd", "n_records": len(rows)},
                   original_evidence="; ".join(sorted({
                       icd_map.get(str(r["icd_version"]) + "|" + str(r["icd_code"]).strip(), "")
                       for r in rows})[:5]))
        dxp2 = dxp.copy()
        dxp2["key"] = dxp2["icd_version"].astype(str) + "|" + dxp2["icd_code"].astype(str).str.strip()
        dxp2["title"] = dxp2["key"].map(icd_map)
        top_dx = dxp2["title"].value_counts().head(10)
        fw.add(0, None, "疾病状况", "既往诊断概要",
               "；".join(f"{k}（{v}次记录）" for k, v in top_dx.items() if k),
               "L2", "stable", source_ref={"table": "diagnoses_icd"})

        admp = admissions[admissions["subject_id"] == sid].sort_values("admittime")
        pre_adm = admp[admp["dischtime"] < ws]
        fw.add(0, None, "疾病状况", "既往住院",
               f"窗口前共 {len(pre_adm)} 次住院",
               "L2", "stable", source_ref={"table": "admissions", "n": int(len(pre_adm))})
        dx1 = dxp2.sort_values("seq_num").drop_duplicates("hadm_id", keep="first").set_index("hadm_id")["title"]
        for _, a in admp.iterrows():
            di = to_day_index(a["admittime"], ws, we)
            if not di or di <= 0:
                continue
            title = dx1.get(a["hadm_id"], "")
            fw.add(di, a["admittime"], "住院事件", "住院",
                   f"{a['admittime'].date()} 入院（{a['admission_type']}），主诊断：{title}，{a['dischtime'].date()} 出院",
                   "L4", "resolved",
                   source_ref={"table": "admissions", "hadm_id": int(a["hadm_id"])},
                   original_evidence=str(title))

        prop = procedures[procedures["subject_id"] == sid]
        pre_ops = prop[prop["chartdate"] < ws]
        if len(pre_ops):
            fw.add(0, None, "手术操作", "既往手术概要",
                   f"窗口前共 {len(pre_ops)} 条手术/操作记录",
                   "L2", "stable", source_ref={"table": "procedures_icd", "n": int(len(pre_ops))})
        for _, p in prop.iterrows():
            di = to_day_index(p["chartdate"], ws, we)
            if not di or di <= 0:
                continue
            name = pro_map.get(str(p["icd_version"]) + "|" + str(p["icd_code"]).strip(), p["icd_code"])
            fw.add(di, p["chartdate"], "手术操作", "操作/手术",
                   str(name), "L4", "resolved",
                   source_ref={"table": "procedures_icd", "hadm_id": int(p["hadm_id"])},
                   original_evidence=f"ICD{p['icd_version']} {p['icd_code']}")

        build_regimen_facts(prescriptions[prescriptions["subject_id"] == sid], fw, ws, we)

        metric_series = defaultdict(list)
        for d in sorted(per_pat_labs.get(sid, {})):
            di = to_day_index(d, ws, we)
            if not di:
                continue
            for itemid, vals in sorted(per_pat_labs[sid][d].items()):
                vn, uom = vals[0]
                name = LAB_WHITELIST[itemid]
                u = f" {uom}" if isinstance(uom, str) and uom.strip() else ""
                fw.add(di, d, "检查结果", name,
                       f"{vn:g}{u}", "L3", "resolved",
                       source_ref={"table": "labevents", "itemid": int(itemid)},
                       original_evidence=f"{d.date()} {name}={vn:g}{u.strip()}")
                metric_series[name].append((d, vn))
        for name, series in metric_series.items():
            if len(series) >= 3:
                vals = [v for _, v in series]
                first, last = series[0][1], series[-1][1]
                direction = "上升" if last > first * 1.1 else ("下降" if last < first * 0.9 else "平稳")
                fw.add(WINDOW_DAYS, we, "长期趋势", f"{name}窗口趋势",
                       f"窗口内 {len(series)} 次测量，范围 {min(vals):g}~{max(vals):g}，"
                       f"首末变化 {first:g}->{last:g}（{direction}）",
                       "L3", "resolved",
                       source_ref={"table": "labevents", "metric": name},
                       original_evidence="; ".join(f"{d.date()}={v:g}" for d, v in series[:20]))

        for _, o in omr[omr["subject_id"] == sid].iterrows():
            name = OMR_WHITELIST.get(str(o["result_name"]).strip())
            if not name:
                continue
            di = to_day_index(o["chartdate"], ws, we)
            if not di or di <= 0:
                continue
            fw.add(di, o["chartdate"], "生理指标", name,
                   str(o["result_value"]), "L3", "resolved",
                   source_ref={"table": "omr"},
                   original_evidence=f"{o['chartdate'].date()} {o['result_name']}={o['result_value']}")

        all_rows.extend(fw.rows)
        n_sup = sum(1 for r in fw.rows if r["state"] == "superseded")

        rxs = prescriptions[prescriptions["subject_id"] == sid].copy()
        rxs["starttime"] = pd.to_datetime(rxs["starttime"], errors="coerce")
        rxs["stoptime"] = pd.to_datetime(rxs["stoptime"], errors="coerce")
        rxs = rxs.dropna(subset=["starttime"])
        active_at_ws = rxs[(rxs["starttime"] < ws) &
                           (rxs["stoptime"].isna() | (rxs["stoptime"] >= ws))]
        active_norms = sorted(active_at_ws["drug"].map(normalize_drug).unique().tolist())
        pre_dx_top = [
            {"title": k, "n_records": int(v)}
            for k, v in dxp2["title"].value_counts().head(15).items() if k
        ]
        pre_window_summaries[sid] = {
            "window": [str(ws.date()), str(we.date())],
            "age_at_window_start": age_at_ws,
            "gender": prow["gender"],
            "chronic_conditions": sorted(cond_hits.keys()),
            "top_pre_window_diagnoses": pre_dx_top,
            "n_pre_window_admissions": int(len(pre_adm)),
            "n_pre_window_procedures": int(len(pre_ops)),
            "medications_active_at_window_start": active_norms[:50],
        }

        summary.append({
            "subject_id": sid,
            "window": [str(ws.date()), str(we.date())],
            "n_facts": len(fw.rows),
            "n_day0": sum(1 for r in fw.rows if r["day_index"] == 0),
            "n_in_window": sum(1 for r in fw.rows if r["day_index"] >= 1),
            "n_superseded": n_sup,
            "by_category": pd.Series([r["category"] for r in fw.rows]).value_counts().to_dict(),
            "by_memory_level": pd.Series([r["memory_level"] for r in fw.rows]).value_counts().to_dict(),
        })
        print(f"  {sid}: {len(fw.rows)} facts (day0={sum(1 for r in fw.rows if r['day_index']==0)}, "
              f"superseded={n_sup}), window {ws.date()}~{we.date()}", flush=True)

    for r in all_rows:
        if r["day_index"] >= 1 and r["date"]:
            ws, we = window_map[r["subject_id"]]
            assert pd.Timestamp(r["date"]) <= we, f"future leak: {r['fact_id']} {r['date']} > {we}"

    with open(ledger_out, "w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    meta = {
        "generated_at": datetime.now().isoformat(),
        "design_decisions": [
            "window = per-patient frozen best-density 90-day window (from screening step)",
            "day_index 1..90; pre-window -> day 0 (Day0 archive material); post-window events strictly EXCLUDED (no clamping) to prevent future leakage",
            "pre-window summary (diagnoses/procedures/admissions/active meds) written for Day0 archive injection",
            "MIMIC shifted calendar preserved; dialogue step maps to relative day labels",
            "allergy facts -> synthetic disclosure (MIMIC-IV has no allergy table)",
            "medication supersession: same normalized drug, dose/route change or >30d gap -> new regimen fact supersedes old",
        ],
        "patients": summary,
        "pre_window_summaries": pre_window_summaries,
        "total_facts": len(all_rows),
    }
    with open(summary_out, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"[Done] {len(all_rows)} facts -> {ledger_out}，用时 {datetime.now() - t0}", flush=True)


if __name__ == "__main__":
    main()
