# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

SEED = 20260705
HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
E2_DIR = SCRIPTS_DIR.parent
DATA_DIR = Path(os.environ.get("HEALTHJEV_BIOMEDICAL_DATA_DIR")
                or str(E2_DIR / "data")).expanduser()


def source_dir(cli_src, env_key: str, default_subdir: str) -> Path:
    if cli_src:
        return Path(cli_src).expanduser()
    override = os.environ.get(env_key)
    if override:
        return Path(override).expanduser()
    return DATA_DIR / default_subdir


def require_exists(path: Path, what: str) -> Path:
    if not path.exists():
        raise SystemExit(f"[split] missing {what}: {path}\n"
                         f"        将第三方数据集放置到该路径，或用 --src / "
                         f"HEALTHJEV_<TASK>_SRC 指向真实数据源目录")
    return path


def stratified_take(items: Sequence[Any], key_fn: Callable[[Any], str], n: int,
                    rng: random.Random) -> List[Any]:
    if n >= len(items):
        return list(items)
    groups: Dict[str, List[int]] = defaultdict(list)
    for i, it in enumerate(items):
        groups[str(key_fn(it))].append(i)
    total = len(items)
    raw = {k: n * len(v) / total for k, v in groups.items()}
    alloc = {k: int(v) for k, v in raw.items()}
    rem = n - sum(alloc.values())
    fracs = sorted(groups, key=lambda k: raw[k] - alloc[k], reverse=True)
    for k in fracs[:rem]:
        alloc[k] += 1
    chosen: List[int] = []
    for k, v in groups.items():
        if alloc[k] > 0:
            pick = rng.sample(v, min(alloc[k], len(v)))
            chosen.extend(pick)
    chosen.sort()
    return [items[i] for i in chosen]


def group_isolated_split(rows: List[Dict[str, Any]], group_key: str,
                         n_accum: int, n_test: int, strat_key: str,
                         seed: int = SEED) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r[group_key])].append(r)
    glist = list(groups.keys())
    rng.shuffle(glist)

    def _pick(target: int, pool: List[str]) -> List[str]:
        picked, cnt = [], 0
        for g in pool:
            if cnt >= target:
                break
            picked.append(g)
            cnt += len(groups[g])
        return picked

    test_groups = _pick(n_test, glist)
    remaining = [g for g in glist if g not in set(test_groups)]
    accum_groups = _pick(n_accum, remaining)
    out: List[Dict[str, Any]] = []
    for g in accum_groups:
        out.extend(groups[g])
    return out


def stratified_group_split(rows: List[Dict[str, Any]], group_key: str,
                           n_accum: int, n_test: int, strat_key: str,
                           seed: int = SEED):
    rng = random.Random(seed)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r[group_key])].append(r)
    gmain = {g: Counter(str(r[strat_key]) for r in rs).most_common(1)[0][0]
             for g, rs in groups.items()}
    by_layer: Dict[str, List[str]] = defaultdict(list)
    for g, m in gmain.items():
        by_layer[m].append(g)
    for m in by_layer:
        rng.shuffle(by_layer[m])

    def _pick_layered(target: int, pool_groups: List[str]) -> List[str]:
        pool = set(pool_groups)
        tol = max(3, int(round(target * 0.05)))
        picked: List[str] = []
        cnt = 0
        pool_size = sum(len(groups[g]) for g in pool_groups)
        layers = sorted(by_layer,
                        key=lambda m: -sum(len(groups[g]) for g in by_layer[m]
                                           if g in pool))
        for m in layers:
            if cnt >= target:
                break
            avail = [g for g in by_layer[m] if g in pool]
            layer_total = sum(len(groups[g]) for g in avail)
            want = int(round(target * layer_total / max(1, pool_size)))
            take, c = [], 0
            deferred: List[str] = []
            for g in avail:
                if c >= want:
                    break
                size_g = len(groups[g])
                if c + size_g <= want + tol:
                    take.append(g)
                    c += size_g
                else:
                    deferred.append(g)
            if c < want and deferred:
                take.append(deferred[0])
                c += len(groups[deferred[0]])
            picked.extend(take)
            cnt += c
        if cnt < target - tol:
            used = set(picked)
            for g in pool_groups:
                if cnt >= target:
                    break
                if g not in used:
                    picked.append(g)
                    cnt += len(groups[g])
        return picked

    pool = list(groups.keys())
    test_groups = _pick_layered(n_test, pool)
    rest = [g for g in pool if g not in set(test_groups)]
    accum_groups = _pick_layered(n_accum, rest)
    accum = [r for g in accum_groups for r in groups[g]]
    test = [r for g in test_groups for r in groups[g]]
    return accum, test


def write_manifest(task: str, accum_rows: List[Dict[str, Any]],
                   test_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out_dir = DATA_DIR / task
    out_dir.mkdir(parents=True, exist_ok=True)
    stats: Dict[str, Any] = {}
    for split, rows in (("accumulate", accum_rows), ("test", test_rows)):
        ids = [r["case_id"] for r in rows]
        assert len(ids) == len(set(ids)), f"{task} {split}: duplicated case_id"
        with open(out_dir / f"manifest_{split}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                rec = {"case_id": r["case_id"], "payload": r["payload"],
                       "gold": r["gold"], "split": split}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        keys = [r["case_id"] for r in rows]
        iso_field = rows[0].get("_iso_field") if rows else None
        n_iso = 0
        if iso_field:
            n_iso = len({str(r["payload"].get(iso_field)) for r in rows})
        print(f"[split:{task}] {split}: n={len(rows)}"
              + (f" unique_{iso_field}={n_iso}" if iso_field else ""))
    return stats
