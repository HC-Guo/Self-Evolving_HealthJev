# -*- coding: utf-8 -*-
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

API_BASE = (os.environ.get("HEALTHJEV_BASE_URL") or os.environ.get("FRIDAY_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
APP_ID = (os.environ.get("HEALTHJEV_API_KEY") or os.environ.get("FRIDAY_APP_ID")
          or os.environ.get("OPENAI_API_KEY") or "")

DOUBAO_MODEL = os.environ.get("HEALTHJEV_MODEL", "Doubao-Seed-2.0-pro")
GLM_MODEL = os.environ.get("HEALTHJEV_JUDGE_MODEL", "glm-5.2")
EMBED_MODEL = os.environ.get("HEALTHJEV_EMBED_MODEL", "doubao-embedding")
E2_JUDGE_MODEL = os.environ.get("E2_JUDGE_MODEL", "gpt-5.6-luna")

THINK_DISABLED = {"thinking": {"type": "disabled"}}
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = float(os.environ.get("READ_TIMEOUT", "240"))


def _think_extra(model: str) -> Dict[str, Any]:
    m = (model or "").lower()
    return dict(THINK_DISABLED) if ("doubao" in m or "glm" in m or "deepseek" in m) else {}


class SharedRateLimiter:
    def __init__(self, name: str, rpm: int = 500):
        self.rpm = max(1, rpm)
        state_dir = Path(os.environ.get(
            "HEALTHCLAW_RATE_DIR",
            "/tmp/healthjev_rate"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = state_dir / f"{name}_timestamps.json"
        if not self.state_file.exists():
            self.state_file.write_text("[]", encoding="utf-8")
        self._local = threading.Lock()

    def acquire(self) -> None:
        while True:
            wait = self._try_acquire_once()
            if wait is None:
                return
            time.sleep(min(wait + 0.05, 5.0))

    def _try_acquire_once(self) -> Optional[float]:
        with self._local:
            with open(self.state_file, "a+", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    raw = f.read().strip() or "[]"
                    try:
                        stamps = json.loads(raw)
                    except Exception:
                        stamps = []
                    now = time.time()
                    window = [t for t in stamps if now - t < 60.0]
                    if len(window) >= self.rpm:
                        return 60.0 - (now - window[0])
                    window.append(now)
                    f.seek(0)
                    f.truncate()
                    f.write(json.dumps(window))
                    f.flush()
                    os.fsync(f.fileno())
                    return None
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)


_THINK_PAT = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)

_MM_TOKEN_RE = re.compile(r"\[\[IMG:([^\]]+)\]\]")
_MM_B64_CACHE: Dict[str, str] = {}


def _img_to_b64(path: str, max_side: int = 1024) -> str:
    import base64
    import io
    from PIL import Image
    mtime = os.path.getmtime(path) if os.path.exists(path) else 0
    key = f"{path}|{mtime}|{max_side}"
    if key in _MM_B64_CACHE:
        return _MM_B64_CACHE[key]
    if not os.path.exists(path):
        raise FileNotFoundError(f"mm image not found: {path}")
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    _MM_B64_CACHE[key] = b64
    return b64


def _convert_mm_content(content: Any) -> Any:
    if not (isinstance(content, str) and "[[IMG:" in content):
        return content
    paths = list(dict.fromkeys(_MM_TOKEN_RE.findall(content)))
    text = _MM_TOKEN_RE.sub("", content)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    for p in paths:
        try:
            b64 = _img_to_b64(p)
        except Exception:
            continue
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return parts


def _convert_mm_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("content"), str) and "[[IMG:" in m["content"]:
            m = dict(m)
            m["content"] = _convert_mm_content(m["content"])
        out.append(m)
    return out


def strip_think_tags(text: str) -> str:
    if not text:
        return ""
    return _THINK_PAT.sub("", text).strip()


class LLMClient:
    def __init__(self, channel: str = "doubao", model: str = DOUBAO_MODEL,
                 rpm: int = 500, api_key: str = APP_ID, base_url: str = API_BASE,
                 multimodal: bool = False,
                 embed_base_url: Optional[str] = None,
                 embed_api_key: Optional[str] = None,
                 extra_payload: Optional[Dict[str, Any]] = None):
        self.channel = channel
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.embed_base_url = (os.environ.get("HEALTHJEV_EMBED_BASE_URL")
                               or embed_base_url or self.base_url).rstrip("/")
        configured_embed_key = os.environ.get("HEALTHJEV_EMBED_API_KEY")
        self.embed_api_key = (configured_embed_key if configured_embed_key is not None
                              else embed_api_key if embed_api_key is not None else api_key)
        self.extra_payload: Dict[str, Any] = dict(extra_payload or {})
        self.multimodal = multimodal
        self.last_usage: Optional[Dict[str, Any]] = None
        self.limiter = SharedRateLimiter(channel, rpm=rpm)
        self._session = requests.Session()
        self._session.trust_env = os.environ.get("HEALTHJEV_TRUST_ENV", "0") == "1"
        self._embed_cache: Dict[str, List[float]] = {}
        self.no_temp_models = {"gpt-5.6-luna"}
        self.max_tokens_scale = {"qwen3.8-27b": 4, "qwen3.5-plus": 4}

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_err: Optional[Exception] = None
        for attempt in range(3):
            self.limiter.acquire()
            try:
                r = self._session.post(url, headers=headers, json=payload,
                                       timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
                if r.status_code == 429:
                    print(f"[llm:{self.channel}] HTTP 429 body={str(r.text)[:300]}",
                          file=sys.stderr, flush=True)
                    time.sleep(3 + attempt * 3)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(
                        f"HTTP {r.status_code} body={r.text[:4000]}".replace("\n", " | "))
                r.raise_for_status()
                data = r.json()
                self.last_usage = data.get("usage")
                return data
            except Exception as exc:
                last_err = exc
                time.sleep(2 + attempt * 3)
        raise RuntimeError(f"LLMClient[{self.channel}] request failed: {last_err}")

    def _build_payload(self, messages, temperature, max_tokens, tools=None, json_mode=False,
                       think_extra: Optional[Dict[str, Any]] = None):
        if self.multimodal:
            messages = _convert_mm_messages(messages)
        payload: Dict[str, Any] = {
            "model": "",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if think_extra:
            payload.update(think_extra)
        if tools:
            payload["tools"] = tools
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _extract(self, data: Dict[str, Any]) -> Dict[str, Any]:
        msg = data["choices"][0]["message"]
        content = strip_think_tags(msg.get("content") or "")
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict) and "function" in tc:
                fn = tc["function"]
                args = fn.get("arguments", "{}")
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                item = {"id": tc.get("id", f"call_{len(tool_calls)}"),
                        "name": fn.get("name", ""), "arguments": args}
                for k, v in tc.items():
                    if k not in ("id", "type", "function") and k not in item:
                        item[k] = v
                tool_calls.append(item)
        _std_msg_keys = {"role", "content", "tool_calls", "reasoning_content"}
        msg_extra = {k: v for k, v in msg.items() if k not in _std_msg_keys}
        return {"content": content, "tool_calls": tool_calls, "raw": msg,
                "msg_extra": msg_extra}

    def chat(self, messages: List[Dict[str, Any]], tools=None, temperature: float = 0.0,
             max_tokens: int = 2000) -> Dict[str, Any]:
        max_tokens = int(max_tokens * self.max_tokens_scale.get(self.model, 1))
        payload = self._build_payload(messages, temperature, max_tokens, tools,
                                      think_extra=_think_extra(self.model))
        payload["model"] = self.model
        if self.extra_payload:
            payload.update(self.extra_payload)
        if self.model in self.no_temp_models:
            payload.pop("temperature", None)
        data = self._post(payload)
        return self._extract(data)

    def ask_chat(self, messages: List[Dict[str, Any]], max_tokens: int = 800,
                 temperature: float = 0.7, json_mode: bool = False) -> str:
        max_tokens = int(max_tokens * self.max_tokens_scale.get(self.model, 1))
        payload = self._build_payload(messages, temperature, max_tokens, json_mode=json_mode,
                                      think_extra=_think_extra(self.model))
        payload["model"] = self.model
        if self.extra_payload:
            payload.update(self.extra_payload)
        if self.model in self.no_temp_models:
            payload.pop("temperature", None)
        try:
            data = self._post(payload)
            return self._extract(data)["content"] or ""
        except Exception:
            return ""

    def ask_block(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.0) -> str:
        return self.ask_chat([{"role": "user", "content": prompt}],
                             max_tokens=max_tokens, temperature=temperature)

    def embed(self, text: str) -> List[float]:
        key = text[:500]
        if key in self._embed_cache:
            return self._embed_cache[key]
        last_err: Optional[Exception] = None
        for attempt in range(3):
            self.limiter.acquire()
            try:
                r = self._session.post(
                    self.embed_base_url + "/embeddings",
                    headers={"Authorization": f"Bearer {self.embed_api_key}",
                             "Content-Type": "application/json"},
                    json={"model": EMBED_MODEL, "input": text[:2000]},
                    timeout=(CONNECT_TIMEOUT, 120),
                    proxies={"http": None, "https": None})
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(3 + attempt * 4)
                    last_err = RuntimeError(f"embeddings HTTP {r.status_code}")
                    continue
                r.raise_for_status()
                vec = r.json()["data"][0]["embedding"]
                self._embed_cache[key] = vec
                return vec
            except Exception as exc:
                last_err = exc
                time.sleep(2 + attempt * 3)
        raise RuntimeError(f"LLMClient[{self.channel}] embed failed: {last_err}")


def make_doubao_client(rpm: int = 500, app_id: str = None, channel: str = None,
                       model: str = None, multimodal: bool = True,
                       base_url: str = None) -> LLMClient:
    app_id = app_id or APP_ID
    channel = channel or "doubao"
    m = (model or DOUBAO_MODEL).lower()
    extra: Dict[str, Any] = {}
    if base_url:
        embed_url, embed_key = API_BASE, (app_id or APP_ID)
        if "qwen" in m:
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
    else:
        embed_url, embed_key = None, None
    return LLMClient(channel=channel, model=model or DOUBAO_MODEL, rpm=rpm, api_key=app_id,
                     multimodal=multimodal, base_url=base_url or API_BASE,
                     embed_base_url=embed_url, embed_api_key=embed_key,
                     extra_payload=extra)


def make_glm_client(rpm: int = 500, app_id: str = None, channel: str = None) -> LLMClient:
    channel = channel or "glm"
    if app_id:
        return LLMClient(channel=channel, model=GLM_MODEL, rpm=rpm, api_key=app_id)
    return LLMClient(channel=channel, model=GLM_MODEL, rpm=rpm)


def make_e2_judge_client(rpm: int = 500) -> LLMClient:
    app_id = os.environ.get("E2_JUDGE_APP_ID") or APP_ID
    return LLMClient(channel="e2_judge", model=E2_JUDGE_MODEL, rpm=rpm, api_key=app_id)


def cosine(a: List[float], b: List[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1e-9
    db = math.sqrt(sum(y * y for y in b)) or 1e-9
    return num / (da * db)
