# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

OPEN_SYS = "你是一名正在使用 AI 健康管家的真实用户。只输出你说的话（第一人称中文口语），不要输出其他内容。"
REPLY_SYS = ("你是一名正在使用 AI 健康管家的真实用户。输出 STRICT JSON（不要 markdown 代码块），"
             "包含你的发言与目标状态更新。")

GOAL_STATES = ("pending", "in_progress", "satisfied", "abandoned")
MAX_TURNS_PER_DAY = 20

_ENVELOPE_MARKS = ('"goal_updates"', '"end_day"', '"disclosed"', '"message"')


def _clean_user_msg(raw: str) -> str:
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:]
    s, e = txt.find("{"), txt.rfind("}")
    if 0 <= s < e:
        try:
            obj = json.loads(txt[s:e + 1], strict=False)
            if isinstance(obj, dict) and str(obj.get("message", "")).strip():
                return str(obj["message"]).strip()
        except Exception:
            pass
        m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
        if m:
            try:
                cand = json.loads('"' + m.group(1) + '"')
            except Exception:
                cand = m.group(1)
            if str(cand).strip():
                return str(cand).strip()
        txt = (txt[:s] + " " + txt[e + 1:]).strip()
    if any(k in txt for k in _ENVELOPE_MARKS):
        return ""
    return txt


def persona_text(profile: Dict[str, Any]) -> str:
    p = profile.get("persona", {})
    c = profile.get("clinical", {})
    demo = profile.get("demo", {})
    return (
        f"{demo.get('age', '')}岁{'女' if demo.get('gender') == 'F' else '男'}，"
        f"{p.get('occupation', '')}；同住：{p.get('living_with', '')}；"
        f"照顾的家人：{p.get('caregiving', '')}；说话方式：{p.get('tone', '')}；"
        f"口头禅：{'/'.join(p.get('common_phrases', []))}；"
        f"饮食偏好：{'；'.join(p.get('dietary_preferences', []))}；"
        f"运动：{'；'.join(p.get('exercise_habits', []))}；睡眠：{p.get('sleep_pattern', '')}\n"
        f"慢病：{'、'.join(c.get('conditions', []))}；"
        f"过敏：{', '.join(a.get('trigger', '') for a in c.get('allergies', []))}"
    )


class UserSimV7:
    def __init__(self, profile: Dict[str, Any], user_goals: Dict[str, Any],
                 client, state_path: Optional[Path] = None):
        self.profile = profile
        self.client = client
        self.days: Dict[int, Dict[str, Any]] = {r["day"]: r for r in user_goals.get("days", [])}
        self.state_path = Path(state_path) if state_path else None
        self.disclosure: List[Dict[str, Any]] = []
        self.rolling_summary = "（刚开始使用健康管家）"
        self.goal_states: Dict[str, str] = {}
        if self.state_path and self.state_path.exists():
            try:
                st = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.disclosure = st.get("disclosure", [])
                self.rolling_summary = st.get("rolling_summary") or self.rolling_summary
            except Exception:
                pass

    def opener(self, day: int) -> str:
        d = self.days.get(day, {})
        script = d.get("disclosure_script") or []
        if script:
            return str(script[0]).strip()
        goals_text = self._day_goals_text(day)
        beat = "；".join(d.get("story_beats", [])) or "（无）"
        prompt = f"""今天是第 {day} 天。你的画像：{persona_text(self.profile)}
近期摘要：{self.rolling_summary}
今天的目标（自然完成，不要一次说完）：{goals_text}
今天的生活剧情：{beat}
你之前已告诉过助手的事（可自然引用，不当作新闻）：{self._recent_disclosure(day)}
请生成今天的开场白：1~3 句口语短句，围绕今天最重要的 1 个目标；禁止列表/标题/【】括号/编造临床数值。"""
        out = self.client.ask_chat(
            [{"role": "system", "content": OPEN_SYS}, {"role": "user", "content": prompt}],
            max_tokens=300, temperature=0.8)
        out = _clean_user_msg(out or "")
        return (out or "嗨，在吗？").strip()

    def followup_disclosure(self, day: int) -> Optional[str]:
        d = self.days.get(day, {})
        script = d.get("disclosure_script") or []
        if len(script) > 1:
            return str(script[1]).strip()
        return None

    def reply(self, day: int, agent_msg: str, transcript: List[Dict[str, str]], turn: int) -> Dict[str, Any]:
        d = self.days.get(day, {})
        convo = "\n".join(
            f"{'你' if m['role'] == 'user' else '助手'}: {m['content'][:500]}"
            for m in transcript[-8:])
        fact_lines = [f"- {f['name']}: {f['value']}" for f in d.get("facts", [])[:10]]
        sm_lines = [f"- 自测{k}={v}" for k, v in (d.get("self_monitor_values") or {}).items()]
        prompt = f"""今天是第 {day} 天，第 {turn} 轮。
你的画像：{persona_text(self.profile)}
今天的目标与状态：{self._day_goals_text(day)}
本轮之前的对话：
{convo}
助手刚才说：
{agent_msg[:1200]}

你可以说的临床数值只能来自（防编造，不得说出以下之外的数值）：
{chr(10).join(fact_lines + sm_lines) if (fact_lines or sm_lines) else '（今天没有新的医疗数值）'}
已披露账本（可自然引用）：{self._recent_disclosure(day, k=8)}

输出 STRICT JSON：
{{"message": "你的发言（中文口语 1~3 句；goal 未满足就继续推进——追问/补充/换角度；全部满足就自然收尾）",
  "goal_updates": [{{"goal_id": "...", "status": "satisfied|abandoned|in_progress"}}],
  "end_day": false,
  "disclosed": "本轮你向助手透露的新信息摘要（没有则空字符串）"}}
规则：satisfied=助手已实质满足该目标；被敷衍且不耐烦可 abandoned；全部 goal 终态 → end_day=true 并说自然结束语；
不评价助手好坏；不提前说未来的事。"""
        raw = self.client.ask_chat(
            [{"role": "system", "content": REPLY_SYS}, {"role": "user", "content": prompt}],
            max_tokens=500, temperature=0.7, json_mode=True)
        try:
            txt = raw.strip()
            if txt.startswith("```"):
                txt = txt.strip("`")
                if txt.startswith("json"):
                    txt = txt[4:]
            obj = json.loads(txt[txt.find("{"): txt.rfind("}") + 1], strict=False)
            if not isinstance(obj, dict):
                raise ValueError("reply json not a dict")
        except Exception:
            return {"message": (_clean_user_msg(raw or "") or "嗯嗯")[:300], "goal_updates": [],
                    "end_day": False, "disclosed": ""}
        msg = _clean_user_msg(str(obj.get("message", "")))
        if not msg:
            msg = _clean_user_msg(raw or "")
        updates = []
        for u in obj.get("goal_updates", []) or []:
            gid, st = str(u.get("goal_id", "")), str(u.get("status", ""))
            if gid in self.goal_states and st in GOAL_STATES:
                updates.append((gid, st))
        for gid, st in updates:
            self.goal_states[gid] = st
        end_day = bool(obj.get("end_day", False))
        if self.goal_states and all(s in ("satisfied", "abandoned") for s in self.goal_states.values()):
            end_day = True
        if not msg:
            msg = "嗯。"
        return {"message": msg[:300], "goal_updates": updates, "end_day": end_day,
                "disclosed": str(obj.get("disclosed", ""))[:200]}

    def day_end(self, day: int, transcript: List[Dict[str, str]], disclosed_notes: List[str]):
        d = self.days.get(day, {})
        for note in disclosed_notes:
            if note:
                self.disclosure.append({"day": day, "note": note[:150]})
        for f in (d.get("facts") or [])[:12]:
            self.disclosure.append({"day": day, "note": f"{f['name']}: {str(f['value'])[:80]}"})
        for k, v in (d.get("self_monitor_values") or {}).items():
            self.disclosure.append({"day": day, "note": f"自测{k}={v}"})
        user_msgs = [m["content"] for m in transcript if m["role"] == "user"]
        today_line = f"D{day}: " + (user_msgs[0][:100] if user_msgs else "")
        if day % 7 == 0:
            try:
                summary = self.client.ask_chat([{
                    "role": "user",
                    "content": ("把以下健康管家使用记录压缩成 200 字内摘要"
                                "（保留关键医疗事实/偏好/剧情进展）：\n"
                                f"{self.rolling_summary}\n{today_line}")}],
                    max_tokens=300, temperature=0.3)
                if summary.strip():
                    self.rolling_summary = summary.strip()[:500]
            except Exception:
                pass
        else:
            self.rolling_summary = (self.rolling_summary + "\n" + today_line)[-1200:]
        self.save_state(day)

    def save_state(self, day: int):
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({
            "disclosure": self.disclosure, "rolling_summary": self.rolling_summary,
            "last_day": day, "updated_at": __import__("datetime").datetime.now().isoformat(
                timespec="seconds"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    def disclosure_summary_text(self, day: int) -> str:
        rows = [r for r in self.disclosure if r["day"] < day]
        if not rows:
            return "（用户尚未向助手披露任何信息；Day0 档案除外）"
        return "\n".join(f"- 第{r['day']}天: {r['note']}" for r in rows[-120:])

    def _day_goals_text(self, day: int) -> str:
        d = self.days.get(day, {})
        lines = []
        for g in d.get("goals", []):
            st = self.goal_states.get(g["goal_id"], "pending")
            lines.append(f"- goal_id={g['goal_id']} [{g['goal_type']}|{st}] {g['content']}"
                         f"（完成条件：{g['end_condition']}）")
        return "\n".join(lines) or "（今天没有特定目标，自然闲聊）"

    def _recent_disclosure(self, day: int, k: int = 12) -> str:
        rows = [r for r in self.disclosure if r["day"] < day][-k:]
        if not rows:
            return "（还没有向助手透露过什么）"
        return "\n".join(f"- 第{r['day']}天: {r['note']}" for r in rows)
