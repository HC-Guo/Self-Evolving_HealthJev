# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FADE_TH = 0.25

SUMMARY_PROMPT = """Summarize the events and key information in the content.
以下是一天中用户与 AI 健康助手的对话记录，请用中文输出 2~3 句当日事件摘要，保留关键医疗事实与数值。
只输出摘要文本本身。"""

PERSONA_DAY_PROMPT = """Based on the following dialogue, please summarize the user's personality traits and emotions.
以下是一天中用户与 AI 健康助手的对话记录，请用中文输出 1~2 句用户性格与情绪洞察。
只输出洞察文本本身。"""

PERSONA_GLOBAL_PROMPT = """The following are the user's exhibited personality traits and emotions
throughout multiple days. Please provide a highly concise and general summary of the user's personality.
以下是多日积累的用户性格/情绪洞察列表，请用中文输出一段不超过 120 字的全局用户画像。
只输出画像文本本身。

【本次任务特别约束】本任务是批量独立案例评测：每个"天"对应一个独立的新患者/新任务实例，
不同天之间不存在同一位用户。请只聚合跨案例反复出现的共性模式（如常见的诉求类型、信息
披露方式、情绪基调），禁止把不同案例的人物身份、病史、数值合并成"同一位用户"的画像。"""

_CROSS_CASE_TURN_RULE = "（背景：本条对话来自一个独立的评测 case，与其他 case 无用户层面的关联）"


def _parse_text(raw: str) -> str:
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith(("json", "text")):
            txt = txt[4:]
    return txt.strip()[:400]


class MemoryBankMemory:
    def __init__(self, client, memory_dir, log_fn=print, top_k: int = 5,
                 frozen: bool = False):
        self.client = client
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.log_fn = log_fn
        self.top_k = top_k
        self.frozen = frozen
        self.pieces: List[Dict[str, Any]] = []
        self.daily_summaries: List[Dict[str, Any]] = []
        self.personality_daily: List[Dict[str, Any]] = []
        self.personality_global: str = ""
        self.forgetting_log: List[Dict[str, Any]] = []
        self._counter = 0
        self._load()

    def _store_path(self) -> Path:
        return self.memory_dir / "memorybank.json"

    def _load(self):
        if self._store_path().exists():
            try:
                obj = json.loads(self._store_path().read_text(encoding="utf-8"))
                self.pieces = obj.get("pieces", [])
                self.daily_summaries = obj.get("daily_summaries", [])
                self.personality_daily = obj.get("personality_daily", [])
                self.personality_global = obj.get("personality_global", "")
                self.forgetting_log = obj.get("forgetting_log", [])
                self._counter = obj.get("counter", len(self.pieces))
            except Exception:
                pass

    def save(self):
        try:
            self._store_path().write_text(json.dumps({
                "pieces": self.pieces, "daily_summaries": self.daily_summaries,
                "personality_daily": self.personality_daily[-90:],
                "personality_global": self.personality_global,
                "forgetting_log": self.forgetting_log[-2000:],
                "counter": self._counter}, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _next_id(self) -> str:
        self._counter += 1
        return f"mb-{self._counter:05d}"

    def _embed(self, text: str) -> List[float]:
        last = None
        for attempt in range(6):
            try:
                return self.client.embed(text)
            except Exception as e:
                last = e
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"memorybank embed failed after retries: {last}")

    @staticmethod
    def _cosine(a, b) -> float:
        try:
            num = sum(x * y for x, y in zip(a, b))
            da = sum(x * x for x in a) ** 0.5
            db = sum(x * x for x in b) ** 0.5
            return num / (da * db) if da and db else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def retention(piece: Dict[str, Any], day: int) -> float:
        t = max(0, day - piece.get("last_day", piece.get("day", day)))
        s = max(1, piece.get("strength", 1))
        return 2.718281828 ** (-t / s)

    def _update_strengths(self, day: int):
        if self.frozen:
            return
        faded = []
        for p in self.pieces:
            if p.get("faded"):
                continue
            r = self.retention(p, day)
            if r < FADE_TH:
                p["faded"] = True
                p["faded_day"] = day
                faded.append({"id": p["id"], "text": p["text"][:80], "faded_day": day,
                              "strength": p["strength"], "last_day": p.get("last_day")})
        if faded:
            self.forgetting_log.extend(faded)
            self.log_fn(f"    [mb-forget] day={day} faded={len(faded)} "
                        f"(e.g. {faded[0]['text'][:30]}...)")

    def add_turn(self, day: int, user_msg: str, assistant_msg: str):
        if self.frozen:
            return None
        text = f"用户: {str(user_msg)[:300]}\n助手: {str(assistant_msg)[:300]}"
        self.pieces.append({"id": self._next_id(), "text": text,
                            "embedding": self._embed(text), "day": day, "last_day": day,
                            "strength": 1, "faded": False})
        return self.pieces[-1]["id"]

    def retrieve(self, query: str, day: int, k: Optional[int] = None) -> List[Dict[str, Any]]:
        k = k or self.top_k
        if not query:
            return []
        q = self._embed(query)
        scored = []
        for p in self.pieces:
            if p.get("faded"):
                continue
            scored.append((self._cosine(q, p["embedding"]) * self.retention(p, day), p))
        scored.sort(key=lambda t: t[0], reverse=True)
        hits = []
        for s, p in scored[:k]:
            if s <= 0.05:
                continue
            if not self.frozen:
                p["strength"] = p.get("strength", 1) + 1
                p["last_day"] = day
            hits.append({"id": p["id"], "text": p["text"], "score": round(s, 4),
                         "strength": p["strength"], "retention": round(self.retention(p, day), 3)})
        if not self.frozen and hits:
            self.save()
        return hits

    def day_end(self, day: int, transcript: List[Dict[str, str]]):
        if self.frozen:
            return
        convo = "\n".join(
            f"{'用户' if m['role'] == 'user' else '助手'}: {m['content'][:300]}"
            for m in transcript if m["role"] in ("user", "assistant"))
        convo = convo + _CROSS_CASE_TURN_RULE
        try:
            summary = _parse_text(self.client.ask_chat([
                {"role": "user", "content": SUMMARY_PROMPT + f"\n\n[dialog]\n{convo[:6000]}"}],
                max_tokens=300, temperature=0.3))
            if summary:
                self.daily_summaries.append({"day": day, "text": summary})
        except Exception:
            pass
        try:
            insight = _parse_text(self.client.ask_chat([
                {"role": "user", "content": PERSONA_DAY_PROMPT + f"\n\n[dialog]\n{convo[:6000]}"}],
                max_tokens=200, temperature=0.3))
            if insight:
                self.personality_daily.append({"day": day, "text": insight})
        except Exception:
            pass
        try:
            daily_blob = "\n".join(f"第{r['day']}天: {r['text']}"
                                   for r in self.personality_daily[-10:])
            self.personality_global = _parse_text(self.client.ask_chat([
                {"role": "user", "content": PERSONA_GLOBAL_PROMPT + f"\n\n[daily Personalities]\n{daily_blob}"}],
                max_tokens=200, temperature=0.3)) or self.personality_global
        except Exception:
            pass
        self._update_strengths(day)
        self.save()

    def context_block(self, query: str, day: int) -> Dict[str, str]:
        hits = self.retrieve(query, day)
        recent_days = [d for d in self.daily_summaries if day - 7 <= d["day"] < day][-7:]
        sum_block = "\n".join(f"- 第{d['day']}天摘要: {d['text']}" for d in recent_days) \
            or "（暂无历史摘要）"
        return {"memories": hits, "summaries": sum_block,
                "portrait": self.personality_global or "（画像形成中）"}

    def blob_text(self) -> str:
        live = [p["text"] for p in self.pieces if not p.get("faded")]
        sums = [d["text"] for d in self.daily_summaries]
        return ("\n".join(live) + "\n" + "\n".join(sums))[:20000]

    def sample(self, k: int = 5) -> List[str]:
        return [p["text"][:150] for p in self.pieces[:k]]

    def stats(self) -> Dict[str, Any]:
        live = [p for p in self.pieces if not p.get("faded")]
        return {"mode": "memorybank_ebbinghaus",
                "n_pieces_total": len(self.pieces), "n_pieces_live": len(live),
                "n_faded": len(self.pieces) - len(live),
                "n_daily_summaries": len(self.daily_summaries),
                "has_portrait": bool(self.personality_global)}
