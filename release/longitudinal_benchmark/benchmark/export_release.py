# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PUBLISH_ROOT = Path(__file__).resolve().parents[1]
N_USERS = 20

GOAL_ID_RE = re.compile(r"^G(\d+)-D(\d+)-(\d+)$")
FACT_ID_RE = re.compile(r"^F(\d+)-(\d{5})$")
TRAP_ID_RE = re.compile(r"^T(\d+)-(\d{2})$")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_NORM_RE = re.compile(r"[\s，。、；：！？,.;:!?\-—_/()（）\[\]【】\"'“”‘’]+")


def _norm(text: str) -> str:
    return _NORM_RE.sub("", str(text)).lower()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _dump_text(obj, indent=1) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent)


def collect_source_ids(goals_dir: Path):
    stems = []
    for fp in sorted(goals_dir.glob("*.json")):
        if not re.fullmatch(r"\d{8}", fp.stem):
            raise SystemExit(f"源 goals 文件名必须是 8 位 subject_id：{fp.name}")
        stems.append(int(fp.stem))
    if len(stems) != N_USERS:
        raise SystemExit(f"源 goals 目录必须恰好 {N_USERS} 个用户文件，实际 {len(stems)}：{goals_dir}")
    if len(set(stems)) != len(stems):
        raise SystemExit("源 goals 目录存在重复 subject_id")
    return sorted(stems)


def build_id_map(source_ids):
    public_ids = list(range(1, N_USERS + 1))
    secrets.SystemRandom().shuffle(public_ids)
    return dict(zip(source_ids, public_ids))


def remap_goal_id(goal_id, sid, pid):
    m = GOAL_ID_RE.match(str(goal_id))
    if not m or int(m.group(1)) != sid:
        raise SystemExit(f"goal_id 与源 subject_id 不一致: {goal_id} (期望前缀 G{sid}-)")
    return f"G{pid}-D{m.group(2)}-{m.group(3)}"


def remap_fact_id(fact_id, sid, pid):
    m = FACT_ID_RE.match(str(fact_id))
    if not m or int(m.group(1)) != sid:
        raise SystemExit(f"fact_id 与源 subject_id 不一致: {fact_id} (期望前缀 F{sid}-)")
    return f"F{pid}-{m.group(2)}"


def remap_trap_id(trap_id, sid, pid):
    m = TRAP_ID_RE.match(str(trap_id))
    if not m or int(m.group(1)) != sid:
        raise SystemExit(f"trap_id 与源 subject_id 不一致: {trap_id} (期望前缀 T{sid}-)")
    return f"T{pid}-{m.group(2)}"


def remap_goals(ug, sid, pid):
    if int(ug["subject_id"]) != sid:
        raise SystemExit(f"goals subject_id 不一致: {ug['subject_id']} != {sid}")
    out = {"subject_id": pid, "days": [], "privacy_days": ug.get("privacy_days", [])}
    for r in ug["days"]:
        if int(r["subject_id"]) != sid:
            raise SystemExit(f"day row subject_id 不一致: D{r['day']}")
        row = dict(r)
        row["subject_id"] = pid
        row["facts"] = [dict(f, fact_id=remap_fact_id(f["fact_id"], sid, pid))
                        for f in row.get("facts", [])]
        row["goals"] = [dict(g, goal_id=remap_goal_id(g["goal_id"], sid, pid))
                        for g in row["goals"]]
        if row.get("privacy"):
            row["privacy"] = dict(row["privacy"],
                                  trap_ids=[remap_trap_id(t, sid, pid)
                                            for t in row["privacy"].get("trap_ids", [])])
        out["days"].append(row)
    days = [int(r["day"]) for r in out["days"]]
    if days != list(range(1, len(days) + 1)):
        raise SystemExit(f"公开用户 {pid} 的 days 必须从 1 起连续（assets 契约），实际: "
                         f"{days[:5]}...{days[-3:] if len(days) > 5 else ''}")
    return out


def remap_traps(traps, sid, pid):
    out = []
    for t in traps:
        if int(t["subject_id"]) != sid:
            raise SystemExit(f"trap subject_id 不一致: {t.get('trap_id')} (期望 {sid})")
        out.append(dict(t, subject_id=pid, trap_id=remap_trap_id(t["trap_id"], sid, pid)))
    return out


def safe_profile(profile, sid, pid):
    demo = profile.get("demo")
    persona = profile.get("persona")
    clinical = profile.get("clinical") or {}
    if not isinstance(demo, dict) or not demo:
        raise SystemExit(f"源画像缺 demo（sid={sid}），无法安全渲染")
    if not isinstance(persona, dict) or not persona:
        raise SystemExit(f"源画像缺 persona（sid={sid}），无法安全渲染")
    conditions = clinical.get("conditions", [])
    allergies = clinical.get("allergies", [])
    if not isinstance(conditions, list) or not isinstance(allergies, list):
        raise SystemExit(f"源画像 clinical 白名单字段类型异常（sid={sid}）")
    return {
        "subject_id": pid,
        "demo": dict(demo),
        "persona": dict(persona),
        "clinical": {"conditions": conditions, "allergies": allergies},
    }


def scan_known_issues(goals_by_pid, traps_by_pid):
    users_missing_traps = 0
    proactive_leaks = 0
    supersession_leaks = 0
    signature_missing = 0
    for pid, ug in goals_by_pid.items():
        traps = traps_by_pid.get(pid, [])
        if not traps:
            users_missing_traps += 1
        traps_by_day = defaultdict(list)
        for t in traps:
            traps_by_day[t["day"]].append(t)
        for r in ug["days"]:
            script = " ".join(r.get("disclosure_script", []))
            contents = " ".join(g["content"] for g in r["goals"])
            blob = _norm(contents + " " + script)
            for g in r["goals"]:
                for c in g.get("cross_refs", []):
                    if not c.get("cue"):
                        q = _norm(c.get("quote", ""))
                        if len(q) >= 8 and q[:12] in _norm(g["content"]):
                            proactive_leaks += 1
                    if "supersession" in c.get("note", ""):
                        nums = set(NUM_RE.findall(c.get("quote", "")))
                        if nums & set(NUM_RE.findall(g["content"])):
                            supersession_leaks += 1
            for t in traps_by_day.get(r["day"], []):
                if t["type"] in ("must_not_store", "must_store", "auth_decl"):
                    if _norm(t.get("signature", "")) not in blob:
                        signature_missing += 1
    return {
        "missing_traps": {
            "users_without_registered_traps": users_missing_traps,
            "detail": "部分用户在陷阱注册表中无任何登记陷阱（构造批次历史遗留）；"
                      "这些用户的治理类考察以日级 privacy 标注为准。",
        },
        "occasional_cue_leak": {
            "proactive_quote_leaks": proactive_leaks,
            "supersession_value_leaks": supersession_leaks,
            "trap_signature_missing": signature_missing,
            "detail": "少量 proactive 钩子台词含历史 quote、supersession 台词含答案数值，"
                      "或陷阱签名词未落在当日台词；属基准内容层面的问题，对所有被评测系统对称，"
                      "不构成对任一系统的偏置。",
        },
    }


def main():
    ap = argparse.ArgumentParser(
        description="发布资产匿名化导出（受控 MIMIC 派生数据 → 匿名候选；不做任何上传）")
    ap.add_argument("--source-goals-dir", required=True,
                    help="源 goals 目录（冻结构造产物 goals_v7/，内含 <subject_id>.json ×20，用户受控）")
    ap.add_argument("--source-probes", required=True,
                    help="源陷阱注册表（traps_v7.json，{\"traps\": [...]}，用户受控）")
    ap.add_argument("--source-profiles", required=True,
                    help="源用户画像（user_profiles_v5.json，{\"profiles\": [...]}，用户受控）")
    ap.add_argument("--out", required=True,
                    help="导出目录（用户显式提供；若位于发布树内请确保该路径不入发布（如 .release-assets/，"
                         "已由 .gitignore 排除））")
    ap.add_argument("--map-output", required=True,
                    help="公开 ID → 源 ID 映射表输出路径（必须位于发布树之外的外部受控路径；"
                         "含敏感源 ID，绝不随代码发布）")
    ap.add_argument("--publish-root", type=Path, default=PUBLISH_ROOT,
                    help="发布树根目录（默认本仓库根；仅用于守卫检查，可覆盖）")
    ap.add_argument("--acknowledge-controlled-data", action="store_true",
                    help="确认了解导出产物为 MIMIC 派生的受控数据（该确认不构成许可或授权）")
    ap.add_argument("--overwrite", action="store_true",
                    help="允许覆盖已存在的导出目标文件（默认拒绝覆盖）")
    args = ap.parse_args()

    if not args.acknowledge_controlled_data:
        print("拒绝执行：本工具导出的是 MIMIC 派生的受控数据。\n"
              "如你已获授权在受控环境操作该数据，请显式追加 --acknowledge-controlled-data。\n"
              "注意：该确认仅表示你了解数据的受控性质，不构成任何许可、授权或再分发权利；"
              "对外发布前必须完成相应数据许可/协议。", file=sys.stderr)
        sys.exit(2)
    print("[controlled-data] 提醒：导出产物为 MIMIC 派生数据（受控）；--acknowledge-controlled-data "
          "不等于许可或授权；对外发布前需完成数据许可确认。本工具不进行任何上传。", flush=True)

    publish_root = args.publish_root
    source_goals_dir = Path(args.source_goals_dir)
    source_probes = Path(args.source_probes)
    source_profiles = Path(args.source_profiles)
    out_dir = Path(args.out)
    map_output = Path(args.map_output)

    if _inside(map_output, publish_root):
        raise SystemExit(f"拒绝：--map-output 必须位于发布树之外的外部受控路径（当前在发布树内："
                         f"{map_output}；发布根 = {publish_root}）")
    existing = [p for p in [out_dir / "governance_probes.json", out_dir / "personas.json",
                            out_dir / "manifest.json", map_output]
                if p.exists()]
    existing += list((out_dir / "goals").glob("*.json")) if (out_dir / "goals").is_dir() else []
    if existing and not args.overwrite:
        raise SystemExit(f"目标已存在（{len(existing)} 个文件），默认不覆盖；请换新目录或显式 --overwrite。"
                         f"\n示例: {sorted(str(p) for p in existing)[:3]}")
    if _inside(out_dir, publish_root):
        print(f"[warn] --out 位于发布树内（{out_dir}）：请确保该路径不进入对外发布"
              f"（如使用 .release-assets/ 等已被 .gitignore 排除的目录），"
              "或待数据许可确认后再发布。", flush=True)

    source_ids = collect_source_ids(source_goals_dir)
    id_map = build_id_map(source_ids)
    print(f"[map] {N_USERS} 个源 subject_id → 公开 ID 1..{N_USERS}（secrets.SystemRandom 随机双射）",
          flush=True)

    goals_by_pid = {}
    for sid in source_ids:
        pid = id_map[sid]
        ug = _load_json(source_goals_dir / f"{sid}.json")
        goals_by_pid[pid] = remap_goals(ug, sid, pid)

    probes = _load_json(source_probes)
    source_traps = probes["traps"] if isinstance(probes, dict) else probes
    traps_by_sid = defaultdict(list)
    for t in source_traps:
        traps_by_sid[int(t["subject_id"])].append(t)
    unknown = set(traps_by_sid) - set(source_ids)
    if unknown:
        raise SystemExit(f"陷阱注册表含有 goals 之外的 subject_id：{sorted(unknown)}")
    traps_by_pid = {}
    for sid in source_ids:
        traps_by_pid[id_map[sid]] = remap_traps(traps_by_sid.get(sid, []), sid, id_map[sid])

    profiles_doc = _load_json(source_profiles)
    profiles_rows = profiles_doc["profiles"] if isinstance(profiles_doc, dict) else profiles_doc
    profile_by_sid = {}
    for p in profiles_rows:
        sid = int(p["subject_id"])
        if sid in profile_by_sid:
            raise SystemExit(f"源画像存在重复 subject_id: {sid}")
        profile_by_sid[sid] = p
    missing_profiles = set(source_ids) - set(profile_by_sid)
    if missing_profiles:
        raise SystemExit(f"源画像缺失 {len(missing_profiles)} 个 subject_id 的画像，无法导出")
    personas = [safe_profile(profile_by_sid[sid], sid, id_map[sid]) for sid in source_ids]
    personas.sort(key=lambda p: p["subject_id"])

    n_days = sum(len(ug["days"]) for ug in goals_by_pid.values())
    n_goals = sum(len(r["goals"]) for ug in goals_by_pid.values() for r in ug["days"])
    known_issues = scan_known_issues(goals_by_pid, traps_by_pid)

    texts = {}
    for pid, ug in goals_by_pid.items():
        texts[f"goals/{pid}.json"] = _dump_text(ug, indent=1)
    probes_out = {
        "note": "治理探针登记表（judge/runner 审计真值；不进 UserSim 视图）",
        "traps": [t for pid in sorted(traps_by_pid) for t in traps_by_pid[pid]],
    }
    texts["governance_probes.json"] = _dump_text(probes_out, indent=1)
    texts["personas.json"] = _dump_text({"profiles": personas}, indent=2)
    for rel, text in texts.items():
        for sid in source_ids:
            if str(sid) in text:
                raise SystemExit(f"源 ID 泄漏：{rel} 中检出源 subject_id 字符串（匿名化不完整，已中止导出）")
    print("[scan] 源 ID 泄漏扫描通过（全部导出文件未检出源 subject_id）", flush=True)

    files = {}
    (out_dir / "goals").mkdir(parents=True, exist_ok=True)
    for rel, text in texts.items():
        path = out_dir / rel
        data = text.encode("utf-8")
        path.write_bytes(data)
        files[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    manifest = {
        "version": "release-1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_ids": sorted(goals_by_pid),
        "counts": {"users": len(goals_by_pid), "days": n_days, "goals": n_goals},
        "files": files,
        "known_issues": known_issues,
        "anonymization": {
            "method": "source subject_id -> public id 1..20 random bijection (secrets.SystemRandom)",
            "rewritten_fields": "subject_id and G/F/T id prefixes only; clinical values and dialogue text unchanged",
            "id_mapping": "public->source mapping stored OUTSIDE the release at a user-controlled path",
        },
        "notes": [
            "数据为 MIMIC 派生（受控）；本 manifest 与导出不构成许可或再分发授权。",
            "personas 为安全白名单渲染（demo/persona/clinical{conditions,allergies}），非完整源画像。",
        ],
    }
    (out_dir / "manifest.json").write_text(_dump_text(manifest, indent=2), encoding="utf-8")

    map_doc = {str(pid): sid for sid, pid in id_map.items()}
    map_output.parent.mkdir(parents=True, exist_ok=True)
    map_output.write_text(json.dumps(map_doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[out] {out_dir}（goals ×{len(goals_by_pid)} + governance_probes + personas + manifest）",
          flush=True)
    print(f"[counts] users={len(goals_by_pid)} days={n_days} goals={n_goals}", flush=True)
    print(f"[known-issues] 缺陷阱用户 {known_issues['missing_traps']['users_without_registered_traps']}"
          f"；proactive 泄题 {known_issues['occasional_cue_leak']['proactive_quote_leaks']}"
          f"；supersession 泄题 {known_issues['occasional_cue_leak']['supersession_value_leaks']}"
          f"（已如实写入 manifest）", flush=True)
    print(f"[map] 公开 ID → 源 ID 映射已写入外部受控路径：{map_output}（不得随代码/资产发布）", flush=True)


if __name__ == "__main__":
    main()
