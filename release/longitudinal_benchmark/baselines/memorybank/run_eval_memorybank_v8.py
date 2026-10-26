# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memorybank_memory import MemoryBankMemory
from healthclaw.llm_client import make_doubao_client
from usersim.usersim_v7 import UserSimV7, persona_text
from judge.checks import check_value_mismatch, check_traps

from benchmark.assets import load_user_assets
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs" / "memorybank"

MB_APP_ID = os.environ.get("MEMORYBANK_APP_ID", "")
RATE_CHANNEL = "doubao_memorybank"

MAX_TURNS_PER_DAY = 20
MB_SYS_TEMPLATE = """You are SiliconFriend, an empathetic AI companion with long-term memory,
now serving as a personal health assistant. Recall relevant memories to stay consistent with
what happened before, respond with warmth, and answer health questions concretely.

[User Portrait]
{portrait}

[Relevant Memories (strength-weighted recall)]
{memories}

[Recent Daily Summaries]
{summaries}"""


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


def run_user(user_id: int, run_id: str, doubao_rpm: int, limit_days=None,
             resume: bool = False, runs_root=None, controlled_assets=None) -> dict:
    profile, archive, user_goals, user_traps, day_facts = load_user_assets(
        user_id, controlled_assets=controlled_assets)
    run_root = (Path(runs_root) if runs_root else DEFAULT_RUNS_ROOT) / run_id
    user_dir = run_root / str(user_id)
    days_dir = user_dir / "days"
    traj_dir = user_dir / "trajectories"
    for d in (days_dir, traj_dir):
        d.mkdir(parents=True, exist_ok=True)

    progress_path = user_dir / "day_progress.json"
    done_days = []
    if resume and progress_path.exists():
        try:
            done_days = json.loads(progress_path.read_text(encoding="utf-8")).get("days", [])
        except Exception:
            done_days = []

    d0_summary = day0_summary_text(archive, profile)

    log_path = traj_dir / "run.log"
    log_f = log_path.open("a", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        log_f.write(msg + "\n")
        log_f.flush()

    client = make_doubao_client(rpm=doubao_rpm, app_id=MB_APP_ID, channel=RATE_CHANNEL)
    if not resume:
        mem_dir = user_dir / "memorybank_memory"
        if mem_dir.exists():
            shutil.rmtree(mem_dir)
    sim = UserSimV7(profile, user_goals, client,
                    state_path=user_dir / "usersim_state" / f"{user_id}.json")
    mb = MemoryBankMemory(client, user_dir / "memorybank_memory", log_fn=lambda m: log(f"    {m}"))

    recall_path = traj_dir / "memorybank_recalls.jsonl"
    sysp_dir = traj_dir / "sysprompts"
    sysp_dir.mkdir(parents=True, exist_ok=True)

    total_days = limit_days or len(user_goals["days"])
    ws = archive.get("window_start", "")
    n_errors = 0
    log(f"[start] user={user_id} run_id={run_id} runner=memorybank_final "
        f"app_id=***{MB_APP_ID[-5:]} channel={RATE_CHANNEL} rpm={doubao_rpm} "
        f"fresh={not resume} days={total_days} done={done_days} "
        f"memory=memorybank(ebbinghaus R=e^(-t/S),fade@0.25,recall S+1) day0=none "
        f"pieces={mb.stats()['n_pieces_total']}")

    user_text_cum = ""
    for _dp in sorted(days_dir.glob(f"{user_id}_D*.json")):
        try:
            _old = json.loads(_dp.read_text(encoding="utf-8"))
            user_text_cum += "\n" + "\n".join(
                m.get("content", "") for m in _old.get("transcript", [])
                if m.get("role") == "user")
        except Exception:
            pass

    for day in range(1, total_days + 1):
        if day in done_days:
            continue
        day_start = time.time()
        try:
            day_row = user_goals["days"][day - 1]
            goals = day_row.get("goals", [])
            sim.goal_states = {g["goal_id"]: "pending" for g in goals}
            sim.save_state(day)

            transcript = []
            opener = sim.opener(day)
            transcript.append({"role": "user", "content": opener})
            followup = sim.followup_disclosure(day)

            end_day = False
            turn = 0
            sim_out = {"disclosed": ""}
            disclosed_notes: list = []
            recall_hits = 0
            while turn < MAX_TURNS_PER_DAY:
                turn += 1
                user_msg = transcript[-1]["content"]

                ctx = mb.context_block(user_msg, day)
                recall_hits += len(ctx["memories"])
                sys_prompt = MB_SYS_TEMPLATE.format(
                    portrait=ctx["portrait"],
                    memories="\n".join(f"- {m['text']}" for m in ctx["memories"])
                    or "（暂无被召回的记忆）",
                    summaries=ctx["summaries"])
                (sysp_dir / f"D{day:03d}_T{turn:02d}.txt").write_text(sys_prompt, encoding="utf-8")
                for mark in ("用户接入档案", "基线体检记录", "健康画像", "Day-0"):
                    assert mark not in sys_prompt, f"sys prompt leaks {mark}"
                with recall_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"user_id": user_id, "day": day, "turn": turn,
                                        "query": user_msg[:120],
                                        "hits": [{"id": m["id"], "score": m["score"],
                                                  "strength": m["strength"],
                                                  "retention": m["retention"]} for m in ctx["memories"]],
                                        "ts": now_iso()}, ensure_ascii=False) + "\n")

                history = [{"role": m["role"], "content": m["content"]}
                           for m in transcript[:-1] if m["role"] in ("user", "assistant")]
                agent_response = client.ask_chat(
                    [{"role": "system", "content": sys_prompt}] + history +
                    [{"role": "user", "content": user_msg}],
                    max_tokens=800, temperature=0.7)
                agent_response = (agent_response or "").strip()
                transcript.append({"role": "assistant", "content": agent_response})

                mb.add_turn(day, user_msg, agent_response)

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

            mb.day_end(day, transcript)

            trap_check = check_traps(transcript, mb.blob_text(), day, user_traps)

            (traj_dir / "memory_snapshots").mkdir(parents=True, exist_ok=True)
            live = [p for p in mb.pieces if not p.get("faded")]
            (traj_dir / "memory_snapshots" / f"D{day:03d}.json").write_text(json.dumps(
                {"user_id": user_id, "day": day, "mode": "memorybank_ebbinghaus",
                 "snapshot": {"n_live": len(live), "n_faded": mb.stats()["n_faded"],
                              "strength_hist": sorted(p.get("strength", 1) for p in live)[-30:],
                              "last_summary": (mb.daily_summaries[-1]["text"]
                                               if mb.daily_summaries else ""),
                              "portrait": mb.personality_global,
                              "recent_faded": mb.forgetting_log[-5:]},
                 "ts": now_iso()}, ensure_ascii=False, indent=1), encoding="utf-8")

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
                "asset_mode": user_goals["asset_mode"],
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
                "web_search_calls": [],
                "tool_trace": [],
                "recall_count": recall_hits,
                "gate_interventions": 0,
                "goal_outcomes": {"end_reason": outcome_note, "n_turns": len(transcript)},
                "story_beats": day_row.get("story_beats", []),
                "privacy_preset": day_row.get("privacy"),
                "trap_check": trap_check,
                "value_mismatch": value_mismatch,
                "distill": None,
                "memory_stats": mb.stats(),
                "memorybank_stats": {"recall_hits": recall_hits,
                                     "n_pieces_total": mb.stats()["n_pieces_total"],
                                     "n_pieces_live": mb.stats()["n_pieces_live"],
                                     "n_faded": mb.stats()["n_faded"],
                                     "n_daily_summaries": mb.stats()["n_daily_summaries"]},
                "snapshot_path": str(traj_dir / "memory_snapshots" / f"D{day:03d}.json"),
                "elapsed_s": round(time.time() - day_start, 1),
            }
            (days_dir / f"{user_id}_D{day:03d}.json").write_text(
                json.dumps(day_record, ensure_ascii=False, indent=1), encoding="utf-8")

            done_days.append(day)
            progress_path.write_text(json.dumps({"days": sorted(done_days)}, ensure_ascii=False),
                                     encoding="utf-8")
            log(f"  day={day} type={day_row.get('day_type')} turns={len(transcript)} "
                f"end={outcome_note} recalls={recall_hits} "
                f"live={mb.stats()['n_pieces_live']} faded={mb.stats()['n_faded']} "
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
              "variant": "memorybank_final", "timestamp": now_iso()}
    (user_dir / "scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser(description="Final longitudinal MemoryBank baseline runner")
    ap.add_argument("--user", type=int, required=True)
    ap.add_argument("--run-id", default="final")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--doubao-rpm", type=int, default=100,
                    help="本进程 doubao RPM（MemoryBank 通道独立 500 总额，每进程 ≤100）")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--runs-root", default=None,
                    help="runs 根目录（默认 runs/memorybank；显式传入可覆盖）")
    ap.add_argument("--controlled-assets", default=None,
                    help="Authorized reference assets; defaults to HEALTHJEV_CONTROLLED_ASSETS")
    args = ap.parse_args()
    run_user(args.user, args.run_id, args.doubao_rpm, limit_days=args.days,
             resume=args.resume, runs_root=args.runs_root, controlled_assets=args.controlled_assets)


if __name__ == "__main__":
    main()
