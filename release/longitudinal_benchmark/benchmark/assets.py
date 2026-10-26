from __future__ import annotations

import json
import os
import warnings
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AssetsUnavailableError(FileNotFoundError):
    pass


def _read_json(path: Path):
    if not path.is_file():
        raise AssetsUnavailableError(
            f"Required asset missing: {path}. Data are not included with this release; "
            "licensed data required. Supply authorized assets; empty clinical "
            "profiles or synthetic substitutes do not reproduce the benchmark.")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _controlled_file(root: Path, name: str, legacy: str) -> Path:
    path = root / name
    return path if path.is_file() else root / legacy


def _profile(data, sid: int) -> dict:
    rows = data.get("profiles", data) if isinstance(data, dict) else data
    if isinstance(rows, dict):
        row = rows.get(str(sid))
    else:
        found = [p for p in rows if int(p["subject_id"]) == sid]
        if len(found) != 1:
            raise ValueError("Expected exactly one authorized profile for the requested user")
        row = found[0]
    if not isinstance(row, dict) or int(row.get("subject_id", sid)) != sid:
        raise ValueError("Missing or inconsistent authorized profile")
    for key in ("demo", "persona", "clinical"):
        if not isinstance(row.get(key), dict) or not row[key]:
            raise ValueError(f"Profile requires nonempty safe filtered {key}; no synthetic fallback")
    return dict(row)


def _fact(row: dict, sid: int, day: int, source_sid: int) -> dict:
    if not isinstance(row, dict) or any(k not in row for k in ("category", "name", "value")):
        raise ValueError("Each fact requires category/name/value")
    if int(row.get("subject_id", source_sid)) != source_sid:
        raise ValueError("Fact subject_id does not match its user")
    if int(row.get("day_index", day)) != day or day < 1:
        raise ValueError("Fact day_index does not match its goals day")
    if not row["category"] or not row["name"] or row["value"] is None:
        raise ValueError("Fact category/name must be nonempty and value must not be null")
    return dict(row, subject_id=sid, day_index=day)


def load_user_assets(user_id, *, controlled_assets=None, require_day0=False):
    sid = int(user_id)
    if not 1 <= sid <= 20:
        raise ValueError("Release user_id must be a public ID in 1..20; private IDs are not published")
    root = Path(os.environ.get("HEALTHJEV_BENCHMARK_DATA", PROJECT_ROOT / "benchmark" / "data"))
    controlled = controlled_assets or os.environ.get("HEALTHJEV_CONTROLLED_ASSETS")
    if require_day0 and not controlled:
        raise AssetsUnavailableError("--with-day0 is a non-paper ablation and requires licensed "
                                     "controlled data via HEALTHJEV_CONTROLLED_ASSETS or --controlled-assets")
    goals = _read_json(root / "goals" / f"{sid}.json")
    if int(goals.get("subject_id", sid)) != sid or not goals.get("days"):
        raise ValueError("Goals require matching public subject_id and nonempty days")
    days = goals["days"]
    if [int(d["day"]) for d in days] != list(range(1, len(days) + 1)):
        raise ValueError("Goals days must be consecutive, starting at day 1")
    for day in days:
        if int(day.get("subject_id", sid)) != sid:
            raise ValueError("Goals day subject_id does not match public user_id")
    probes = _read_json(root / "governance_probes.json")
    traps = probes["traps"] if isinstance(probes, dict) else probes
    user_traps = [dict(t, subject_id=sid) for t in traps if int(t["subject_id"]) == sid]
    dayfacts = defaultdict(list)
    if controlled:
        private_root = Path(controlled)
        mapping_path = private_root / "private_user_ids.json"
        source_sid = sid
        if mapping_path.is_file():
            mapping = {int(k): int(v) for k, v in _read_json(mapping_path).items()}
            if set(mapping) != set(range(1, 21)) or len(set(mapping.values())) != 20:
                raise ValueError("Private ID mapping must be a bijection for public IDs 1..20")
            source_sid = mapping[sid]
        profile = _profile(_read_json(_controlled_file(
            private_root, "profiles.json", "user_profiles_v5.json")), source_sid)
        archives = _read_json(_controlled_file(
            private_root, "day0_archives.json", "day0_archives_v5.json"))
        archives = archives.get("archives", archives)
        archive = archives.get(str(source_sid))
        if not isinstance(archive, dict) or not archive:
            raise ValueError("Controlled data require a nonempty Day0 archive")
        if int(archive.get("subject_id", source_sid)) != source_sid:
            raise ValueError("Controlled archive subject_id does not match its user")
        archive = dict(archive)
        ledger = _controlled_file(private_root, "ledger.jsonl", "hidden_ledger_v5.jsonl")
        if not ledger.is_file():
            raise AssetsUnavailableError("Controlled ledger is not included; licensed data required")
        with ledger.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if int(row["subject_id"]) == source_sid:
                    day = int(row["day_index"])
                    if 1 <= day <= len(days):
                        dayfacts[day].append(_fact(row, sid, day, source_sid))
        mode = "exact-reference-mode"
    else:
        profile = _profile(_read_json(root / "personas.json"), sid)
        archive = {}
        for row in days:
            if "facts" not in row or not isinstance(row["facts"], list):
                raise ValueError("Release goals must explicitly provide a facts list for every day")
            day = int(row["day"])
            dayfacts[day].extend(_fact(f, sid, day, sid) for f in row["facts"])
        mode = "release-mode"
    profile["subject_id"] = sid
    archive.update(subject_id=sid, asset_mode=mode)
    goals = dict(goals, subject_id=sid, asset_mode=mode)
    warnings.warn(f"HealthJev {mode}: " + ("goals-only day facts; not full-ledger exact reproduction"
                  if mode == "release-mode" else "authorized full ledger and private ID mapping"),
                  UserWarning, stacklevel=2)
    return profile, archive, goals, user_traps, dayfacts
