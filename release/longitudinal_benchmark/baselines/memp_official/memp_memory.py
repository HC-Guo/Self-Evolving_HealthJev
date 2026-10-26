# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

OFFICIAL_DIRECT_BUILD_PROMPT = '''
You are provided with a query and a trajectory taken to solve the query. The trajectory consists of multiple steps of thought, action and observation. 
Your task is to generate a workflow based on critical steps to help solve similar queries in the future.
A critical step is one that have a significant impact on fulfilling the query, the step action belongs to the set [FlightSearch, GoogleDistanceMatrix, AccommodationSearch, RestaurantSearch, AttractionSearch, CitySearch, NotebookWrite, Planner], and the action's outcome is successful and contributes positively to achieving the query.
Notice: Write the workflow as a natural, coherent paragraph (not as a bullet list or numbered steps). Use clear, concise language to describe what actions should be taken and in what general order.
-----EXAMPLE WORKFLOW----
To solve this query, begin by looking for available flights to the destination city and compare their arrival times. After choosing a suitable flight, check how far the airport is from the city center to plan your transportation. Then, search for accommodation options close to the main attractions or convenient transit. Once lodging is arranged, explore top-rated restaurants nearby and highlight a few must-visit attractions. Finally, organize all selected places and times into a daily plan to ensure a smooth and enjoyable trip.
-----EXAMPLE END----
Query:
{query}
Trajectory:
{trajectory}
Output the workflow without any explanation or context:
'''

EMBED_MODEL = os.environ.get("MEMB_EMBED_MODEL", "doubao-embedding")
RETRIEVE_NUM = 3
RETRIEVE_THRESHOLD = 0.5


def _embed(client, text: str) -> List[float]:
    import requests

    client.limiter.acquire()
    r = requests.post(
        client.base_url + "/embeddings",
        headers={"Authorization": f"Bearer {client.api_key}", "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": text[:2000]},
        timeout=(15, 120), proxies={"http": None, "https": None},
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def _cos(a: List[float], b: List[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1e-9
    db = math.sqrt(sum(y * y for y in b)) or 1e-9
    return num / (da * db)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MempMemory:
    def __init__(self, client, memory_dir: Path, log_fn=None):
        self.client = client
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.docs_path = self.memory_dir / "documents.json"
        self.documents: List[Dict[str, Any]] = []
        if self.docs_path.exists():
            try:
                self.documents = json.loads(self.docs_path.read_text(encoding="utf-8"))
            except Exception:
                self.documents = []
        self.log = log_fn or (lambda msg: None)

    def build(self, query: str, trajectory: str) -> str:
        prompt = OFFICIAL_DIRECT_BUILD_PROMPT.format(query=query, trajectory=trajectory)
        workflow = self.client.ask_chat(
            [{"role": "system", "content": "You are a helpful assistant."},
             {"role": "user", "content": prompt}],
            max_tokens=800, temperature=0.2)
        return (workflow or "").strip()

    def update_vanilla(self, query: str, trajectory: str, day: int) -> Optional[Dict[str, Any]]:
        for d in self.documents:
            if d.get("query") == query:
                self.log(f"    [memp] duplicate query skipped (day={day})")
                return None
        workflow = self.build(query, trajectory)
        if not workflow:
            self.log(f"    [memp] empty workflow (day={day})")
            return None
        doc = {"id": f"doc-{day:03d}", "day": day, "query": query[:500], "workflow": workflow[:2000],
               "embedding": None, "created_at": _now_iso()}
        self.documents.append(doc)
        self._save()
        return doc

    def retrieve(self, query: str, top_k: int = RETRIEVE_NUM,
                 threshold: float = RETRIEVE_THRESHOLD) -> List[Dict[str, Any]]:
        if not self.documents:
            return []
        q_emb = _embed(self.client, query)
        scored = []
        for d in self.documents:
            if not d.get("embedding"):
                d["embedding"] = _embed(self.client, d.get("query", ""))
                self._save()
            scored.append((_cos(q_emb, d["embedding"]), d))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = [(s, d) for s, d in scored[:top_k] if s >= threshold]
        for s, d in hits:
            d["last_hit_score"] = round(s, 4)
        if hits:
            self._save()
        return [d for _, d in hits]

    def _save(self):
        self.docs_path.write_text(json.dumps(self.documents, ensure_ascii=False, indent=1),
                                  encoding="utf-8")

    def stats(self) -> Dict[str, Any]:
        return {"n_docs": len(self.documents)}

    def blob_text(self) -> str:
        return "\n".join(d.get("workflow", "") for d in self.documents)


def render_day_trajectory(turn_records: List[Dict[str, Any]], max_chars: int = 6000) -> str:
    lines = []
    for i, t in enumerate(turn_records, 1):
        lines.append(f'{{"step": {i}, "thought": {json.dumps(t.get("user", "")[:160], ensure_ascii=False)}'
                     f' /* user query */}}')
        for s in t.get("steps", [])[:4]:
            lines.append(f'{{"step": {i}, "thought": {json.dumps(str(s.get("thought", ""))[:200], ensure_ascii=False)}, '
                         f'"action": {json.dumps(str(s.get("action", ""))[:200], ensure_ascii=False)}}}')
        lines.append(f'{{"step": {i}, "action": "Reply to user", '
                     f'"observation": {json.dumps(str(t.get("answer", ""))[:300], ensure_ascii=False)}}}')
    text = "\n".join(lines)
    return text[:max_chars]
