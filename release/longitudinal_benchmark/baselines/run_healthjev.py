# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from healthclaw.llm_client import make_doubao_client
from healthclaw.memory import FiveLayerMemory
from healthclaw.agent import HealthClawAgent
from healthclaw.tools import ToolBox
from healthclaw.write_guard import WriteGuard
from healthclaw import distill as distill_mod
from usersim.usersim_v7 import UserSimV7, persona_text
from judge.checks import check_value_mismatch, check_traps

from benchmark.assets import load_user_assets

DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs" / "healthjev"

MAX_TURNS_PER_DAY = 20
NORM_RE = re.compile(r"[\s，。、；：！？,.;:!?\-—_/()（）\[\]【】\"'“”‘’]+")


def _norm(text: str) -> str:
    return NORM_RE.sub("", str(text)).lower()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def day0_summary_text(archive: dict, profile: dict) -> str:
    if archive.get("asset_mode") == "release-mode":
        return "（release-mode：未提供 Day0 档案；goals-only 真值，不代表完整账本复现）"
    lines = [f"接入时间：{archive.get('window_start', '')}",
             f"慢病：{'、'.join(profile['clinical'].get('conditions', []))}",
             "过敏：" + "; ".join(f"{a['trigger']}（{a['reaction']}）"
                                  for a in profile["clinical"].get("allergies", []))]
    meds = archive.get("medications_at_window_start") or []
    if meds:
        lines.append("接入时用药：" + "；".join(str(m) for m in meds[:15]))
    diag = archive.get("top_pre_window_diagnoses") or []
    if diag:
        lines.append("既往诊断概要：" + "；".join(str(d.get("title", "")) for d in diag[:8]))
    base = archive.get("baseline_checkups") or []
    if base:
        lines.append("基线化验（Day0 档案，数值为接入时快照）：\n" + "\n".join(
            f"- {c['name']}: {c['value']}（{c.get('date', '')}）" for c in base[:18]))
    return "\n".join(lines)


def snapshot_memory_text(memory: FiveLayerMemory) -> str:
    parts = [json.dumps(memory.l2, ensure_ascii=False),
             json.dumps(memory.l3, ensure_ascii=False)]
    for ep in sorted(memory.l4_dir.glob("D*.json"))[-5:]:
        parts.append(ep.read_text(encoding="utf-8"))
    for c in memory.index.chunks[-80:]:
        parts.append(c["text"])
    return "\n".join(parts)[:60000]


def run_user(user_id: int, run_id: str, doubao_rpm: int, limit_days=None,
             resume: bool = False, runs_root=None, no_day0: bool = True,
             doubao_app_id: str = None, controlled_assets=None) -> dict:
    profile, archive, user_goals, user_traps, day_facts = load_user_assets(
        user_id, controlled_assets=controlled_assets, require_day0=not no_day0)
    run_root = (Path(runs_root) if runs_root else DEFAULT_RUNS_ROOT) / run_id
    user_dir = run_root / str(user_id)
    days_dir = user_dir / "days"
    mem_dir = user_dir / "memory"
    traj_dir = user_dir / "trajectories"
    for d in (days_dir, mem_dir, traj_dir):
        d.mkdir(parents=True, exist_ok=True)

    progress_path = user_dir / "day_progress.json"
    done_days = []
    if resume and progress_path.exists():
        try:
            done_days = json.loads(progress_path.read_text(encoding="utf-8")).get("days", [])
        except Exception:
            done_days = []

    d0_summary = day0_summary_text(archive, profile)

    user_text_cum = ""
    for _dp in sorted(days_dir.glob(f"{user_id}_D*.json")):
        try:
            _old = json.loads(_dp.read_text(encoding="utf-8"))
            user_text_cum += "\n" + "\n".join(
                m.get("content", "") for m in _old.get("transcript", [])
                if m.get("role") == "user")
        except Exception:
            pass

    log_path = traj_dir / "run.log"
    log_f = log_path.open("a", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        log_f.write(msg + "\n")
        log_f.flush()

    channel = "doubao_hc3" if doubao_app_id else None
    client = make_doubao_client(rpm=doubao_rpm, app_id=doubao_app_id, channel=channel)
    web_client = make_doubao_client(rpm=doubao_rpm, app_id=doubao_app_id, channel=channel)
    if not resume:
        import shutil
        if mem_dir.exists():
            shutil.rmtree(mem_dir)
        mem_dir.mkdir(parents=True, exist_ok=True)
    memory = FiveLayerMemory(client, mem_dir)
    if not no_day0:
        memory.seed_from_day0(archive, profile)
    else:
        log(f"[day0] skipped (--no-day0): fresh memory, same starting point as baselines")

    sim = UserSimV7(profile, user_goals, client,
                    state_path=user_dir / "usersim_state" / f"{user_id}.json")
    agent = HealthClawAgent(memory, ToolBox(memory, client, web_client), client)
    toolbox = agent.toolbox

    total_days = limit_days or len(user_goals["days"])
    ws = archive.get("window_start", "")
    n_errors = 0
    log(f"[start] user={user_id} run_id={run_id} fresh={not resume} "
        f"days={total_days} done={done_days} memory_stats={memory.stats()}")

    for day in range(1, total_days + 1):
        if day in done_days:
            continue
        day_start = time.time()
        try:
            day_row = user_goals["days"][day - 1]
            goals = day_row.get("goals", [])
            sim.goal_states = {g["goal_id"]: "pending" for g in goals}
            sim.save_state(day)
            hooks = [c for g in goals for c in g.get("cross_refs", [])]
            hook_entities = []
            for c in hooks:
                m = re.match(r"^[^\s（(：:]+", str(c.get("quote", "")))
                if m:
                    hook_entities.append(m.group(0))
            agent.start_day(day, hook_day=bool(hooks), hook_entities=hook_entities)

            guard_sources = [{"name": f.get("name", ""), "value": str(f.get("value", "")),
                              "source": "goals_fact"} for f in day_facts.get(day, [])]
            for k, v in (day_row.get("self_monitor_values") or {}).items():
                guard_sources.append({"name": k, "value": str(v), "source": "self_monitor"})
            toolbox.set_guard(WriteGuard(guard_sources))
            toolbox.log = []
            toolbox.recalled_texts = []
            toolbox.n_recalls = 0

            transcript = []
            opener = sim.opener(day)
            transcript.append({"role": "user", "content": opener})
            followup = sim.followup_disclosure(day)

            end_day = False
            turn = 0
            sim_out = {"disclosed": ""}
            disclosed_notes: list = []
            while turn < MAX_TURNS_PER_DAY:
                turn += 1
                user_msg = transcript[-1]["content"]
                agent_response = agent.respond(user_msg)
                transcript.append({"role": "assistant", "content": agent_response})
                if turn == 1 and followup:
                    transcript.append({"role": "user", "content": followup})
                    continue
                sim_out = sim.reply(day, agent_response, transcript, turn)
                for gid, st in sim_out.get("goal_updates", []):
                    sim.goal_states[gid] = st
                if sim_out.get("disclosed"):
                    disclosed_notes.append(sim_out["disclosed"])
                if sim_out.get("end_day") or not sim_out.get("message"):
                    end_day = True
                    break
                transcript.append({"role": "user", "content": sim_out["message"]})
                if all(s in ("satisfied", "abandoned") for s in sim.goal_states.values()) \
                        and sim.goal_states:
                    end_day = True
                    break
            outcome_note = "normal" if end_day else "turn_limit"

            sim.day_end(day, transcript, disclosed_notes)

            episode = summarize_episode(client, day, transcript, day_row)
            memory.write_episode(day, episode["facts"], episode["user_concerns"],
                                 episode["open_items"])

            user_spoken_today = "\n".join(m.get("content", "") for m in transcript
                                           if m.get("role") == "user")
            for f in day_facts.get(day, []):
                if f["category"] == "检查结果" and f.get("name"):
                    _num = re.search(r"\d+(?:\.\d+)?", str(f["value"]))
                    if _num and _num.group() in user_spoken_today:
                        memory.write_profile(
                            {"recent_metrics": {f["name"]: {"value": str(f["value"])[:60],
                                                            "day": day}}}, day)

            distill_record = None
            if distill_mod.should_distill(day):
                try:
                    distill_record = distill_mod.distill(memory, day, client, user_dir / "distill")
                    log(f"  [distill] day={day} entries={distill_record.get('n_written')}")
                except Exception as exc:
                    log(f"  [warn] distill failed: {exc}")

            mem_blob = snapshot_memory_text(memory)
            (traj_dir / f"memory_snapshot_D{day:03d}.txt").write_text(
                f"# day={day} ts={now_iso()}\n" + mem_blob, encoding="utf-8")
            trap_check = check_traps(transcript, mem_blob, day, user_traps)

            user_text_cum += "\n" + "\n".join(m.get("content", "") for m in transcript
                                               if m.get("role") == "user")
            _hist_facts = [f for _d, _fl in day_facts.items() if _d < day for f in _fl]
            value_mismatch = check_value_mismatch(
                transcript, day_facts.get(day, []), [],
                historical_facts=_hist_facts, user_history_text=user_text_cum)
            if value_mismatch:
                log(f"  [value-mismatch] day={day} n={len(value_mismatch)}")
            date_str = ""
            if ws:
                try:
                    date_str = (datetime.fromisoformat(ws) + timedelta(days=day)).date().isoformat()
                except Exception:
                    date_str = ""
            ledger_truth = "当日事实：\n" + "\n".join(
                f"- [{f['category']}] {f['name']}: {str(f['value'])[:120]}"
                for f in day_facts.get(day, [])) if day_facts.get(day) else "（当日无医疗事件）"
            day_record = {
                "subject_id": user_id, "day": day, "date": date_str,
                "asset_mode": user_goals["asset_mode"], "no_day0": no_day0,
                "day_type": day_row.get("day_type"),
                "transcript": transcript,
                "goals": [{"goal_id": g["goal_id"], "goal_type": g["goal_type"],
                           "content": g["content"], "expected_behavior": g["expected_behavior"],
                           "cross_refs": g.get("cross_refs", []),
                           "final_state": sim.goal_states.get(g["goal_id"], "pending")}
                          for g in goals],
                "profile_summary": persona_text(profile),
                "day0_summary": d0_summary,
                "ledger_truth": ledger_truth,
                "disclosure_summary": sim.disclosure_summary_text(day),
                "web_search_calls": [l for l in toolbox.log if l["tool"] == "web_search"],
                "tool_trace": toolbox.log,
                "recall_count": toolbox.n_recalls,
                "gate_interventions": agent.gate_used,
                "goal_outcomes": {"end_reason": outcome_note, "n_turns": len(transcript)},
                "story_beats": day_row.get("story_beats", []),
                "privacy_preset": day_row.get("privacy"),
                "trap_check": trap_check,
                "value_mismatch": value_mismatch,
                "distill": distill_record,
                "memory_stats": memory.stats(),
                "elapsed_s": round(time.time() - day_start, 1),
            }
            (days_dir / f"{user_id}_D{day:03d}.json").write_text(
                json.dumps(day_record, ensure_ascii=False, indent=1), encoding="utf-8")

            done_days.append(day)
            progress_path.write_text(json.dumps({"days": sorted(done_days)}, ensure_ascii=False),
                                     encoding="utf-8")
            log(f"  day={day} type={day_row.get('day_type')} turns={len(transcript)} "
                f"end={outcome_note} recalls={toolbox.n_recalls} gate={agent.gate_used} "
                f"elapsed={time.time() - day_start:.0f}s")
        except Exception as exc:
            n_errors += 1
            log(f"  [error] day={day}: {exc}")
            traceback.print_exc(file=log_f)
            with (user_dir / "daily_errors.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"day": day, "error": str(exc)[:500],
                                    "timestamp": now_iso()}, ensure_ascii=False) + "\n")

        if day % 10 == 0:
            log(f"[progress] user={user_id} day={day}/{total_days} errors={n_errors}")

    log(f"[end] user={user_id} days={len(done_days)} errors={n_errors}")
    result = {"user_id": user_id, "n_days": len(done_days), "n_errors": n_errors,
              "variant": "healthjev_final", "timestamp": now_iso()}
    (user_dir / "scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return result


def summarize_episode(client, day: int, transcript, day_row) -> dict:
    convo = "\n".join(f"{'用户' if m['role'] == 'user' else '管家'}: {m['content'][:300]}"
                      for m in transcript)
    user_spoken = "\n".join(m["content"] for m in transcript if m.get("role") == "user")
    _hint_facts = []
    for f in day_row.get("facts", [])[:12]:
        _num = re.search(r"\d+(?:\.\d+)?", str(f.get("value", "")))
        if (_num and _num.group() in user_spoken) or str(f.get("name", "")) in user_spoken:
            _hint_facts.append(f)
    facts_hint = "；".join(f"{f['name']}:{str(f['value'])[:40]}" for f in _hint_facts)
    raw = client.ask_chat([
        {"role": "system", "content": "你是健康管家的记忆整理员。输出 STRICT JSON。"},
        {"role": "user", "content":
            f"把第 {day} 天对话整理成结构化纪要。当天已知事实（数值以此为准）：{facts_hint}\n"
            f"对话：\n{convo[:6000]}\n"
            "输出 STRICT JSON：{\"facts\":[\"当天医疗/生活事实，含数值，最多8条\"],"
            "\"user_concerns\":[\"用户表达的关注点，最多4条\"],"
            "\"open_items\":[\"未决事项/待跟进，最多4条\"]}"}],
        max_tokens=700, temperature=0.0, json_mode=True)
    try:
        txt = raw.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            if txt.startswith("json"):
                txt = txt[4:]
        obj = json.loads(txt[txt.find("{"): txt.rfind("}") + 1], strict=False)
    except Exception:
        obj = {}
    return {"facts": [str(x)[:160] for x in (obj.get("facts") or [])[:8]],
            "user_concerns": [str(x)[:160] for x in (obj.get("user_concerns") or [])[:4]],
            "open_items": [str(x)[:160] for x in (obj.get("open_items") or [])[:4]]}


def main():
    ap = argparse.ArgumentParser(description="Final longitudinal HealthJev runner")
    ap.add_argument("--user", type=int, required=True)
    ap.add_argument("--run-id", default="final")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--doubao-rpm", type=int, default=120,
                    help="本进程 doubao RPM 预算")
    ap.add_argument("--resume", action="store_true")
    day0 = ap.add_mutually_exclusive_group()
    day0.add_argument("--no-day0", dest="no_day0", action="store_true", default=True,
                      help="Paper condition (default): no Day0/profile injection into agent")
    day0.add_argument("--with-day0", dest="no_day0", action="store_false",
                      help="Non-paper ablation; requires licensed controlled Day0 data")
    ap.add_argument("--controlled-assets", default=None,
                    help="Authorized reference assets; defaults to HEALTHJEV_CONTROLLED_ASSETS")
    ap.add_argument("--doubao-app-id", default=None,
                    help="独立 doubao APP_ID")
    ap.add_argument("--runs-root", default=None,
                    help="runs 根目录（默认 runs/healthjev；显式传入可覆盖）")
    args = ap.parse_args()
    run_user(args.user, args.run_id, args.doubao_rpm, limit_days=args.days,
             resume=args.resume, runs_root=args.runs_root, no_day0=args.no_day0,
             doubao_app_id=args.doubao_app_id, controlled_assets=args.controlled_assets)


if __name__ == "__main__":
    main()
