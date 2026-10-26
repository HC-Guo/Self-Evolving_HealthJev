# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_NORM_RE = re.compile(r"[\s，。、；：！？,.;:!?\-—_/()（）\[\]【】\"'“”‘’]+")


def _norm(t: str) -> str:
    return _NORM_RE.sub("", str(t)).lower()


def _guard_output_dir(out_dir: Path):
    out_dir = out_dir.resolve()
    if out_dir == PROJECT_ROOT or PROJECT_ROOT in out_dir.parents:
        raise SystemExit(
            f"refusing to write inside the release tree: {out_dir}\n"
            "构造产物含源 subject_id 与临床内容，请指向发布树之外的用户受控输出目录。")
    if any(out_dir.glob("FROZEN_MANIFEST*.json")):
        raise SystemExit(
            f"refusing to touch frozen data: {out_dir} 含 FROZEN_MANIFEST*.json。\n"
            "repair 仅作用于新生成的构造输出；冻结资产保持只读。")


def main():
    ap = argparse.ArgumentParser(
        description="陷阱台词确定性修复（generation-only；需显式 --output-dir，不触碰冻结数据）")
    ap.add_argument("--output-dir", required=True,
                    help="构造输出目录（内含 goals_v7/*.json 与 traps_v7.json，由 generate 产出）")
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    _guard_output_dir(out_dir)
    goals_dir = out_dir / "goals_v7"
    traps_file = out_dir / "traps_v7.json"
    if not goals_dir.is_dir() or not traps_file.is_file():
        raise SystemExit(f"构造输出不完整：需要 {goals_dir} 与 {traps_file}（请先运行 generate）")

    traps_all = json.loads(traps_file.read_text(encoding="utf-8"))["traps"]
    traps_by_sid = defaultdict(list)
    for t in traps_all:
        traps_by_sid[t["subject_id"]].append(t)

    n_patched = 0
    for gp in sorted(goals_dir.glob("*.json")):
        ug = json.loads(gp.read_text(encoding="utf-8"))
        sid = ug["subject_id"]
        days = {r["day"]: r for r in ug["days"]}
        for t in traps_by_sid.get(sid, []):
            row = days.get(t["day"])
            if row is None:
                continue
            script = row.get("disclosure_script") or []
            contents = " ".join(g["content"] for g in row["goals"])
            blob = _norm(contents + " " + " ".join(script))
            if t["type"] in ("must_not_store", "must_store", "auth_decl"):
                if _norm(t["signature"]) in blob:
                    continue
                idx = next((i for i, g in enumerate(row["goals"]) if g["goal_type"] == "chat"),
                           len(row["goals"]) - 1)
                g = row["goals"][idx]
                g["content"] = (g["content"].rstrip("。 ") + "。 " + t["sentence"])[:400]
                n_patched += 1
            elif t["type"] == "wrong_value":
                said = _norm(str(t.get("said_value", "")))
                tm = re.match(r"^([\d.]+)", str(t.get("true_value", "")))
                true_num = _norm(tm.group(1)) if tm else ""
                need_said = not said or said not in blob
                need_true = not true_num or true_num not in blob
                if not (need_said or need_true):
                    continue
                item = t.get("item", "指标")
                true_disp = str(t.get("true_value", "")).strip()
                frag = (f"今天复查结果出来了，医生说{item}是{true_disp}。"
                        if need_true else "")
                if need_said:
                    frag += f"不过我记得之前好像是{str(t.get('said_value', ''))}来着，是不是我记错了？你帮我记一下呗。"
                script = (script + [frag[:450]]) if frag else script
                row["disclosure_script"] = script
                n_patched += 1
        gp.write_text(json.dumps(ug, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[repair] patched {n_patched} trap placements (output-dir={out_dir})")


if __name__ == "__main__":
    main()
