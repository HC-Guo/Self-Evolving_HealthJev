# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm_client import LLMClient, cosine

CONSTITUTION = """你是用户的个人健康管家。你陪伴用户长期管理健康，记得用户的情况，给出贴合用户本人的建议。

【记忆规则】
1. 回答涉及用户此前提及的事项时，先用 memory_recall 检索相关历史，再作答；先查后答。
2. 引用任何指标/用药/时间信息前，与检索结果核对；同一指标以最新日期的记录为准（时效对齐）。
3. 用户告知新的健康信息（用药调整、化验结果、测量值、过敏、重要生活事件）后，
   在回答之余用 memory_write 记入档案；写入前与用户口述/单据内容核对，单位与数值保持一致。
4. 用户明确要求不记录的内容，不写入档案，口头接住即可。
5. 无验证不引用：凡引用用户历史数值/日期/用药方案，必须先 memory_recall 取到原文；
   取不到时如实说“我这边记录不确定，你再和我说一下”，绝不凭印象给出具体数值。

【表达规范】
1. 直接回答用户的问题本身；不暴露内部过程（不提工具名、检索过程、记忆写入状态）。
2. 不向用户索要其已提供过的信息。
3. 不复读机：不重复上一轮说过的话；每次回应推进对话。
4. 说话自然口语化，贴合用户的关注点；先回应人，再谈指标。
5. 化验/多项指标解读：逐项回应（数值→参考范围高低→结合病史的建议），
   不笼统带过；危急值立即建议就医，不做模糊化处理。
6. 恶化指标不安慰：指标较既往明显恶化时，如实指出变化并建议就医/联系医生，
   禁止用“都在安全范围/控制得不错/恢复得挺好”带过。
7. 行为建议安全检查：给饮食/运动/生活建议前，对照病史与在用用药——
   糖尿病控糖用户不主动建议备含糖零食（低血糖处置除外）；化疗/免疫抑制期用户
   避免推荐增加感染风险的行为；透析/肾病用户注意限水限钾限磷。与病情矛盾的建议不出。

【安全与隐私底线】
1. 危急值识别清单（双单位）：血糖 <3.9 mmol/L（≈70 mg/dL）或 >16.7 mmol/L（≈300 mg/dL）；
   血钾 >6.0 mmol/L；收缩压 >180 或 <90 mmHg；肌酐较近期记录急剧翻倍；
   尿酸 >540 μmol/L（≈9.0 mg/dL）。出现危急值：立即建议就医/联系医生，不做模糊化处理。
2. 不做诊断结论；给出建议方向并提示就医边界。
3. 未经用户本人授权，不向任何第三方（家人/同事/保险等）披露具体健康信息；
   对外材料只给最小必要披露，身份证号等强身份字段脱敏。
4. 联网搜索时不把用户姓名、身份证号、精确指标值放入搜索词。"""

CAPABILITY_INDEX = """【能力索引】
- memory_recall(query)：语义检索你的长期记忆（档案/每日纪要/经验），返回最相关的历史片段。
- memory_write(...)：更新个人档案（L2）或追加纪要；仅限本管家场景，无需用户确认技术细节。
- web_search(query)：查询公开健康知识（不带入用户隐私）。
- 记忆覆盖：个人档案（在用用药/最近指标/过敏/病史/偏好）、每日纪要（L4）、管家经验（L3）。"""

E2_CONSTITUTION = """你是一个严谨的任务执行助手，长期处理同类任务，从历史经验中持续改进。

【记忆规则】
1. 涉及以往案例/数值/结论时，先用 memory_recall 检索相关经验与历史，再作答；先查后答。
2. 引用历史数值/结论前必须与检索结果核对；无验证不引用：检索不到就如实说明，绝不凭印象给出。
3. 本 case 中得到验证的做法、遇到的坑，会在收尾结算时由系统沉淀为可复用经验。

【表达规范】
1. 直接回答问题本身；不暴露内部过程（不提工具名、检索过程、记忆写入状态）。
2. 答案需给出明确、可判定的结果（数值/选项/结论），不含糊。
3. 使用工具时先核对工具输出与题面的一致性；工具输出与自身判断矛盾且工具置信度低时，
   以谨慎复核后的结论为准，不盲目听信单一工具。
"""

E2_CAPABILITY_INDEX = """【能力索引】
- memory_recall(query)：语义检索你的长期记忆（历史案例纪要/沉淀经验），返回最相关片段。
- memory_write(...)：写入本 case 的工作记忆（结构化字段）。
- 任务专属工具：见工具清单；每个工具输出含可靠性标记，低置信度输出需自行复核。
- 记忆覆盖：案例纪要（L4）、沉淀经验（L3）。
"""

L2_SCHEMA_KEYS = {"medications", "recent_metrics", "allergies", "history", "preferences"}

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_METRIC_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def norm_metric_key(name: str) -> str:
    n = _METRIC_PAREN_RE.sub("", str(name))
    return re.sub(r"\s+", "", n).lower()[:40]


def norm_drug_key(drug: str) -> str:
    d = _METRIC_PAREN_RE.sub("", str(drug))
    return re.sub(r"\s+", "", d).lower()[:60]


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class SemanticIndex:
    def __init__(self, client: LLMClient, memory_dir: Path):
        self.client = client
        self.path = memory_dir / "index_chunks.json"
        self.chunks: List[Dict[str, Any]] = []
        if self.path.exists():
            try:
                self.chunks = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.chunks = []
        self._emb_cache_path = memory_dir / "index_embeddings.json"
        self._emb_cache: Dict[str, List[float]] = {}
        if self._emb_cache_path.exists():
            try:
                self._emb_cache = json.loads(self._emb_cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._emb_cache = {}

    def _embed(self, text: str) -> List[float]:
        key = _sha1(text)
        if key not in self._emb_cache:
            try:
                self._emb_cache[key] = self.client.embed(text)
            except Exception as exc:
                import sys
                print(f"[memory] embed failed, fallback to zero vector: {exc}",
                      file=sys.stderr, flush=True)
                self._emb_cache[key] = [0.0] * 1536
            self._emb_dirty = getattr(self, "_emb_dirty", 0) + 1
            if self._emb_dirty >= 16:
                self._save_embeddings()
                self._emb_dirty = 0
        return self._emb_cache[key]

    def add_chunk(self, text: str, day_index: int, layer: str, source: str,
                  name: str = "", value: str = "") -> Dict[str, Any]:
        if not text or len(text.strip()) < 4:
            return {}
        chunk = {"chunk_id": f"c{_sha1(layer + str(day_index) + text[:120])}",
                 "text": text[:600], "day_index": int(day_index), "layer": layer,
                 "source": source[:80], "name": str(name)[:40], "value": str(value)[:80],
                 "embedding": None}
        self.chunks = [c for c in self.chunks
                       if not (c["layer"] == layer and c["name"] and c["name"] == chunk["name"]
                               and layer == "L2")]
        self.chunks.append(chunk)
        self._save()
        return chunk

    def replace_chunk_by_source(self, layer: str, source: str, text: str, day_index: int) -> Dict[str, Any]:
        self.chunks = [c for c in self.chunks
                       if not (c["layer"] == layer and c.get("source") == source)]
        return self.add_chunk(text, day_index, layer, source)

    def query(self, query_text: str, current_day: int, top_k: int = 5,
              threshold: float = 0.35) -> List[Dict[str, Any]]:
        if not self.chunks:
            return []
        q_emb = self._embed(query_text)
        q_nums = set(NUM_RE.findall(query_text or ""))
        scored = []
        for c in self.chunks:
            if c.get("embedding") is None:
                c["embedding"] = self._embed(c["text"])
            s = cosine(q_emb, c["embedding"])
            recency = 1.0 - min(1.0, max(0, current_day - c["day_index"]) / 60.0)
            s += 0.15 * recency
            if c.get("value"):
                c_nums = set(NUM_RE.findall(c["value"]))
                if q_nums and (q_nums & c_nums):
                    s += 0.10
            if c.get("name") and c["name"] in (query_text or ""):
                s += 0.08
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"score": round(s, 4), **{k: c[k] for k in
                 ("chunk_id", "text", "day_index", "layer", "source", "name", "value")}}
                for s, c in scored[:top_k] if s >= threshold]

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.chunks, ensure_ascii=False), encoding="utf-8")

    def _save_embeddings(self):
        import os
        tmp = self._emb_cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._emb_cache), encoding="utf-8")
        os.replace(str(tmp), str(self._emb_cache_path))

    def stats(self) -> Dict[str, Any]:
        return {"n_chunks": len(self.chunks),
                "by_layer": {ly: sum(1 for c in self.chunks if c["layer"] == ly)
                             for ly in ("L2", "L3", "L4")}}


class FiveLayerMemory:
    E2_SOP_CAP = 600
    SIM_THRESHOLD = 0.72

    def __init__(self, client: LLMClient, memory_dir: Path, period_label: str = "天",
                 e2_mode: bool = False, inject_mode: str = "auto"):
        self.dir = Path(memory_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.index = SemanticIndex(client, self.dir)
        self.l2_path = self.dir / "L2_profile.json"
        self.l3_path = self.dir / "L3_sop.json"
        self.l4_dir = self.dir / "L4_episodes"
        self.l4_dir.mkdir(parents=True, exist_ok=True)
        self.e2_mode = e2_mode
        try:
            _umin = float(os.environ.get("E2_UTILITY_MIN", ""))
        except (TypeError, ValueError):
            _umin = 0.0
        self.UTILITY_INJECT_MIN = _umin if _umin > 0 else self.UTILITY_INJECT_MIN
        self.inject_mode = inject_mode
        self.constitution = E2_CONSTITUTION if e2_mode else CONSTITUTION
        self.capability_index = E2_CAPABILITY_INDEX if e2_mode else CAPABILITY_INDEX
        self.l2 = self._load_json(self.l2_path, self._empty_l2())
        if self.l3_path.exists():
            self.l3 = self._load_json(self.l3_path, [])
        else:
            self.l3 = [] if e2_mode else self._default_sop()
        self.frozen = False
        self.period_label = period_label

    @staticmethod
    def _empty_l2() -> Dict[str, Any]:
        return {"medications": [], "recent_metrics": {}, "allergies": [], "history": [],
                "preferences": []}

    @staticmethod
    def _default_sop() -> List[Dict[str, Any]]:
        return [
            {"id": "sop-1", "kind": "builtin", "scope": "慢病管理对话",
             "points": "先接住用户当下诉求；结合在用用药与最近指标给针对性建议；给出 1~2 条可执行动作；涉及危急值按清单处理。",
             "source_days": []},
            {"id": "sop-2", "kind": "builtin", "scope": "危急值识别清单",
             "points": "血糖<3.9或>16.7 mmol/L（≈70/300 mg/dL）、血钾>6.0 mmol/L、收缩压>180或<90 mmHg、肌酐急剧翻倍、尿酸>540 μmol/L（≈9.0 mg/dL）→ 立即建议就医，不模糊化。",
             "source_days": []},
            {"id": "sop-3", "kind": "builtin", "scope": "报告解读流程",
             "points": "先复述用户拿到的关键数值（含单位，必要时双单位换算）；逐项对照参考范围说明高低；结合病史与在用用药解读；最后给随访/饮食/就医建议。",
             "source_days": []},
        ]

    def _load_json(self, path: Path, default):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return default

    def write_profile(self, payload: Dict[str, Any], day: int) -> Dict[str, Any]:
        if self.frozen:
            return {"applied": [], "rejected": [{"key": "*", "reason": "frozen"}]}
        applied, rejected = [], []
        for key, val in (payload or {}).items():
            if key not in L2_SCHEMA_KEYS:
                rejected.append({"key": key, "reason": "schema 外字段"})
                continue
            if key == "medications":
                for m in val or []:
                    if isinstance(m, dict) and m.get("drug"):
                        new_key = norm_drug_key(m["drug"])
                        self.l2["medications"] = [
                            x for x in self.l2["medications"]
                            if not (norm_drug_key(x.get("drug", "")) == new_key
                                    or (new_key and new_key in norm_drug_key(x.get("drug", "")))
                                    or (new_key and norm_drug_key(x.get("drug", "")) in new_key))]
                        self.l2["medications"].append(
                            {"drug": str(m["drug"])[:60], "dose": str(m.get("dose", ""))[:80],
                             "start_day": int(m.get("start_day", day)),
                             "note": str(m.get("note", ""))[:120]})
                        applied.append(f"medications:{m['drug']}")
                        self.index.add_chunk(f"在用用药：{m['drug']} {m.get('dose', '')} {m.get('note', '')}"
                                             .strip(), day, "L2", "memory_write", name=m["drug"],
                                             value=f"{m.get('dose', '')}")
            elif key == "recent_metrics":
                for name, mv in (val or {}).items():
                    if isinstance(mv, dict) and mv.get("value") is not None:
                        k = norm_metric_key(name)
                        rec_day = int(mv.get("day", day))
                        new_val = str(mv["value"])[:60]
                        cur = self.l2["recent_metrics"].get(k)
                        if isinstance(cur, dict) and cur.get("value") is not None:
                            series = [h for h in (cur.get("history") or [])
                                      if isinstance(h, dict) and h.get("day") != rec_day]
                            series.append({"value": str(cur.get("value"))[:60],
                                           "day": int(cur.get("day", 0))})
                            series = sorted(series, key=lambda x: x.get("day", 0))[-8:]
                        else:
                            series = []
                        self.l2["recent_metrics"][k] = {
                            "value": new_val, "day": rec_day, "history": series}
                        applied.append(f"recent_metrics:{name}")
                        self.index.add_chunk(
                            f"最近指标 {name}：{new_val}（第{rec_day}天记录）"
                            + (f"；历史：" + "→".join(
                                f"第{h['day']}天{h['value']}" for h in series[-3:])
                               if series else ""),
                            rec_day, "L2", "memory_write",
                            name=str(name)[:40], value=str(mv["value"]))
            elif key == "allergies":
                for a in val or []:
                    a = str(a)[:80]
                    if a and a not in self.l2["allergies"]:
                        self.l2["allergies"].append(a)
                        applied.append(f"allergies:{a}")
                        self.index.add_chunk(f"过敏：{a}", day, "L2", "memory_write", name="过敏", value=a)
            elif key == "history":
                for h in val or []:
                    h = str(h)[:120]
                    if h and h not in self.l2["history"]:
                        self.l2["history"].append(h)
                        applied.append(f"history:{h[:20]}")
                        self.index.add_chunk(f"病史：{h}", day, "L2", "memory_write")
            elif key == "preferences":
                for p in val or []:
                    p = str(p)[:80]
                    if p and p not in self.l2["preferences"]:
                        self.l2["preferences"].append(p)
                        applied.append(f"preferences:{p[:20]}")
                        self.index.add_chunk(f"偏好：{p}", day, "L2", "memory_write")
        self._save_l2()
        return {"applied": applied, "rejected": rejected}

    def _save_l2(self):
        self.l2_path.write_text(json.dumps(self.l2, ensure_ascii=False, indent=1), encoding="utf-8")

    def _save_l3(self):
        self.l3_path.write_text(json.dumps(self.l3, ensure_ascii=False, indent=1), encoding="utf-8")

    SCOPE_SIM_THRESHOLD = 0.85
    POINTS_CAP = 1200

    @staticmethod
    def _norm_text(s: str) -> str:
        return re.sub(r"[\s\W_]+", "", str(s).lower())

    def _find_same_scope_bucket(self, scope: str):
        sn = self._norm_text(scope)
        for e in self.l3:
            if e.get("kind") == "distilled" and self._norm_text(e.get("scope", "")) == sn:
                return e
        try:
            q = self.index._embed(f"适用范围：{scope}")
        except Exception:
            return None
        for e in self.l3:
            if e.get("kind") != "distilled":
                continue
            try:
                sim = cosine(q, self.index._embed(f"适用范围：{e.get('scope', '')}"))
            except Exception:
                continue
            if sim >= self.SCOPE_SIM_THRESHOLD:
                return e
        return None

    @staticmethod
    def _merge_points(old: str, new: str) -> str:
        def split_pts(s: str) -> List[str]:
            parts = re.split(r"[；;\n]+", s)
            return [p.strip(" 　-.。•*；;\n") for p in parts if p.strip(" 　-.。•*；;\n")]
        pts = split_pts(old)
        existing_norm = [FiveLayerMemory._norm_text(x) for x in pts]
        for p in split_pts(new):
            pn = FiveLayerMemory._norm_text(p)
            if not pn:
                continue
            if pn in existing_norm:
                continue
            near = False
            for i, ex in enumerate(pts):
                if difflib.SequenceMatcher(None, pn, existing_norm[i]).ratio() > 0.8:
                    near = True
                    break
            if near:
                continue
            pts.append(p)
            existing_norm.append(pn)
        return "；".join(pts)[:FiveLayerMemory.POINTS_CAP]

    def _find_similar_distilled(self, text: str, threshold: Optional[float] = None):
        th = threshold if threshold is not None else self.SIM_THRESHOLD
        try:
            q = self.index._embed(text)
        except Exception:
            return None
        best_chunk, best_sim = None, 0.0
        for c in self.index.chunks:
            if c.get("layer") != "L3":
                continue
            if c.get("embedding") is None:
                c["embedding"] = self.index._embed(c["text"])
            sim = cosine(q, c["embedding"])
            if sim > best_sim:
                best_chunk, best_sim = c, sim
        if best_chunk is None or best_sim < th:
            return None
        sid = str(best_chunk.get("source", ""))
        if not sid.startswith("sop:"):
            return None
        for e in self.l3:
            if e.get("id") == sid[4:]:
                return e, best_sim
        return None

    def add_sop_entries(self, entries: List[Dict[str, Any]], kind: str = "distilled",
                        source_day: Optional[int] = None,
                        per_case: bool = False) -> Dict[str, Any]:
        if self.frozen:
            return {"actions": [], "reason": "frozen"}
        actions: List[Dict[str, Any]] = []
        for e in entries:
            if not (e.get("scope") and e.get("points")):
                continue
            scope = str(e["scope"])[:60]
            points = str(e["points"])[:self.POINTS_CAP]
            day = int(e.get("source_day") or source_day or 0)
            if per_case:
                new_id = f"sop-d{len(self.l3) + 1}"
                self.l3.append({"id": new_id, "kind": kind, "scope": scope, "points": points,
                                "source_days": [day] if day else [], "n_updates": 0,
                                "utility": float(e.get("utility", 0.5)),
                                "case_id": e.get("case_id", "")})
                self.index.add_chunk(f"管家经验（适用：{scope}）：{points}", day or 0, "L3", f"sop:{new_id}")
                actions.append({"action": "create", "id": new_id, "scope": scope})
                continue
            ent = self._find_same_scope_bucket(scope)
            if ent:
                if e.get("replace"):
                    action = "replace"
                else:
                    points = self._merge_points(ent.get("points", ""), points)
                    action = "merge"
                ent["points"] = points
                src = set(int(x) for x in (ent.get("source_days") or []) if x)
                if day:
                    src.add(day)
                ent["source_days"] = sorted(src)[-10:]
                ent["n_updates"] = int(ent.get("n_updates", 0)) + 1
                text = f"管家经验（适用：{ent['scope']}）：{points}"
                self.index.replace_chunk_by_source("L3", f"sop:{ent['id']}", text, day or 0)
                actions.append({"action": action, "id": ent["id"], "scope": ent["scope"]})
            else:
                new_id = f"sop-d{len(self.l3) + 1}"
                self.l3.append({"id": new_id, "kind": kind, "scope": scope, "points": points,
                                "source_days": [day] if day else [], "n_updates": 0,
                                "utility": float(e.get("utility", 0.5))})
                self.index.add_chunk(f"管家经验（适用：{scope}）：{points}", day or 0, "L3", f"sop:{new_id}")
                actions.append({"action": "create", "id": new_id, "scope": scope})
        distilled = [x for x in self.l3 if x.get("kind") == "distilled"]
        if len(distilled) > self.E2_SOP_CAP:
            drop_ids = {x["id"] for x in distilled[:len(distilled) - self.E2_SOP_CAP]}
            self.l3 = [x for x in self.l3 if x["id"] not in drop_ids]
            self.index.chunks = [c for c in self.index.chunks
                                 if not (c["layer"] == "L3"
                                         and str(c.get("source", "")).startswith("sop:")
                                         and c["source"][4:] in drop_ids)]
            self.index._save()
        self._save_l3()
        return {"actions": actions}

    def write_episode(self, day: int, facts: List[str], user_concerns: List[str],
                      open_items: List[str], reward: Optional[float] = None,
                      error_hint: Optional[str] = None,
                      lesson: Optional[str] = None) -> Dict[str, Any]:
        if self.frozen:
            return {}
        ep = {"day": int(day),
              "facts": [str(x)[:160] for x in facts[:10]],
              "user_concerns": [str(x)[:160] for x in user_concerns[:6]],
              "open_items": [str(x)[:160] for x in open_items[:6]]}
        if reward is not None:
            ep["reward"] = float(reward)
        if error_hint:
            ep["error_hint"] = str(error_hint)[:160]
        if lesson:
            ep["lesson"] = str(lesson)[:300]
        (self.l4_dir / f"D{day:03d}.json").write_text(
            json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
        text = (f"第{day}天纪要：事实——{'；'.join(ep['facts']) or '无'}。"
                f"用户关注——{'；'.join(ep['user_concerns']) or '无'}。"
                f"未决——{'；'.join(ep['open_items']) or '无'}。")
        self.index.add_chunk(text, day, "L4", "episode")
        return ep

    def load_episode(self, day: int) -> Optional[Dict[str, Any]]:
        p = self.l4_dir / f"D{day:03d}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def recent_episodes(self, up_to_day: int, n_days: int = 10) -> List[Dict[str, Any]]:
        eps = []
        for d in range(max(1, up_to_day - n_days + 1), up_to_day + 1):
            ep = self.load_episode(d)
            if ep:
                eps.append(ep)
        return eps

    def render_l2(self) -> str:
        l2 = self.l2
        lines = ["【个人档案 L2】"]
        if l2["medications"]:
            lines.append("在用用药：" + "；".join(
                f"{m['drug']}({m.get('dose', '')})" for m in l2["medications"][:15]))
        else:
            lines.append("在用用药：（暂无记录）")
        if l2["recent_metrics"]:
            lines.append("最近指标（按记录日，含既往趋势）：")
            for name, mv in list(l2["recent_metrics"].items())[:20]:
                hist = mv.get("history") or [] if isinstance(mv, dict) else []
                trend = "；既往：" + "→".join(
                    f"第{h['day']}天{h['value']}" for h in hist[-3:]) if hist else ""
                lines.append(f"  - {name}: {mv['value']}（第{mv['day']}天）{trend}")
        if l2["allergies"]:
            lines.append("过敏：" + "、".join(l2["allergies"]))
        if l2["history"]:
            lines.append("病史：" + "；".join(l2["history"][:10]))
        if l2["preferences"]:
            lines.append("偏好：" + "；".join(l2["preferences"][:10]))
        return "\n".join(lines)

    RETRIEVAL_TOP_K = 6
    RETRIEVAL_GATE = 0.32
    UTILITY_INJECT_MIN = 0.0
    UTILITY_WEIGHT = 0.15

    def set_sop_utility(self, entry_id: str, utility: float) -> bool:
        if self.frozen:
            return False
        for e in self.l3:
            if e.get("id") == entry_id:
                e["utility"] = max(-1.0, min(1.0, float(utility)))
                self._save_l3()
                return True
        return False

    def render_l3(self, question: Optional[str] = None) -> str:
        entries = self.l3
        if question and (self.frozen or self.inject_mode == "retrieve") and entries:
            try:
                q = self.index._embed(question[:512])
                scored = []
                for e in entries:
                    if float(e.get("utility", 0.5)) < self.UTILITY_INJECT_MIN:
                        continue
                    try:
                        emb = self.index._embed(
                            f"管家经验（适用：{e.get('scope')}）：{e.get('points')}")
                        scored.append((cosine(q, emb)
                                       + self.UTILITY_WEIGHT * float(e.get("utility", 0.5)), e))
                    except Exception:
                        continue
                scored.sort(key=lambda x: -x[0])
                entries = [e for s, e in scored[:self.RETRIEVAL_TOP_K]
                           if s >= self.RETRIEVAL_GATE]
            except Exception:
                entries = [e for e in self.l3
                           if float(e.get("utility", 0.5)) >= self.UTILITY_INJECT_MIN]
        if not entries:
            return "【工作流程与经验 L3】（本题无匹配经验）"
        lines = ["【工作流程与经验 L3】"]
        for e in entries:
            tag = "内置SOP" if e.get("kind") == "builtin" else "经验"
            lines.append(f"- [{tag}] {e['scope']}：{e['points']}")
        return "\n".join(lines)

    def system_prompt(self, day: int, question: Optional[str] = None) -> str:
        return (f"{self.constitution}\n\n{self.capability_index}\n\n"
                f"当前是第 {day} 个{self.period_label}。\n\n{self.render_l2()}\n\n"
                f"{self.render_l3(question=question)}")

    def seed_from_day0(self, archive: Dict[str, Any], profile: Dict[str, Any]):
        base = archive.get("baseline_checkups") or []
        for c in base[:18]:
            if c.get("name") and c.get("value"):
                self.l2["recent_metrics"][norm_metric_key(c["name"])] = {
                    "value": str(c["value"])[:60], "day": 0, "history": []}
                self.index.add_chunk(f"基线指标 {c['name']}：{c['value']}（Day0 档案，接入时快照）",
                                     0, "L2", "day0_archive", name=str(c["name"]),
                                     value=str(c["value"]))
        for m in (archive.get("medications_at_window_start") or [])[:15]:
            drug = str(m)[:60]
            self.l2["medications"].append({"drug": drug, "dose": "", "start_day": 0, "note": "接入时在用"})
            self.index.add_chunk(f"在用用药：{drug}（Day0 接入档案）", 0, "L2", "day0_archive", name=drug)
        for a in profile.get("clinical", {}).get("allergies", []):
            al = f"{a.get('trigger', '')}（{a.get('reaction', '')[:60]}）"
            if al not in self.l2["allergies"]:
                self.l2["allergies"].append(al)
                self.index.add_chunk(f"过敏：{al}", 0, "L2", "day0_archive", name="过敏", value=al)
        for d in profile.get("clinical", {}).get("conditions", []):
            h = str(d)
            if h not in self.l2["history"]:
                self.l2["history"].append(h)
                self.index.add_chunk(f"病史：{h}", 0, "L2", "day0_archive")
        for cond in (archive.get("top_pre_window_diagnoses") or [])[:6]:
            t = str(cond.get("title", ""))[:80]
            if t and t not in self.l2["history"]:
                self.l2["history"].append(t)
        self._save_l2()

    def stats(self) -> Dict[str, Any]:
        return {"l2_metrics": len(self.l2["recent_metrics"]),
                "l2_meds": len(self.l2["medications"]),
                "l3_entries": len(self.l3), "frozen": self.frozen,
                "index": self.index.stats()}

    def export_frozen(self, path, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"L0": self.constitution, "L1": self.capability_index,
                   "L3": self.l3, "meta": meta or {},
                   "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, ensure_ascii=False, indent=1)
        p.write_text(raw, encoding="utf-8")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        Path(str(p) + ".sha256").write_text(digest, encoding="utf-8")
        return {"path": str(p), "sha256": digest,
                "n_entries": len(self.l3)}

    def load_frozen(self, path) -> Dict[str, Any]:
        p = Path(path)
        payload = json.loads(p.read_text(encoding="utf-8"))
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        expected = ""
        if Path(str(p) + ".sha256").exists():
            expected = Path(str(p) + ".sha256").read_text(encoding="utf-8").strip()
        if expected and expected != digest:
            raise RuntimeError(f"frozen repo checksum mismatch: {p}")
        self.l3 = payload.get("L3") or []
        self.frozen = True
        self.index.chunks = [c for c in self.index.chunks
                             if not (c.get("layer") == "L3"
                                     and str(c.get("source", "")).startswith("sop:"))]
        self.index._save()
        for e in self.l3:
            if e.get("kind") == "distilled":
                self.index.add_chunk(
                    f"管家经验（适用：{e['scope']}）：{e['points']}", 0, "L3", f"sop:{e['id']}")
        self._save_l3()
        return {"n_entries": len(self.l3), "sha256": digest,
                "meta": payload.get("meta", {})}
