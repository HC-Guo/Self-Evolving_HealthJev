
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .memory import FiveLayerMemory
from .tools import TOOLS_SCHEMA, ToolBox
from . import distill as _distill_mod

MAX_INTERNAL_TURNS = 8
MAX_TOKENS = 2000
TEMPERATURE = 0.0

SETTLE_PROMPT = """你是记忆蒸馏器。以下是任务 agent 刚完成的一个 case 的处理记录。

case 输入摘要：{question_brief}
agent 最终回答摘要：{answer_brief}
判分结果：{reward_str}{error_line}{gold_line}

本 case 之前已有的相关经验：
{similar}

结算规则：{mode_line}

请判断并提炼：这个 case 包含的"可复用经验"（方法/规则/避坑点）。
注意：经验必须是"条件→做法"的方法规则（可以包含本题的具体答案数值与细节——
标准答案已给出，对照复盘的学习记录属正常沉淀）。
判错的 case 必须对照标准答案复盘：错在哪、正确思路是什么，提炼为"错在哪→下次怎么办"
的教训（含具体对策，如单位/精度/格式/先验规则），
不要输出"综合判断/审慎核对"这类空洞反思；判对的 case 提炼具体有效做法。

scope 命名硬约束：scope 必须具体到本题实际使用的公式/量表/子任务名称
（如 Wells-DVT 评分、CHA2DS2-VASc 评分、CKD-EPI eGFR 计算、QTc Bazett 校正、
Cockcroft-Gault 肌酐清除率、Child-Pugh 分级），不同公式/量表严禁共用同一个 scope；
严禁使用"医学计算""XX计算场景"这类泛化标题——后续经验按 scope 分桶沉淀，
标题泛化会导致不同公式的经验被错误合并。

输出 STRICT JSON：{{"scope": "适用场景（≤20字，落到具体公式/量表名）", "points": "具体可执行经验（≤120字）", "reason": "一句话理由"}}
每个 case 都必须总结一条经验（判错→教训，判对→有效做法），不存在"无可学"的情况。"""

HOOK_PREINJECT = ("[系统提示] 本次对话涉及用户此前提及的事项；回答前先用 memory_recall 检索相关历史，"
                  "再结合检索结果作答；引用历史数值必须与检索结果一致。")

_GATE_HINT = ("[系统提示] 你尚未检索与用户提到的事项相关的历史（{hint}）。"
              "请先调用 memory_recall 检索，再给出最终回答。")

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_REF_RANGE_RE = re.compile(
    r"(正常参考范围|正常范围|参考范围|参考区间|正常值|参考值|范围为|控制在|保持在|维持在|目标|区间|范围)"
    r"\s*[:：]?\s*[\d.]+\s*[~～\-—–至到]\s*[\d.]+")
_URGENT_RE = re.compile(r"(就医|急诊|120|尽快[^。！？]{0,10}(医院|医生)|联系(医生|主治|大夫)|"
                        r"去(医院|看医生)|看医生|前往医院|转诊)")

_BUILTIN_METRICS = ("血糖", "血压", "收缩压", "血钾", "血钠", "肌酐", "尿酸", "血红蛋白",
                    "白细胞", "血小板", "ALT", "AST", "总胆红素", "白蛋白", "TSH", "尿素氮")

_DOWN_CLAIM_RE = re.compile(r"(降了|下降|回落|降了不少|降下来|恢复得?不错|好转|改善|降回)")
_UP_CLAIM_RE = re.compile(r"(上升|升高|涨了|变差|恶化|又高了|高上去了|升上去了)")


def _num_in_text(num_str: str, text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"(?<![\d.])" + re.escape(num_str) + r"(?![\d.])", text))


def _leading_float(s: str) -> Optional[float]:
    m = _NUM_RE.search(str(s))
    return float(m.group()) if m else None


class HealthClawAgent:
    def __init__(self, memory: FiveLayerMemory, toolbox: ToolBox, client,
                 max_internal_turns: int = MAX_INTERNAL_TURNS,
                 e2_mode: bool = False):
        self.memory = memory
        self.toolbox = toolbox
        self.client = client
        self.max_internal_turns = max_internal_turns
        self.e2_mode = e2_mode
        self.messages: List[Dict[str, Any]] = []
        self.day = 0
        self.is_hook_day = False
        self.hook_entities: List[str] = []
        self.gate_used = 0
        self.verify_used = 0
        self.verify_trace: List[Dict[str, Any]] = []
        self.turn_trace: List[Dict[str, Any]] = []

    def start_day(self, day: int, hook_day: bool, hook_entities: Optional[List[str]] = None):
        self.day = day
        self.is_hook_day = hook_day
        self.hook_entities = hook_entities or []
        self.messages = [{"role": "system", "content": self.memory.system_prompt(day)}]
        if hook_day:
            self.messages[0]["content"] += "\n\n" + HOOK_PREINJECT
        self.gate_used = 0
        self.verify_used = 0
        self.verify_trace = []
        self.turn_trace = []

    def respond(self, user_msg: str) -> str:
        query_text = re.sub(r"\[\[IMG:[^\]]*\]\]", "", user_msg or "")
        _retrieve_mode = (self.memory.frozen
                          or getattr(self.memory, "inject_mode", "auto") == "retrieve")
        if _retrieve_mode and self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = self.memory.system_prompt(self.day, question=query_text)
        self.messages.append({"role": "user", "content": user_msg})
        final_answer: Optional[str] = None
        for internal_turn in range(self.max_internal_turns):
            resp = self._chat_with_retry()
            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []
            if tool_calls:
                def _tc_history(tc: dict) -> dict:
                    out = {"id": tc["id"], "type": "function",
                           "function": {"name": tc["name"],
                                        "arguments": tc["arguments"]}}
                    for k, v in tc.items():
                        if k not in ("id", "type", "function", "name", "arguments"):
                            out[k] = v
                    return out
                self.messages.append({"role": "assistant", "content": content,
                                      "tool_calls": [_tc_history(tc) for tc in tool_calls],
                                      **(resp.get("msg_extra") or {})})
                for tc in tool_calls:
                    result = self.toolbox.dispatch(tc["name"], tc["arguments"], self.day)
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                          "content": json.dumps(result, ensure_ascii=False)[:2000]})
                self.turn_trace.append({"internal_turn": internal_turn, "tools": [t["name"] for t in tool_calls]})
                continue
            final_answer = content.strip()
            if not final_answer:
                self.messages.append({"role": "user", "content":
                                      "[系统提示] 你的上一条回复为空。请直接给出面向用户的最终回答，不要再调用任何工具。"})
                self.turn_trace.append({"internal_turn": internal_turn, "empty": True})
                continue
            self.messages.append({"role": "assistant", "content": final_answer})
            if (self.is_hook_day and self.gate_used < 1
                    and internal_turn < self.max_internal_turns - 1
                    and not self._hook_covered(final_answer)):
                self.gate_used += 1
                hint = "、".join(self.hook_entities[:3])
                self.messages.append({"role": "user", "content": _GATE_HINT.format(hint=hint)})
                self.turn_trace.append({"internal_turn": internal_turn, "gate": True})
                continue
            if (not self.e2_mode and self.verify_used < 1
                    and internal_turn < self.max_internal_turns - 1
                    and re.search(r"\d", final_answer)):
                issues = self._verify_answer(final_answer)
                if issues:
                    self.verify_used += 1
                    self.messages.append({"role": "user", "content": issues})
                    self.verify_trace.append({"internal_turn": internal_turn,
                                              "issues": issues[:400]})
                    self.turn_trace.append({"internal_turn": internal_turn, "verify": True})
                    continue
            break
        if final_answer is None:
            self.messages.append({"role": "user", "content":
                                  "[系统提示] 请立即给出面向用户的最终回答，不要再调用任何工具。"
                                  "直接输出最终答案文本。"})
            resp = self._chat_with_retry_forced()
            final_answer = (resp.get("content") or "").strip()
            if not final_answer:
                final_answer = self._best_effort_answer()
            self.messages.append({"role": "assistant", "content": final_answer})
        return final_answer

    def _chat_with_retry_forced(self) -> Dict[str, Any]:
        try:
            return self.client.chat(self.messages, tools=None,
                                    temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
        except Exception:
            return self.client.chat(self.messages, tools=None,
                                    temperature=TEMPERATURE, max_tokens=MAX_TOKENS)

    def _best_effort_answer(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "assistant" and (m.get("content") or "").strip():
                return m["content"].strip()
        return "抱歉，我需要再确认一下您的问题。"

    def _hook_covered(self, answer: str) -> bool:
        blob = answer + "\n" + "\n".join(self.toolbox.recalled_texts[-10:])
        return any(e and e in blob for e in self.hook_entities)

    def _verify_answer(self, answer: str) -> Optional[str]:
        issues: List[str] = []
        convo_text = "\n".join(str(m.get("content", "")) for m in self.messages
                               if m.get("role") in ("user", "assistant"))
        recalled_text = "\n".join(self.toolbox.recalled_texts[-10:])
        l2_metrics = self.memory.l2.get("recent_metrics") or {}

        unverified = []
        names = set(l2_metrics.keys()) | {m.lower() for m in _BUILTIN_METRICS}
        for name in names:
            if not name or len(name) < 2:
                continue
            pat = re.compile(re.escape(name) + r"[^\d。！？!?\n]{0,15}(\d+(?:\.\d+)?)", re.I)
            for mnum in pat.finditer(answer):
                seg = answer[max(0, mnum.start(1) - 16): mnum.end(1) + 8]
                if _REF_RANGE_RE.search(seg):
                    continue
                v_str = mnum.group(1)
                series_vals = []
                mv = l2_metrics.get(name)
                if isinstance(mv, dict):
                    series_vals.append(str(mv.get("value", "")))
                    series_vals.extend(str(h.get("value", "")) for h in (mv.get("history") or []))
                if (_num_in_text(v_str, convo_text)
                        or _num_in_text(v_str, recalled_text)
                        or any(_num_in_text(v_str, sv) for sv in series_vals)):
                    continue
                unverified.append(f"{name}={v_str}")
        if unverified:
            issues.append(
                "[系统校验] 以下数值在今日对话与记忆检索中都找不到出处，疑似凭印象给出："
                + "、".join(unverified[:6])
                + "。请修正：能通过记忆核实的改用核实后的数值；核实不了的不要给出具体数字，"
                  "改为描述趋势并请用户再确认（例如：'我这边记录不太确定，你再说一下上次的数值？'）。"
                  "修正后重新给出完整回答。")

        user_text = "\n".join(str(m.get("content", "")) for m in self.messages
                              if m.get("role") == "user")
        crit = self._critical_findings(user_text)
        if crit and not _URGENT_RE.search(answer):
            issues.append(
                "[系统校验] 用户今天报告的数值已达到需要紧急处理的水平："
                + "；".join(crit[:4])
                + "。但你的回答没有给出明确的就医/联系医生建议。请修正：明确指出该数值异常"
                  "（说明超出了什么范围）、建议尽快就医或联系主治医生，不要用'平稳/正常'带过，"
                  "修正后重新给出完整回答。")

        for name, mv in l2_metrics.items():
            if not isinstance(mv, dict) or len(mv.get("history") or []) < 1:
                continue
            latest = _leading_float(mv.get("value", ""))
            prev = _leading_float((mv.get("history") or [-1])[-1].get("value", ""))
            if latest is None or prev is None or prev == 0:
                continue
            ratio = latest / prev
            if ratio > 5 or ratio < 0.2:
                continue
            if abs(ratio - 1.0) < 0.05:
                continue
            for m in re.finditer(re.escape(name), answer, re.I):
                ctx = answer[max(0, m.start() - 25): m.end() + 30]
                if ratio > 1 and _DOWN_CLAIM_RE.search(ctx):
                    issues.append(
                        f"[系统校验] {name} 的记录趋势是上升（第{(mv.get('history') or [{}])[-1].get('day')}"
                        f"天{(mv.get('history') or [{}])[-1].get('value')} → 第{mv.get('day')}天"
                        f"{mv.get('value')}），但你的回答却说它下降/好转/恢复，方向说反了。"
                        "请按记忆记录的真实趋势修正表述（如指标在恶化应如实说明并建议就医），"
                        "修正后重新给出完整回答。")
                    break
                if ratio < 1 and _UP_CLAIM_RE.search(ctx):
                    issues.append(
                        f"[系统校验] {name} 的记录趋势是下降，但你的回答却说它上升/恶化，方向说反了。"
                        "请按记忆记录的真实趋势修正表述，修正后重新给出完整回答。")
                    break
        if not issues:
            return None
        return "\n".join(dict.fromkeys(issues))[:1500]

    @staticmethod
    def _critical_findings(user_text: str) -> List[str]:
        findings = []
        if not user_text:
            return findings
        for m in re.finditer(r"血糖[^\d。！？!?\n]{0,12}(\d+(?:\.\d+)?)", user_text):
            v = float(m.group(1))
            win = user_text[max(0, m.start() - 8): m.end() + 8]
            mg = bool(re.search(r"mg", win, re.I))
            if (mg and (v < 70 or v > 300)) or (not mg and (v < 3.9 or v > 16.7)):
                findings.append(f"血糖 {m.group(1)}{'mg/dL' if mg else 'mmol/L'}（危急水平）")
        for m in re.finditer(r"(?:收缩压|血压)[^\d。！？!?\n]{0,8}(\d{2,3})\s*[/／]\s*(\d{2,3})", user_text):
            sys_v = float(m.group(1))
            if sys_v > 180 or sys_v < 90:
                findings.append(f"收缩压 {m.group(1)} mmHg（危急水平）")
        for m in re.finditer(r"血钾[^\d。！？!?\n]{0,12}(\d+(?:\.\d+)?)", user_text):
            if float(m.group(1)) > 6.0:
                findings.append(f"血钾 {m.group(1)} mmol/L（>6.0危急）")
        for name in ("ALT", "AST", "转氨酶"):
            for m in re.finditer(name + r"[^\d。！？!?\n]{0,12}(\d{3,5})(?:\.\d+)?", user_text, re.I):
                if float(m.group(1)) > 500:
                    findings.append(f"{name} {m.group(1)} IU/L（显著升高，需紧急评估）")
        for m in re.finditer(r"血小板[^\d。！？!?\n]{0,12}(\d+(?:\.\d+)?)", user_text):
            if float(m.group(1)) < 20:
                findings.append(f"血小板 {m.group(1)}（<20 严重偏低，出血风险）")
        for m in re.finditer(r"白细胞[^\d。！？!?\n]{0,12}(\d+(?:\.\d+)?)", user_text):
            if float(m.group(1)) < 1.0:
                findings.append(f"白细胞 {m.group(1)}（<1.0 严重粒细胞缺乏，感染风险）")
        return findings[:6]

    def _chat_with_retry(self) -> Dict[str, Any]:
        try:
            return self.client.chat(self.messages, tools=self.toolbox.schemas(),
                                    temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
        except Exception:
            return self.client.chat(self.messages, tools=self.toolbox.schemas(),
                                    temperature=TEMPERATURE, max_tokens=MAX_TOKENS)

    def settle_case(self, case_idx: int, question_brief: str, answer_brief: str,
                    reward: float, error_hint: Optional[str] = None,
                    used_tools: bool = False, judge_client=None,
                    gold: Optional[str] = None,
                    tool_calls: Optional[int] = None) -> Dict[str, Any]:
        record: Dict[str, Any] = {"case": case_idx, "reward": reward, "settled": False}
        if not self.memory.frozen:
            similar = self.memory.index.query(
                f"{question_brief[:120]} {error_hint or ''}", current_day=case_idx, top_k=3)
            similar_text = "\n".join(f"- {h.get('text', '')[:120]}" for h in similar) or "（无）"
            must = reward < 0.5
            if must:
                mode_line = ("该 case 判错：对照标准答案复盘（错在哪、正确思路是什么），"
                             "必须沉淀一条教训类经验（错在哪→下次怎么办，含具体对策）。")
            else:
                mode_line = "该 case 判对：必须沉淀一条有效做法类经验（具体可复用的思路/步骤/格式）。"
            gold_line = (f"\n本题标准答案（复盘用）：{str(gold)[:1000]}" if gold else "")
            prompt = SETTLE_PROMPT.format(
                question_brief=question_brief[:9500], answer_brief=answer_brief[:2000],
                reward_str=f"{reward:.2f}（{'' if must else '正确'}）",
                error_line=(f"\n错误归因（judge）：{error_hint}" if error_hint else ""),
                gold_line=gold_line,
                similar=similar_text, mode_line=mode_line)
            raw = self.client.ask_chat(
                [{"role": "system", "content": "You are a careful memory distiller. Output STRICT JSON only."},
                 {"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.0, json_mode=True)
            obj: Dict[str, Any] = {}
            try:
                txt = (raw or "").strip()
                if txt.startswith("```"):
                    txt = txt.strip("`")
                    if txt.startswith("json"):
                        txt = txt[4:]
                obj = json.loads(txt[txt.find("{"): txt.rfind("}") + 1], strict=False)
            except Exception:
                obj = {}
            scope = str(obj.get("scope") or "").strip()
            points = str(obj.get("points") or "").strip()
            if not (scope and points):
                if must:
                    scope, points = "错题教训", str(error_hint or answer_brief)[:280]
                else:
                    scope, points = "有效做法", f"本题作答正确的可复用思路：{answer_brief[:180]}"
            record["reason"] = str(obj.get("reason", ""))[:200]
            jres = _distill_mod.judge_case_experience(
                judge_client, scope=scope, points=points,
                question=question_brief, answer=answer_brief,
                reward=reward, gold=gold, error_hint=error_hint,
                tool_calls=tool_calls, kind="per_case", case_idx=case_idx)
            record["judge"] = {"approved": jres["approved"], "reason": jres["reason"],
                               "unverified": jres["unverified"]}
            res = self.memory.add_sop_entries(
                [{"scope": scope, "points": points, "source_day": case_idx,
                  "utility": 0.5 if jres["approved"] else -0.5}],
                per_case=True)
            record["settled"] = True
            record["actions"] = res.get("actions", [])
            record["lesson"] = points[:300]
            if not jres["approved"]:
                record["judge_rejected"] = True
        self.memory.write_episode(
            case_idx, facts=[question_brief[:160]], user_concerns=[answer_brief[:160]],
            open_items=[f"判分：{reward:.2f}" + (f"；错误归因：{error_hint}" if error_hint else "")],
            reward=reward, error_hint=error_hint, lesson=record.get("lesson"))
        return record
