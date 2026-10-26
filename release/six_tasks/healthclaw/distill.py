# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memory import FiveLayerMemory

CONSOLIDATE_INTERVAL_CASES = 20

CONSOLIDATE_PROMPT = """以下是某任务 agent 最近 {n} 个 case 的处理纪要（含判分结果与错误归因），
以及当前已有的经验条目（它们已逐 case 通过 judge 核验）。请做一次"经验二次聚合"：
1. 合并重复：多条经验讲同一件事时，合并为一条更完整的表述；
2. 修订矛盾：若某条已有经验与近期观察矛盾，输出修订后的表述，并置 "replace": true；
3. 泛化提升：把多条同桶细粒度经验归纳为更高阶的 SOP（保留关键限定条件，不丢失细节）；
4. 错题教训与成功做法同等重要：判错的 case 若暴露了可复用的坑（如取整/单位/格式/分布先验），
   必须提炼为"坑→对策"式教训，不要因为 case 本身答错就放弃沉淀；
5. 只依据纪要与已有经验归纳，不要编造；经验必须是"方法/规则/避坑点"
   （允许包含具体病例的答案数值与细节——复盘信息来自标准答案，属正常学习记录）；
6. 每条经验必须含【具体触发条件 + 具体做法】（如公式名/单位/精度/工具参数/标签判定规则/输出格式），
   严禁输出"综合判断/审慎核对/全面分析"这类无具体做法的方法论套话；
7. 可执行性必须相对纯文本 LLM 的能力边界：不得要求对序列/文本做字符级定位、精确数到第 N 个
   字符等模型做不到的操作（如"取第 60 位残基"），应改写为模型通读全文即可判断的线索与规则；
8. 若近期没有值得合并/修订/新增的内容，输出空列表。

近期 case 纪要：
{episodes}

当前已有经验条目（已过逐 case 核验）：
{l3_text}

输出 STRICT JSON：{{"entries":[{{"scope":"适用场景（≤20字）","points":"具体可执行经验（≤150字）","replace":false}}]}}
其中 "replace": true 仅在修订已有条目时使用（会整体替换同 scope 的旧要点）。"""

JUDGE_REVIEW_PROMPT = """你是判分审核员。以下是一条从交互轨迹中归纳出的"经验条目"，以及支撑它的
近期 case 纪要（含真实判分结果）。请核验：
1. 事实性（分两种情形核验，不要用单一标准）：
   - 教训类经验（来自判错 case）：核验教训是否与错误事实一致——错误归因指出的坑，
     教训是否针对它给出下次的对策；与错误事实矛盾、或教的是导致答错的做法→打回。
     "该 case 本身答错了"不是打回教训的理由；
   - 做法类经验（来自判对 case）：核验"有效做法"是否真实反映本题成功路径，不得虚构。
2. 泛化性：经验应是下次遇到同类型题还能用得上的方法规则
   （允许包含具体病例的答案数值与细节——复盘信息来自标准答案，属正常学习记录）；
3. 可执行性：经验必须含具体触发条件与做法（公式名/单位/精度/工具参数/标签规则/输出格式），
   纯方法论套话（如"综合判断""审慎作答"而无具体做法）→打回。

经验条目：scope={scope}；points={points}

支撑纪要：
{episodes}

输出 STRICT JSON：{{"verdict": "通过" 或 "打回", "reason": "一句话理由"}}"""

CASE_EXP_JUDGE_PROMPT = """你是经验审核员。agent 刚完成一道题并总结了一条"经验条目"，
请结合本题完整语境核验这条经验是否值得写入长期记忆：
1. 事实性（分两种情形核验，不要用单一标准）：
   - 本题判错时：经验应为"教训/避坑点"——核验教训是否与本题错误事实一致
     （错误归因指出的坑，教训是否针对它给出下次的具体对策）。与错误事实矛盾、
     或教的是导致本题答错的做法→打回；"本题答错了"本身不是打回教训的理由；
   - 本题判对时：核验"有效做法"是否真实反映本题成功路径，不得虚构本题未发生的步骤；
2. 泛化性：经验应是下次遇到同类型题还能用得上的方法规则
   （允许包含本题的具体答案数值与细节——复盘信息来自标准答案，属正常学习记录）；
3. 可执行性：经验必须含具体触发条件与做法（如公式名/单位/精度/工具参数/标签判定规则/
   输出格式），下次遇到同类型题能直接照做；纯方法论套话（如"综合判断""审慎作答"
   而无任何具体做法）→打回。

经验条目：scope={scope}；points={points}

本题语境：
- 题目：{question}
- agent 回答：{answer}
- 判分：{reward}{gold_line}{hint_line}

输出 STRICT JSON：{{"verdict": "通过" 或 "打回", "reason": "一句话理由"}}"""

_QUESTION_MAX = 9500
_GOLD_MAX = 1000
_ANSWER_MAX = 2000
_HINT_MAX = 200

_VAGUE_PHRASES = ("综合判断", "综合考量", "综合判定", "具体问题具体分析", "具体情况具体分析",
                  "审慎", "谨慎作答", "全面分析", "全面考虑", "认真分析", "多方考虑",
                  "结合实际情况", "权衡各", "综合评估")
_CONCRETE_RE = re.compile(
    r"\d|[A-Za-z]{2,}|先|再|然后|必须|不要|禁止|避免|优先|默认|除非|如果|若|当|检查|"
    r"核对|使用|调用|公式|单位|精度|保留|取整|工具|标签|输出|格式|阈值|参数", re.I)


def quality_gate(scope: str, points: str):
    pts = (points or "").strip()
    if len(pts) < 15:
        return False, "quality_gate: 过短，缺少可执行内容"
    vague_hits = [w for w in _VAGUE_PHRASES if w in pts]
    has_concrete = bool(_CONCRETE_RE.search(pts))
    if vague_hits and not has_concrete:
        return False, f"quality_gate: 空洞经验（仅含'{vague_hits[0]}'类泛化表述，无具体做法）"
    if not has_concrete:
        return False, "quality_gate: 无可执行动作标记（缺具体触发条件或做法）"
    return True, ""


def should_consolidate(case_idx: int) -> bool:
    return case_idx > 0 and case_idx % CONSOLIDATE_INTERVAL_CASES == 0


def _episodes_text(episodes: List[Dict[str, Any]]) -> str:
    lines = []
    for e in episodes:
        reward = e.get("reward")
        r_str = f"{reward:.2f}" if isinstance(reward, (int, float)) else "未记录"
        hint = f"；错误归因：{e['error_hint']}" if e.get("error_hint") else ""
        lesson = f"；教训：{e['lesson']}" if e.get("lesson") else ""
        lines.append(f"- case#{e['day']}：事实={'；'.join(e['facts']) or '无'}；"
                     f"回答要点={'；'.join(e['user_concerns']) or '无'}；"
                     f"判分={r_str}{hint}{lesson}")
    return "\n".join(lines)


def _parse_json(raw: str) -> Dict[str, Any]:
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:]
    try:
        return json.loads(txt[txt.find("{"): txt.rfind("}") + 1], strict=False)
    except Exception:
        return {}


def _parse_json_retry(raw: str, retry_fn=None) -> Dict[str, Any]:
    obj = _parse_json(raw)
    if obj:
        return obj
    if retry_fn is not None:
        try:
            obj = _parse_json(retry_fn())
        except Exception:
            return {}
    return obj


def judge_case_experience(judge_client, scope: str, points: str, question: str,
                          answer: str, reward: float, gold: Optional[str] = None,
                          error_hint: Optional[str] = None,
                          tool_calls: Optional[int] = None, kind: str = "per_case",
                          case_idx: Optional[int] = None) -> Dict[str, Any]:
    ok, greason = quality_gate(scope, points)
    if not ok:
        return {"approved": False, "reason": greason, "raw": "", "unverified": False}
    if judge_client is not None and hasattr(judge_client, "verify_experience"):
        return judge_client.verify_experience(
            {"scope": scope, "points": points, "reward": reward, "question": question,
             "gold": gold, "answer": answer, "error_hint": error_hint,
             "tool_calls": tool_calls, "kind": kind, "case_idx": case_idx})
    if judge_client is None:
        return {"approved": True, "reason": "no_judge", "raw": "", "unverified": True}
    prompt = CASE_EXP_JUDGE_PROMPT.format(
        scope=scope, points=points, question=str(question or "")[:_QUESTION_MAX],
        answer=str(answer or "")[:_ANSWER_MAX],
        reward=f"{reward:.2f}",
        gold_line=(f"\n- 标准答案（GT）：{str(gold)[:_GOLD_MAX]}" if gold else ""),
        hint_line=(f"\n- 错误归因：{error_hint}" if error_hint else ""))

    def _ask() -> Optional[str]:
        return judge_client.ask_block(prompt, max_tokens=300, temperature=0.0)

    raw1 = _ask()
    jobj = _parse_json(raw1)
    raw2 = None
    if not jobj:
        raw2 = judge_client.ask_block(
            prompt + "\n再次强调：只输出一个 JSON 对象，形如 {\"verdict\": \"通过\" 或 \"打回\", \"reason\": \"…\"}，"
                     "不要任何其他文字、不要代码围栏。",
            max_tokens=200, temperature=0.0)
        jobj = _parse_json(raw2)
    raw_all = f"[try1]{raw1}\n[try2]{raw2 or ''}"
    verdict = str(jobj.get("verdict", "")).strip()
    if not verdict:
        return {"approved": True, "reason": "parse_failed_degraded",
                "raw": raw_all[:1000], "unverified": True}
    approved = "通过" in verdict
    return {"approved": approved,
            "reason": str(jobj.get("reason", "") or verdict)[:200],
            "raw": raw_all[:1000], "unverified": False}


def consolidate(memory: FiveLayerMemory, case_idx: int, client,
                judge_client=None, log_dir: Optional[Path] = None,
                n_cases: int = 20) -> Dict[str, Any]:
    if memory.frozen:
        return {"case_idx": case_idx, "triggered": True, "reason": "frozen"}
    episodes = memory.recent_episodes(case_idx, n_days=n_cases)
    if not episodes:
        return {"case_idx": case_idx, "triggered": True, "n_entries": 0, "reason": "no_episodes"}
    ep_text = _episodes_text(episodes)
    l3_text = "\n".join(
        f"- [{e['scope']}] {e['points']}" for e in memory.l3 if e.get("kind") == "distilled") or "（暂无）"
    prompt = CONSOLIDATE_PROMPT.format(n=len(episodes), episodes=ep_text, l3_text=l3_text)
    raw = client.ask_chat(
        [{"role": "system", "content": "You are a careful memory distiller. Output STRICT JSON only."},
         {"role": "user", "content": prompt}],
        max_tokens=900, temperature=0.0, json_mode=True)
    obj = _parse_json(raw)
    candidates = [e for e in (obj.get("entries") or [])[:4]
                  if e.get("scope") and e.get("points")]

    approved, rejected = [], []
    judge_raws = []
    for e in candidates:
        gok, greason = quality_gate(e.get("scope", ""), e.get("points", ""))
        if not gok:
            rejected.append({"entry": e, "reason": greason, "raw": "quality_gate"})
            continue
        if judge_client is None:
            approved.append(e)
            continue
        if hasattr(judge_client, "verify_experience"):
            jres = judge_client.verify_experience(
                {"scope": e["scope"], "points": e["points"], "reward": None,
                 "question": "", "gold": "", "answer": "", "error_hint": "",
                 "tool_calls": None, "kind": "consolidate", "case_idx": case_idx})
            judge_raws.append({"scope": e["scope"], "raw": str(jres.get("raw", ""))[:1000]})
            if jres.get("approved"):
                approved.append(e)
            else:
                rejected.append({"entry": e,
                                 "reason": (jres.get("reason") or "jev_rejected")[:200]})
            continue
        jr1 = judge_client.ask_block(
            JUDGE_REVIEW_PROMPT.format(scope=e["scope"], points=e["points"], episodes=ep_text[:4000]),
            max_tokens=300, temperature=0.0)
        jobj = _parse_json(jr1)
        jr2 = None
        if not jobj:
            jr2 = judge_client.ask_block(
                JUDGE_REVIEW_PROMPT.format(scope=e["scope"], points=e["points"], episodes=ep_text[:4000])
                + "\n再次强调：只输出一个 JSON 对象，不要任何其他文字、不要代码围栏。",
                max_tokens=200, temperature=0.0)
            jobj = _parse_json(jr2)
        raw_all = f"[try1]{jr1}\n[try2]{jr2 or ''}"
        judge_raws.append({"scope": e["scope"], "raw": raw_all[:1000]})
        verdict = str(jobj.get("verdict", "")).strip()
        if not verdict:
            e2 = dict(e)
            e2["unverified"] = True
            approved.append(e2)
            rejected.append({"entry": e, "reason": "parse_failed_degraded（降级通过）",
                             "raw": raw_all[:300]})
        elif "通过" in verdict:
            approved.append(e)
        else:
            rejected.append({"entry": e,
                             "reason": (jobj.get("reason") or verdict or "未给出理由")[:200]})

    written = memory.add_sop_entries(approved, source_day=case_idx, per_case=True)
    record = {"case_idx": case_idx, "triggered": True,
              "input_cases": [e["day"] for e in episodes],
              "candidates": candidates, "approved": approved,
              "rejected": rejected, "judge_raws": judge_raws,
              "written_actions": written.get("actions", []),
              "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"consolidate_C{case_idx:05d}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def distill(memory: FiveLayerMemory, day: int, client, judge_client=None,
            log_dir: Optional[Path] = None, n_days: int = 20) -> Dict[str, Any]:
    return consolidate(memory, day, client, judge_client=judge_client, log_dir=log_dir, n_cases=n_days)


def should_distill(day: int) -> bool:
    return should_consolidate(day)
