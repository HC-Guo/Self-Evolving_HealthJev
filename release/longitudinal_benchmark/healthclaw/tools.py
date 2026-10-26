# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .memory import FiveLayerMemory
from .write_guard import WriteGuard

TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "memory_recall",
        "description": "语义检索你的长期记忆（个人档案/每日纪要/管家经验），返回最相关的历史片段（含记录日期）。回答涉及用户过往事项前必须先调用。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索问题或关键词，如'用户上次说的血钾值'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "memory_write",
        "description": "写入长期记忆。target=profile 时 payload 字段仅限：medications[{drug,dose,note}]/recent_metrics{指标名:{value,day}}/allergies[]/history[]/preferences[]；target=note 时把一条用户告知的事实存为纪要补充。数值必须与用户口述/单据一致。",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "enum": ["profile", "note"]},
            "payload": {"type": "object", "description": "写入内容（profile 时按固定 schema）"}},
            "required": ["target"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "查询公开健康知识/常识（不带入用户姓名、身份证号或精确指标值）。返回检索结果摘要。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索问题"}},
            "required": ["query"]}}},
]

_WEB_PRIVACY_PAT = re.compile(r"[身份证|医保卡]|身份证|医保卡?号")


class ToolBox:
    def __init__(self, memory: FiveLayerMemory, client, web_client,
                 write_guard: Optional[WriteGuard] = None,
                 extra_schemas: Optional[List[Dict[str, Any]]] = None,
                 extra_handlers: Optional[Dict[str, Any]] = None,
                 include_builtin: bool = True):
        self.memory = memory
        self.client = client
        self.web_client = web_client
        self.guard = write_guard
        self.extra_schemas = list(extra_schemas or [])
        self.extra_handlers = dict(extra_handlers or {})
        self.include_builtin = include_builtin
        self.log: List[Dict[str, Any]] = []
        self.recalled_texts: List[str] = []
        self.n_recalls = 0

    def schemas(self) -> List[Dict[str, Any]]:
        return (TOOLS_SCHEMA if self.include_builtin else []) + self.extra_schemas

    def set_guard(self, guard: Optional[WriteGuard]):
        self.guard = guard

    def dispatch(self, name: str, arguments: str, day: int) -> Dict[str, Any]:
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except Exception:
            args = {}
        try:
            if name in self.extra_handlers:
                result = self.extra_handlers[name](args, day)
            elif name == "memory_recall":
                result = self._recall(str(args.get("query", "")), day)
            elif name == "memory_write":
                result = self._write(args, day)
            elif name == "web_search":
                result = self._web_search(str(args.get("query", "")))
            else:
                result = {"error": f"unknown tool {name}"}
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self.log.append({"day": day, "tool": name, "args": args,
                         "ok": "error" not in result})
        return result

    def _recall(self, query: str, day: int) -> Dict[str, Any]:
        if not query.strip():
            return {"error": "empty query"}
        self.n_recalls += 1
        hits = self.memory.index.query(query, current_day=day, top_k=5)
        for h in hits:
            self.recalled_texts.append(h.get("text", ""))
        return {"results": [
            {"day": h["day_index"], "layer": h["layer"], "score": h["score"],
             "text": h["text"]} for h in hits] or "（没有检索到相关记忆）"}

    def _write(self, args: Dict[str, Any], day: int) -> Dict[str, Any]:
        target = str(args.get("target", "profile"))
        payload = args.get("payload") or {}
        if target == "profile":
            if self.guard:
                check = self.guard.check_write_payload(payload)
                if check["conflicts"]:
                    c = check["conflicts"][0]
                    return {"status": "rejected",
                            "reason": (f"写入校验未通过：{c['item']} 声称值 {c['claimed']} 与当日来源 "
                                       f"（{c['source']}，真值 {c['truth']}）矛盾；请核对单位后以来源真值写入"),
                            "conflicts": check["conflicts"]}
            summary = self.memory.write_profile(payload, day)
            return {"status": "ok", "written": summary["applied"], "rejected": summary["rejected"]}
        elif target == "note":
            text = str(payload.get("text", ""))[:400]
            if not text:
                return {"status": "rejected", "reason": "empty note"}
            self.memory.index.add_chunk(text, day, "L4", "user_note")
            return {"status": "ok", "written": ["note"]}
        return {"status": "rejected", "reason": f"unknown target {target}"}

    def _web_search(self, query: str) -> Dict[str, Any]:
        if not query.strip():
            return {"error": "empty query"}
        out = self.web_client.ask_block(
            "请就以下问题给出简明、可靠的公开健康知识回答（150字内），"
            "如涉及数值请给出常见单位；这是面向健康助手的检索式提问，不要编造文献：\n" + query,
            max_tokens=300, temperature=0.0)
        return {"summary": (out or "（检索无结果）")[:600]}
