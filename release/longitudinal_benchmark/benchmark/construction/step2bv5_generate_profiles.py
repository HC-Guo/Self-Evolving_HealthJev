# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SEED = 20260705
N_PATIENTS = 20
MODEL_DEFAULT = "Doubao-Seed-2.0-pro"

CONDITION_ZH = {
    "diabetes": "2型糖尿病", "hypertension": "高血压", "ckd": "慢性肾脏病",
    "copd": "慢阻肺", "coronary_heart": "冠心病", "atrial_fib": "房颤",
    "hyperthyroidism": "甲状腺功能亢进",
}
CONDITION_EN = {
    "diabetes": "type 2 diabetes", "hypertension": "hypertension", "ckd": "chronic kidney disease",
    "copd": "COPD", "coronary_heart": "coronary artery disease", "atrial_fib": "atrial fibrillation",
    "hyperthyroidism": "hyperthyroidism",
}

ALLERGY_BANK = [
    {"allergy_id": "allergy-1", "class": "青霉素类抗生素", "trigger": "阿莫西林",
     "reaction": "多年前服药后全身皮疹、喉咙发紧，曾去急诊",
     "rule": "避免使用青霉素类抗生素，止痛/退烧前先说明此过敏史"},
    {"allergy_id": "allergy-2", "class": "磺胺类抗生素", "trigger": "某磺胺类复方药",
     "reaction": "曾出现全身荨麻疹伴发烧",
     "rule": "避免磺胺类抗生素"},
    {"allergy_id": "allergy-3", "class": "阿司匹林/NSAID（阿司匹林敏感）", "trigger": "阿司匹林",
     "reaction": "曾一次服药后喘息并起荨麻疹",
     "rule": "用任何止痛或退烧药前先说明阿司匹林过敏"},
]

PERSONA_PROMPT = """你在为一个中文健康管理 App 的评测基准设计一名虚拟用户的画像。该用户在三个月里每天用 AI 健康助手记录和管理健康。

临床背景（固定，不可修改）：
- 年龄：{age}，性别：{gender}
- 慢性病：{conditions}
- 当前主要用药：{meds}

任务：为该用户设计身份与性格，使每天的对话像真实、具体的人。要求：
- occupation：符合年龄的具体职业（或"退休X"），能影响作息（吃饭时间、活动、压力）；
- living_with：家庭构成（和谁同住，1 句话）；
- caregiving：一位偶尔照顾或咨询的家人（亲属关系 + 对方的身体状况），不能是用户自己的病；
- tone：与助手说话的方式（2-3 个短句：话多话少、情绪风格、在意的点）；
- recording_habit：什么时间/方式记录健康数据（与职业作息挂钩）；
- style_tag：从 [mobile, office, retired, shift, homemaker] 中选一个英文单词；
- common_phrases：恰好 3 句第一人称口头禅（每句不超过 12 个字，中文，口语化）；
- dietary_preferences：3 条具体饮食习惯/偏好/忌口（与职业或背景相关，且与慢性病不冲突）；
- exercise_habits：2 条具体运动习惯（类型 + 时长）；
- sleep_pattern：1 句话；
- hobbies：1-2 个爱好或宠物；
- personality_tag：从 [anxious, steady, chatty, terse, laid-back, dutiful, self-experimenter, avoidant] 中选一个英文单词；
- three_month_goals：3 条第一人称健康目标（与病况一致，三个月尺度）。

硬性约束：
- 所有文本字段必须用中文书写（style_tag/personality_tag 两个枚举标签除外）；
- 不要与本排除列表重复（已占用的全部职业/性格标签，职业必须与其中任何一项都不同，且避免同类近义）：{excluded}；
- 不要出现真实姓名、绝对年份、可推断真实身份的城市名；
- 只输出 STRICT JSON，键：occupation, living_with, caregiving, tone, recording_habit, style_tag, common_phrases, dietary_preferences, exercise_habits, sleep_pattern, hobbies, personality_tag, three_month_goals。
"""


def _require_llm_libs():
    try:
        import httpx
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(f"本步骤需要 openai 与 httpx（当前环境缺失）：{exc}\n"
                         "可先离线查看用法：python3 step2bv5_generate_profiles.py --help")
    return httpx, OpenAI


def _require_api_key():
    app_id = (os.environ.get("HEALTHJEV_API_KEY") or os.environ.get("FRIDAY_APP_ID")
              or os.environ.get("OPENAI_API_KEY"))
    if not app_id:
        raise SystemExit(
            "缺少 LLM 凭据：请设置环境变量 HEALTHJEV_API_KEY（或 FRIDAY_APP_ID / OPENAI_API_KEY）。"
            "凭据不入库；可用 --base-url/--model 指定 OpenAI 兼容网关。")
    return app_id


def _guard_out_path(out_path: Path):
    p = Path(out_path).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit(f"refusing to write inside the release tree: {p}\n"
                         "构造产物含受保护数据的 subject_id，请指向发布树之外的用户受控路径。")


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="构造步骤 2b：用户画像生成（LLM；凭据走环境变量）")
    ap.add_argument("--ledger", required=True, help="hidden_ledger_v5.jsonl 路径（用户受控）")
    ap.add_argument("--ledger-summary", required=True, help="ledger_v5_summary.json 路径（用户受控）")
    ap.add_argument("--out", required=True,
                    help="输出 user_profiles_v5.json 路径（用户受控；不得位于发布树内）")
    ap.add_argument("--n-patients", type=int, default=N_PATIENTS,
                    help=f"画像数量上限（默认 {N_PATIENTS}）")
    ap.add_argument("--model", default=os.environ.get("HEALTHJEV_MODEL", MODEL_DEFAULT),
                    help=f"模型名（默认取环境变量 HEALTHJEV_MODEL 或 {MODEL_DEFAULT}）")
    ap.add_argument("--base-url",
                    default=(os.environ.get("HEALTHJEV_BASE_URL")
                             or os.environ.get("OPENAI_BASE_URL")
                             or "https://api.openai.com/v1"),
                    help="OpenAI 兼容网关地址（默认取 HEALTHJEV_BASE_URL / OPENAI_BASE_URL）")
    args = ap.parse_args()

    app_id = _require_api_key()
    httpx, OpenAI = _require_llm_libs()
    out_path = Path(args.out)
    _guard_out_path(out_path)

    client = OpenAI(base_url=args.base_url, api_key=app_id, timeout=240.0, max_retries=0,
                    http_client=httpx.Client(trust_env=False, timeout=240.0))

    def call_doubao(messages, temperature=0.8, max_tokens=900, max_retries=8):
        import time
        last = None
        for attempt in range(max_retries):
            try:
                resp = client.chat.completions.create(
                    model=args.model, messages=messages, temperature=temperature, max_tokens=max_tokens)
                return resp.choices[0].message.content
            except Exception as e:
                last = e
                print(f"    [retry {attempt + 1}/{max_retries}] {type(e).__name__}: {str(e)[:120]}", flush=True)
                time.sleep(min(45, 2 ** attempt * 2))
        raise RuntimeError(f"doubao call failed: {last}")

    t0 = datetime.now()
    facts = load_jsonl(args.ledger)
    by_sid = defaultdict(list)
    for f in facts:
        by_sid[f["subject_id"]].append(f)
    summary = json.load(open(args.ledger_summary, encoding="utf-8"))
    pre = summary["pre_window_summaries"]
    subjects = sorted(by_sid)[:args.n_patients]
    print(f"[Load] {len(subjects)} patients", flush=True)

    def clinical_summary(sid, pid):
        p = pre[str(sid)]
        conditions_zh = [CONDITION_ZH.get(c, c) for c in p["chronic_conditions"]]
        conditions_en = [CONDITION_EN.get(c, c) for c in p["chronic_conditions"]]
        labs = {}
        for f in sorted(by_sid[sid], key=lambda x: x["day_index"]):
            if f["category"] == "检查结果" and f["day_index"] > 0:
                labs[f["name"]] = f["value"][:40]
        return {
            "age": p["age_at_window_start"],
            "gender": "F" if p["gender"] == "F" else "M",
            "conditions": conditions_zh,
            "conditions_en": conditions_en,
            "current_meds": p["medications_active_at_window_start"][:5],
            "key_labs": dict(list(labs.items())[:6]),
        }

    profiles = []
    used_occupations, used_tags = [], []
    profiles_by_pid = {}
    if out_path.exists():
        old = json.load(open(out_path, encoding="utf-8"))
        for p in old.get("profiles", []):
            profiles_by_pid[p["pid"]] = p
            profiles.append(p)
            used_occupations.append(str(p["persona"].get("occupation", "")))
            used_tags.append(str(p["persona"].get("personality_tag", "")))
        print(f"[Resume] 已有 {len(profiles_by_pid)} 份画像，续跑剩余", flush=True)

    lock = threading.Lock()

    def gen_one(pid, sid):
        cs = clinical_summary(sid, pid)
        allergies = [ALLERGY_BANK[(pid + k) % len(ALLERGY_BANK)] for k in range(2)]
        if allergies[0]["allergy_id"] == allergies[1]["allergy_id"]:
            allergies = [ALLERGY_BANK[pid % 3], ALLERGY_BANK[(pid + 1) % 3]]
        last_err = None
        for _ in range(24):
            with lock:
                excluded = "; ".join(used_occupations) + " || 标签: " + ", ".join(used_tags)
            prompt = PERSONA_PROMPT.format(
                age=cs["age"], gender="女" if cs["gender"] == "F" else "男",
                conditions="、".join(cs["conditions"]) or "慢性病在管",
                meds="；".join(cs["current_meds"]) or "无",
                excluded=excluded,
            )
            obj = parse_json(call_doubao([
                {"role": "system", "content": "You are a careful benchmark designer. Output STRICT JSON only."},
                {"role": "user", "content": prompt},
            ]))
            occ = str(obj.get("occupation", "")).strip().lower()
            tag = str(obj.get("personality_tag", "")).strip().lower()
            with lock:
                taken_occs = {o.strip().lower() for o in used_occupations}
                taken_tags_cnt = {}
                for t in used_tags:
                    k = t.strip().lower()
                    taken_tags_cnt[k] = taken_tags_cnt.get(k, 0) + 1
                if occ and occ not in taken_occs and taken_tags_cnt.get(tag, 0) < 3:
                    used_occupations.append(str(obj.get("occupation", "")))
                    used_tags.append(str(obj.get("personality_tag", "")))
                    break
                last_err = f"duplicate occ/tag: {occ}/{tag}"
        else:
            raise RuntimeError(f"pid={pid} 无法生成不重复画像: {last_err}")

        profile = {
            "pid": pid,
            "subject_id": sid,
            "demo": {"age": cs["age"], "gender": cs["gender"]},
            "clinical": {
                "conditions": cs["conditions"],
                "conditions_en": cs["conditions_en"],
                "medications_at_window_start": cs["current_meds"],
                "key_labs": cs["key_labs"],
                "allergies": allergies,
            },
            "persona": obj,
            "generated_by": args.model,
        }
        with lock:
            profiles_by_pid[pid] = profile
            snapshot = [profiles_by_pid[k] for k in sorted(profiles_by_pid)]
            tmp = {"generated_at": datetime.now().isoformat(), "model": args.model, "seed": SEED,
                   "n_profiles": len(snapshot), "profiles": snapshot}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(tmp, f, ensure_ascii=False, indent=2)
        print(f"  [{len(snapshot)}/{len(subjects)}] sid={sid} age={cs['age']} occ={obj.get('occupation')}", flush=True)

    todo = [(pid, sid) for pid, sid in enumerate(subjects) if pid not in profiles_by_pid]
    if todo:
        print(f"[Concurrent] 剩余 {len(todo)} 份，4 路并发", flush=True)
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(gen_one, pid, sid): (pid, sid) for pid, sid in todo}
            for fu in as_completed(futs):
                fu.result()

    profiles = [profiles_by_pid[k] for k in sorted(profiles_by_pid)]
    occs = [p["persona"].get("occupation", "") for p in profiles]
    tags = [p["persona"].get("personality_tag", "") for p in profiles]
    assert len(set(occs)) == len(occs), "occupation 重复！"
    out = {
        "generated_at": datetime.now().isoformat(),
        "model": args.model, "seed": SEED, "n_profiles": len(profiles),
        "diversity": {"unique_occupations": len(set(occs)),
                      "personality_tag_dist": {t: tags.count(t) for t in set(tags)}},
        "profiles": profiles,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[Done] {len(profiles)} profiles -> {out_path}，用时 {datetime.now() - t0}", flush=True)


if __name__ == "__main__":
    main()
