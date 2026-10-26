# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

_FRIDAY_SEARCH_URL = os.environ.get("HEALTHJEV_WEB_SEARCH_URL", "")
_FRIDAY_TOKEN = os.environ.get("HEALTHJEV_WEB_SEARCH_TOKEN", "")


def friday_web_search(query: str, max_results: int = 4) -> str:
    if not _FRIDAY_SEARCH_URL or not _FRIDAY_TOKEN:
        return "（web_search unavailable：未配置 HEALTHJEV_WEB_SEARCH_URL / HEALTHJEV_WEB_SEARCH_TOKEN）"
    import requests

    if not query or not query.strip():
        return "（搜索词为空，未执行搜索）"
    try:
        r = requests.post(
            _FRIDAY_SEARCH_URL,
            headers={"Authorization": f"Bearer {_FRIDAY_TOKEN}",
                     "Content-Type": "application/json"},
            json={"query": query.strip()[:120], "sources": ["baidu-search-v2"],
                  "topK": int(max_results)},
            proxies={"http": None, "https": None},
            timeout=30,
        )
        if r.status_code != 200:
            return f"（搜索失败：HTTP {r.status_code}）"
        items = ((r.json() or {}).get("data") or {}).get("results") or []
        if not items:
            return "（搜索无结果）"
        lines = []
        for i, it in enumerate(items[:int(max_results)], 1):
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "")[:80]
            snippet = (it.get("snippet") or it.get("content") or "")[:200]
            src = it.get("website") or it.get("url") or ""
            lines.append(f"[{i}] {title}\n    {snippet}\n    来源: {src}")
        return "\n".join(lines)
    except Exception as e:
        return f"（搜索异常：{type(e).__name__}）"


REACT_SYS_PROMPT = """你是一个智能体，使用「思考-行动-观察」循环来解决用户的问题。

可用工具：
- web_search[搜索词]：联网搜索公开医学科普与指南信息。搜索词中不要包含用户姓名、身份证号、医保卡号或具体指标值。

严格按以下格式逐步推理：
Thought: 对当前情况的思考与下一步计划
Action: 工具调用（格式必须是 web_search[搜索词]；若已可直接回答，则写 Finish[最终回答]）
Observation: 工具返回结果（由系统填入，你不需要输出）

规则：
- 每次只输出一个 Thought 和一个 Action，不要自己编造 Observation；
- 最多搜索 3 次；不要编造搜索结果；
- 信息足够时输出 Action: Finish[最终回答]；
- Finish 的内容是直接说给用户听的自然语言中文回复：口语、体贴、结合用户所说的具体情况给建议；
- 用户消息中附带的「医院单据」原文就是当日的真实数据，回答中引用数值必须与单据逐字一致（可做必要的单位换算说明，如 mmol/L↔mg/dL）；
- 关于用户历史的信息，只有本轮对话中出现过的才算数；没有记录的就如实说没有记录，不要编造；
- 不要向用户提及工具、搜索或推理过程本身，Finish 内容是纯粹对用户说的话。"""

_THOUGHT_ACTION_RE = re.compile(
    r"Thought\s*[:：]\s*(?P<thought>[\s\S]*?)\n?\s*Action\s*[:：]\s*(?P<action>[\s\S]*)", re.I)
_WEB_SEARCH_RE = re.compile(r"web_search\s*\[(?P<q>[^\]]*)\]", re.I)
_FINISH_RE = re.compile(r"Finish\s*\[(?P<ans>[\s\S]*)\]", re.I)


def parse_react_step(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    m = _THOUGHT_ACTION_RE.search(text or "")
    action_part = m.group("action") if m else (text or "")
    thought = (m.group("thought").strip() if m else "")
    f = _FINISH_RE.search(action_part)
    if f:
        return thought, "finish", f.group("ans").strip()
    w = _WEB_SEARCH_RE.search(action_part)
    if w:
        return thought, "web_search", w.group("q").strip()
    return thought, None, None


def strip_protocol_prefixes(text: str) -> str:
    lines = []
    for ln in (text or "").splitlines():
        if re.match(r"^\s*(Thought|Action|Observation)\s*[:：]", ln, re.I):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def react_episode(client, question: str, history_msgs: List[Dict[str, str]],
                  max_steps: int = 5, max_searches: int = 3,
                  sys_prompt: Optional[str] = None,
                  log_fn=None) -> Dict[str, Any]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt or REACT_SYS_PROMPT}]
    msgs.extend(history_msgs)
    msgs.append({"role": "user", "content": question})

    steps: List[Dict[str, str]] = []
    web_queries: List[str] = []
    n_search = 0
    answer: Optional[str] = None

    for step_i in range(1, max_steps + 1):
        out = client.ask_chat(msgs, max_tokens=800, temperature=0.0)
        thought, act_type, payload = parse_react_step(out)
        steps.append({"step": step_i, "thought": thought[:300],
                      "action": f"{act_type}: {str(payload)[:150] if payload else ''}" or out[:150],
                      "raw": out[:600]})
        if log_fn:
            log_fn(f"      [react] step{step_i} action={act_type} payload={str(payload)[:60]}")
        if act_type == "finish":
            answer = payload or strip_protocol_prefixes(out)
            break
        if act_type == "web_search" and n_search < max_searches:
            n_search += 1
            web_queries.append(str(payload)[:150])
            obs = friday_web_search(payload)
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user", "content": f"Observation: {obs}"})
            continue
        if act_type == "web_search":
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user",
                         "content": "Observation: （已达搜索次数上限，请基于现有信息回答）"})
            continue
        msgs.append({"role": "assistant", "content": out})
        msgs.append({"role": "user",
                     "content": "Observation: （输出格式错误：未找到合法的 Action 行。"
                                "请输出 Action: web_search[搜索词] 或 Action: Finish[最终回答]）"})

    if answer is None:
        msgs.append({"role": "user",
                     "content": "请立即结束推理并给出最终回答：输出一行 Action: Finish[你要对用户说的话]。"})
        out = client.ask_chat(msgs, max_tokens=800, temperature=0.0)
        _, act_type, payload = parse_react_step(out)
        answer = payload if act_type == "finish" and payload else strip_protocol_prefixes(out)

    answer = (answer or "").strip()
    return {"answer": answer, "steps": steps, "web_queries": web_queries,
            "scratchpad": "\n".join(
                f"Thought: {s['thought']}\nAction: {s['action']}" for s in steps)}
