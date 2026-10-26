# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_DAYS = 90
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_NORM_RE = re.compile(r"[\s，。、；：！？,.;:!?\-—_/()（）\[\]【】\"'“”‘’]+")


def _norm(text: str) -> str:
    return _NORM_RE.sub("", str(text)).lower()


def audit_user(ug: dict, traps: list, n_days: int) -> list:
    errs = []
    sid = ug["subject_id"]
    days = ug["days"]
    traps_by_day = defaultdict(list)
    for t in traps:
        traps_by_day[t["day"]].append(t)

    if len(days) != n_days:
        errs.append(f"[A1] [{sid}] days={len(days)} != {n_days}")
    hook_total = cue_total = 0
    for r in days:
        d = r["day"]
        if not (2 <= len(r["goals"]) <= 3):
            errs.append(f"[A1] [{sid}] D{d} goals={len(r['goals'])} 不在 2~3")
        script = " ".join(r.get("disclosure_script", []))
        contents = " ".join(g["content"] for g in r["goals"])
        for g in r["goals"]:
            if not g.get("content") or not g.get("expected_behavior") or not g.get("end_condition"):
                errs.append(f"[A1] [{sid}] D{d} {g.get('goal_id')} 字段缺失")
        if "【" in script or "】" in script or re.search(r"^\s*[-•*]", script, re.M):
            errs.append(f"[A4] [{sid}] D{d} disclosure_script 含单据块/列表")
        for g in r["goals"]:
            for c in g.get("cross_refs", []):
                hook_total += 1
                cue_total += 1 if c.get("cue") else 0
                if not c.get("cue"):
                    q = _norm(c.get("quote", ""))
                    body = _norm(g["content"])
                    if len(q) >= 8 and q[:12] in body:
                        errs.append(f"[A5] [{sid}] D{d} proactive 钩子 content 泄漏 quote")
                if "supersession" in c.get("note", ""):
                    nums = set(NUM_RE.findall(c.get("quote", "")))
                    content_nums = set(NUM_RE.findall(g["content"]))
                    leak = nums & content_nums
                    if leak:
                        errs.append(f"[A5] [{sid}] D{d} supersession content 泄漏答案值 {leak}")
        for t in traps_by_day.get(d, []):
            blob = _norm(contents + " " + script)
            if t["type"] in ("must_not_store", "must_store", "auth_decl"):
                if _norm(t["signature"]) not in blob:
                    errs.append(f"[A6] [{sid}] D{d} trap {t['trap_id']} 台词不在场（signature 缺失）")
            elif t["type"] == "wrong_value":
                said_n = _norm(str(t.get("said_value", "")))
                true_num_m = re.match(r"^[\d.]+", str(t.get("true_value", "")))
                true_n = _norm(true_num_m.group(0)) if true_num_m else ""
                if not said_n or said_n not in blob:
                    errs.append(f"[A6] [{sid}] D{d} wrong_value {t['trap_id']} said_value {t.get('said_value')} 不在台词")
                if not true_n or true_n not in blob:
                    errs.append(f"[A6] [{sid}] D{d} wrong_value {t['trap_id']} true_value 不在台词")
    if hook_total < int(n_days * 0.8):
        errs.append(f"[A2] [{sid}] hooks={hook_total} < {int(n_days * 0.8)}")
    if hook_total and cue_total / hook_total < 0.5:
        errs.append(f"[A2] [{sid}] cued_ratio={cue_total / hook_total:.2f} < 0.5")
    tc = defaultdict(int)
    for t in traps:
        tc[t["type"]] += 1
        if not t.get("trap_id"):
            errs.append(f"[A3] [{sid}] trap 缺 trap_id: {t}")
    scale = max(1, round(n_days / 30))
    need = {k: v * scale for k, v in
            {"must_not_store": 2, "must_store": 2, "auth_decl": 1, "wrong_value": 2}.items()}
    for k, v in need.items():
        if tc[k] < v:
            errs.append(f"[A3] [{sid}] traps[{k}]={tc[k]} < {v}")
    return errs


def verify_integrity(manifest_path: Path, root: Path):
    problems = []
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        return [f"[INT] {manifest_path}: manifest 缺少非空 files 表"], 0
    for path, rec in files.items():
        sha = rec.get("sha256") if isinstance(rec, dict) else rec
        nbytes = rec.get("bytes") if isinstance(rec, dict) else None
        p = Path(path)
        target = p if p.is_absolute() else (root / p)
        if not target.is_file():
            problems.append(f"[INT] missing: {path}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if sha and actual != sha:
            problems.append(f"[INT] sha256 mismatch: {path}")
        if nbytes is not None and target.stat().st_size != nbytes:
            problems.append(f"[INT] size mismatch: {path} ({target.stat().st_size} != {nbytes})")
    return problems, len(files)


def main():
    ap = argparse.ArgumentParser(
        description="goals 资产校验（A1~A6，如实报告不假 PASS）+ 可选 SHA256 冻结与完整性核验")
    ap.add_argument("--goals-dir",
                    help="goals 目录（内含每用户 <subject_id>.json；构造产物或发布候选均可）")
    ap.add_argument("--traps",
                    help="陷阱注册表路径（构造 traps_v7.json 或发布 governance_probes.json，"
                         "格式均为 {\"traps\": [...]}）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="每用户天数（默认 90）")
    ap.add_argument("--freeze", action="store_true",
                    help="审计通过后把 SHA256 清单写入 --manifest（路径必须显式给出）")
    ap.add_argument("--manifest",
                    help="--freeze 的清单输出路径；--verify-integrity 的清单输入路径（可同时使用）")
    ap.add_argument("--verify-integrity", metavar="MANIFEST",
                    help="独立完整性核验：按清单核验文件 SHA256/字节数（不做内容审计豁免）")
    ap.add_argument("--root", type=Path,
                    help="完整性核验时相对清单路径的解析根（默认：清单所在目录）")
    args = ap.parse_args()

    goals_dir = Path(args.goals_dir) if args.goals_dir else None
    traps_file = Path(args.traps) if args.traps else None
    if goals_dir is None and not args.verify_integrity:
        ap.error("需要 --goals-dir 与 --traps 做内容审计；或仅做完整性核验：--verify-integrity MANIFEST")
    if (goals_dir is None) != (traps_file is None):
        ap.error("--goals-dir 与 --traps 必须成对提供")
    if args.freeze and goals_dir is None:
        ap.error("--freeze 需要同时提供 --goals-dir 与 --traps")
    if goals_dir is not None and not goals_dir.is_dir():
        raise SystemExit(f"goals 目录不存在: {goals_dir}")
    if traps_file is not None and not traps_file.is_file():
        raise SystemExit(f"陷阱注册表不存在: {traps_file}")
    if args.freeze and not args.manifest:
        raise SystemExit("--freeze 需要显式 --manifest <输出路径>（不默认写任何位置）")

    audit_errs = []
    audited = []
    n_files = 0
    if goals_dir is not None:
        traps_all = json.loads(traps_file.read_text(encoding="utf-8"))["traps"]
        traps_by_sid = defaultdict(list)
        for t in traps_all:
            traps_by_sid[t["subject_id"]].append(t)

        audited = [traps_file]
        for gp in sorted(goals_dir.glob("*.json")):
            ug = json.loads(gp.read_text(encoding="utf-8"))
            errs = audit_user(ug, traps_by_sid.get(ug["subject_id"], []), args.days)
            audit_errs.extend(errs)
            audited.append(gp)
        n_files = len(list(goals_dir.glob("*.json")))

        summary = Counter(e.split("]")[0] + "]" for e in audit_errs)
        if audit_errs:
            print(f"AUDIT FAIL（{n_files} 个 goals 文件，--days {args.days}）：")
            for e in audit_errs:
                print("  -", e)
            print("缺口汇总（按校验项）:")
            for code, n in sorted(summary.items()):
                print(f"    {code} {n} 项")
            print("说明：以上为资产的真实缺口（含已知问题），本工具不提供豁免；"
                  "已知问题随发布清单（manifest.known_issues）一并如实披露。")
        else:
            print(f"AUDIT PASS（{n_files} 个 goals 文件，--days {args.days}）")

    integrity_errs = []
    n_checked = 0
    if args.verify_integrity:
        manifest_path = Path(args.verify_integrity)
        if not manifest_path.is_file():
            raise SystemExit(f"完整性清单不存在: {manifest_path}")
        root = args.root if args.root else manifest_path.parent
        integrity_errs, n_checked = verify_integrity(manifest_path, root)
        if integrity_errs:
            print(f"INTEGRITY FAIL（清单 {manifest_path}，root={root}）:")
            for e in integrity_errs:
                print("  -", e)
        else:
            print(f"INTEGRITY PASS（清单 {manifest_path}，{n_checked} 个文件）")

    freeze_done = False
    if args.freeze and not audit_errs:
        manifest_out = Path(args.manifest)
        if manifest_out.exists():
            raise SystemExit(f"清单已存在，不覆盖: {manifest_out}")
        manifest_root = manifest_out.resolve().parent
        files = {}
        for p in audited:
            rp = Path(p).resolve()
            try:
                rel = str(rp.relative_to(manifest_root))
            except ValueError:
                rel = str(rp)
            files[rel] = hashlib.sha256(rp.read_bytes()).hexdigest()
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        manifest_out.write_text(json.dumps({
            "frozen_at": datetime.now().isoformat(timespec="seconds"),
            "days_per_user": args.days,
            "note": ("goals 冻结清单（SHA256；路径相对清单文件所在目录记录）。"
                     "真值引用（只读，不复制）：事件账本/用户画像/Day0 档案等受控资产"
                     "由用户自管，不随代码分发"),
            "files": files,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"FROZEN -> {manifest_out} ({len(files)} files)")
        freeze_done = True
    elif args.freeze and audit_errs:
        print("存在审计缺口，拒绝 --freeze（不得为带已知问题的资产出具冻结 PASS）。")

    rc = (1 if audit_errs else 0) | (2 if integrity_errs else 0)
    sys.exit(rc)


if __name__ == "__main__":
    main()
