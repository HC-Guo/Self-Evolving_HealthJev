# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

FACT_EXTRACTION_PROMPT = """你是一名个人信息整理器（Personal Information Organizer），专门从对话中准确抽取事实、用户记忆与偏好，并把它们整理成相互独立、便于管理的事实条目，供未来交互中检索与个性化使用。

需要记住的信息类型：
1. 个人偏好：食物、产品、活动、娱乐等方面的好恶与具体偏好；
2. 重要个人信息：姓名、亲属关系、重要日期等；
3. 计划与意图：即将发生的事件、目标、用户提到过的安排；
4. 活动与服务偏好：饮食、出行、爱好等；
5. 健康与养生信息：饮食禁忌、运动习惯、身体指标、用药、复查、症状等健康相关事实；
6. 职业信息：职业、工作习惯、目标；
7. 其他：用户分享的任何值得长期记住的细节。

few-shot 示例：
用户: 你好。
输出: {"facts": []}
用户: 我昨天拿到化验单，肌酐 130 µmol/L（≈1.47 mg/dL），医生说要复查。
输出: {"facts": ["昨天拿到化验单，肌酐 130 µmol/L（≈1.47 mg/dL），医生要求复查"]}
用户: 我叫约翰，是软件工程师。
输出: {"facts": ["用户名叫约翰", "用户是软件工程师"]}

硬性规则：
- 只根据【用户消息】抽取，不得从助手消息或系统消息中取任何内容；
- 检测用户输入语言，事实用同样语言记录；
- 保留用户说出的具体数值（数值是事实的关键部分）；
- 对话里没有值得记的就返回空列表；
- 只输出 STRICT JSON：{"facts": ["事实1", "事实2", ...]}。

以下是一次用户与助手的对话，请抽取关于用户的事实与偏好："""


UPDATE_MEMORY_PROMPT = """你是一个智能记忆管理器，负责维护一个系统的长期记忆。你可以执行四种操作：
(1) ADD 加入记忆；(2) UPDATE 更新记忆；(3) DELETE 删除记忆；(4) NONE 不变。

请把新抽取的事实与既有记忆逐一对比，为每个新事实判定一种操作：
- ADD：记忆中没有该信息 → 作为新条目加入；
- UPDATE：记忆中已有相近条目但信息发生了变化/更丰富 → 用新事实更新该条目（保持原 id，信息量取大者；
  若表达的是同一件事则无需更新记 NONE）；
- DELETE：新事实明确表明某条既有记忆已过时/失效/被推翻 → 删除该条目；
- NONE：事实已存在于记忆中或无关紧要 → 不变。

few-shot 示例：
既有记忆：
[{"id": "0", "text": "用户是软件工程师"}]
新事实: ["用户名叫约翰"]
输出: {"memory": [{"id": "0", "text": "用户是软件工程师", "event": "NONE"},
                  {"id": "1", "text": "用户名叫约翰", "event": "ADD"}]}

既有记忆：
[{"id": "0", "text": "用户的降压药是氨氯地平 5mg 每日一次"}]
新事实: ["医生把降压药调成了缬沙坦 80mg 每日一次"]
输出: {"memory": [{"id": "0", "text": "用户的降压药是缬沙坦 80mg 每日一次（第N天医生调整）", "event": "UPDATE", "old_memory": "用户的降压药是氨氯地平 5mg 每日一次"}]}

既有记忆：
[{"id": "0", "text": "用户对青霉素过敏"}]
新事实: ["用户此前以为自己对青霉素过敏，这次皮试结果是阴性，医生说不过敏"]
输出: {"memory": [{"id": "0", "text": "用户此前以为对青霉素过敏，皮试阴性，医生确认不过敏", "event": "UPDATE", "old_memory": "用户对青霉素过敏"}]}

规则：
- 只输出 STRICT JSON：{"memory": [{"id": "<既有 id 或新 id>", "text": "<条目文本>",
  "event": "<ADD|UPDATE|DELETE|NONE>", "old_memory": "<仅 UPDATE 时必填>"}]}；
- UPDATE/DELETE/NONE 只能引用输入里给出的既有 id，不得编造新 id；ADD 用不存在的新 id；
- event 枚举只能是 ADD / UPDATE / DELETE / NONE。"""

DAY_CONTEXT_TMPL = "\n当前天数：第{day}天"


def _parse_json(raw: str) -> Optional[dict]:
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:]
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(txt[s:e + 1], strict=False)
    except Exception:
        return None


class Mem0Memory:
    def __init__(self, client, memory_dir, log_fn=print, top_k: int = 3):
        self.client = client
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.log_fn = log_fn
        self.top_k = top_k
        self.memories: List[Dict[str, Any]] = []
        self.ops_log: List[Dict[str, Any]] = []
        self._counter = 0
        self._load()

    def _store_path(self) -> Path:
        return self.memory_dir / "memories.json"

    def _load(self):
        if self._store_path().exists():
            try:
                obj = json.loads(self._store_path().read_text(encoding="utf-8"))
                self.memories = obj.get("memories", [])
                self.ops_log = obj.get("ops_log", [])
                self._counter = obj.get("counter", len(self.memories))
            except Exception:
                pass

    def save(self):
        self._store_path().write_text(json.dumps(
            {"memories": self.memories, "ops_log": self.ops_log[-2000:],
             "counter": self._counter}, ensure_ascii=False, indent=1), encoding="utf-8")

    def _next_id(self) -> str:
        self._counter += 1
        return f"mem-{self._counter:04d}"

    def _embed(self, text: str) -> List[float]:
        last = None
        for attempt in range(6):
            try:
                return self.client.embed(text)
            except Exception as e:
                last = e
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"mem0 embed failed after retries: {last}")

    @staticmethod
    def _cosine(a, b) -> float:
        try:
            num = sum(x * y for x, y in zip(a, b))
            da = sum(x * x for x in a) ** 0.5
            db = sum(x * x for x in b) ** 0.5
            return num / (da * db) if da and db else 0.0
        except Exception:
            return 0.0

    def search(self, query: str, k: Optional[int] = None) -> List[Dict[str, Any]]:
        k = k or self.top_k
        if not self.memories:
            return []
        q = self._embed(query)
        scored = sorted(
            ((self._cosine(q, m["embedding"]), m) for m in self.memories),
            key=lambda t: t[0], reverse=True)[:k]
        return [{"id": m["id"], "text": m["text"], "score": round(s, 4)}
                for s, m in scored if s > 0.2]

    def add_day(self, day: int, transcript: List[Dict[str, str]]) -> Dict[str, Any]:
        convo = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
            for m in transcript if m["role"] in ("user", "assistant"))
        raw = self.client.ask_chat([
            {"role": "system", "content": "You are a Personal Information Organizer. Output STRICT JSON only."},
            {"role": "user", "content": FACT_EXTRACTION_PROMPT + f"\n\n对话：\n{convo[:8000]}"}],
            max_tokens=900, temperature=0.0, json_mode=True)
        obj = _parse_json(raw) or {}
        facts = [str(x).strip()[:200] for x in (obj.get("facts") or []) if str(x).strip()][:12]

        n_add = n_upd = n_del = 0
        for f in facts:
            similar = self._similar_existing(f)
            old_block = "\n".join(json.dumps({"id": s["id"], "text": s["text"]},
                                             ensure_ascii=False) for s in similar) or "[]"
            raw2 = self.client.ask_chat([
                {"role": "system", "content": "You are a smart memory manager. Output STRICT JSON only."},
                {"role": "user", "content": UPDATE_MEMORY_PROMPT +
                 f"\n\n既有记忆：\n{old_block}\n新事实: [\"{f}\"]\n"
                 + DAY_CONTEXT_TMPL.format(day=day)}],
                max_tokens=600, temperature=0.0, json_mode=True)
            obj2 = _parse_json(raw2) or {}
            for item in obj2.get("memory", []) or []:
                event = str(item.get("event", "NONE")).upper()
                mid = str(item.get("id", ""))
                text = str(item.get("text", "")).strip()[:220]
                existing = {m["id"]: i for i, m in enumerate(self.memories)}
                if event == "ADD" and text and mid not in existing:
                    self.memories.append({"id": self._next_id(), "text": text,
                                          "embedding": self._embed(text),
                                          "created_day": day, "updated_day": day})
                    n_add += 1
                elif event == "UPDATE" and text and mid in existing:
                    i = existing[mid]
                    self.memories[i]["text"] = text
                    self.memories[i]["embedding"] = self._embed(text)
                    self.memories[i]["updated_day"] = day
                    n_upd += 1
                elif event == "DELETE" and mid in existing:
                    self.memories.pop(existing[mid])
                    n_del += 1
        rec = {"day": day, "n_facts": len(facts), "facts": facts,
               "n_add": n_add, "n_update": n_upd, "n_delete": n_del,
               "n_memories": len(self.memories), "ts": datetime.now().isoformat(timespec="seconds")}
        self.ops_log.append(rec)
        self.save()
        self.log_fn(f"    [mem0-add] day={day} facts={len(facts)} +{n_add}/~{n_upd}/-{n_del} "
                    f"total={len(self.memories)}")
        return rec

    def _similar_existing(self, fact: str, k: int = 3) -> List[Dict[str, Any]]:
        if not self.memories:
            return []
        q = self._embed(fact)
        scored = sorted(((self._cosine(q, m["embedding"]), m) for m in self.memories),
                        key=lambda t: t[0], reverse=True)[:k]
        return [{"id": m["id"], "text": m["text"], "score": round(s, 4)} for s, m in scored]

    def blob_text(self) -> str:
        return "\n".join(m["text"] for m in self.memories)[:20000]

    def sample(self, k: int = 5) -> List[str]:
        return [m["text"] for m in self.memories[:k]]

    def stats(self) -> Dict[str, Any]:
        return {"mode": "mem0_facts", "n_memories": len(self.memories)}
