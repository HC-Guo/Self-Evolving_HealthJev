**Languages:** English | [简体中文](README.zh-CN.md)

# HealthJev Feishu (Lark) Bot Setup

> **Quick link:** The main repo [README.md](../../README.md) (“Quick start → Install base dependencies”) lists pip packages and capability matrix; this document is the **end-to-end operational path**, including cross-device alerts and on-device Meituan flows.

**Entry point:** `fsapp.py` (health Q&A, Feishu messaging, optional cross-device alerts, optional meal push). Events use **WebSocket long connection + API polling fallback**; you **do not** need a public HTTP callback URL in the Feishu admin.

Implementation details follow `fsapp.py`, `mykey_template.py`, `cross_device_node.py`, and `binding_cli.py`.

---

## 1. Minimal chat bot (recommended first)

### 1.1 Dependencies

There is no Feishu-only `requirements.txt`. Match the main README at minimum: `requests`, `streamlit`, `lark-oapi` (core SDK for Feishu). For closer parity with the full repo:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install requests streamlit lark-oapi playwright beautifulsoup4 bottle PyYAML simple-websocket-server
```

### 1.2 `mykey.py`

```bash
cp mykey_template.py mykey.py
```

Fill at least:

```python
oai_config = {
    "apikey": "YOUR_OPENAI_OR_PROXY_API_KEY",
    "apibase": "https://your-openai-compatible-endpoint/v1",
    "model": "your-model-name",
}

fs_app_id = "cli_xxxxx"
fs_app_secret = "xxxxxxxx"
# fs_allowed_users = ["ou_xxxxx"]  # optional; omit to allow all users

fs_enable_external_alert_server = False  # keep alert listener off; no port 8787
```

Do not commit secrets; you can keep `mykey.py` outside the repo and load via `HEALTHCLAW_MYKEY_PATH`, `HEALTHCLAW_MYKEY_DIR`, or `HEALTHCLAW_SHARED_HOME`, e.g. `export HEALTHCLAW_MYKEY_PATH=/path/to/secure/mykey.py`.

### 1.3 Feishu open platform (<https://open.feishu.cn/>)

Check in order:

| Step | Content |
|------|---------|
| App type | **Custom enterprise app** |
| Capability | Enable **Bot** |
| Events | Receive mode: **Long connection**; subscribe at least **`im.message.receive_v1`** (matches the main code path) |
| Optional events | `im.chat.access_event.bot_p2p_chat_entered_v1` is not strictly required (legacy compatibility) |
| Scopes | `im:message`, `im:message:send_as_bot`, `im:message.p2p_msg:readonly`, `application:application:self_manage` (polling fallback reads app info; keep `self_manage`) |
| Publish | You must **publish a version** for changes to take effect |

### 1.4 Run and verify

```bash
source .venv/bin/activate
python fsapp.py
```

Logs mentioning WebSocket + polling fallback and “waiting for messages…” are good. If the console shows “no app connection” until you save, start the bot locally first; wait for `connected to wss://msg-frontier.feishu.cn/ws/v2...` before saving in the admin.

In Feishu, send **`/help`** and **`/status`** first (LLM can be partial). You should see `P2ImMessageReceiveV1` etc. in logs. “WebSocket connected but no events yet, starting polling fallback…” is expected resilience.

**Behavior:** **Text only**; in **group chats** you must **@ the bot**.

Built-in commands: `/help`, `/status`, `/stop`, `/new`, `/restore`.

### 1.5 How far meal push goes without a phone

- **Basic recommendations:** Feishu push works from built-in candidates without a device.
- **On-device Meituan “live”** (search, screenshots, cart, etc.): needs ADB, OCR, env vars, etc.; see **§3** below.

---

## 2. Cross-device alerts (optional)

On top of **§1.2**, enable the listener and set node fields (adjust values for your environment):

```python
fs_enable_external_alert_server = True
fs_external_alert_host = "0.0.0.0"
fs_external_alert_port = 8787
fs_external_alert_token = "CHANGE_ME_BEFORE_PROD"
cross_device_node_id = "child_01"
fs_external_alert_targets = {"child_01": "ou_xxxxx"}
```

- `fs_external_alert_*`: `fsapp.py` exposes `/external_alert`; `token` authenticates other nodes.
- `cross_device_node_id`: logical id for this Feishu node.
- `fs_external_alert_targets`: maps `target_id → open_id`; if upstream already sends `recipient_open_id`, mapping may be unnecessary.

Health check: `curl http://127.0.0.1:8787/healthz`.

**Bind sender node to Feishu node** (sender + `binding_cli` example):

```bash
python cross_device_node.py --node-id elder_01 --host 0.0.0.0 --port 8790 \
  --binding-file memory/device_bindings.json

python binding_cli.py --server-url http://127.0.0.1:8790 upsert \
  --sender-id elder_01 --target-id child_01 \
  --endpoint http://127.0.0.1:8787/external_alert \
  --token CHANGE_ME_BEFORE_PROD \
  --recipient-open-id ou_xxxxx --target-name "Young OpenClaw"

python binding_cli.py --server-url http://127.0.0.1:8790 list
```

---

## 3. On-device Meituan push (optional)

Requires: working `adb`, authorized device, **`ADB_SERVER_SOCKET` explicitly** set to your service (`meal_plan_service` and demo scripts may disagree on default ports—do not rely on defaults), `HEALTHCLAW_ENABLE_PHONE_MEITUAN=1`, and e.g. `rapidocr_onnxruntime`. `fsapp.py` may default `HEALTHCLAW_ENABLE_PHONE_MEITUAN`, but **you must export `ADB_SERVER_SOCKET` yourself**.

```bash
export ADB_SERVER_SOCKET=tcp:127.0.0.1:15038
export HEALTHCLAW_ENABLE_PHONE_MEITUAN=1
# optional: export HEALTHCLAW_SHARED_HOME=/path/to/shared/healthjev
python fsapp.py
```

If the phone path is not ready, the stack usually **falls back** to built-in candidates (text push may still work without screenshots/live search). To confirm on-device path: check whether pushes include search/shop/cart screenshots and logs mention ADB/search/cart flows. For ADB install and device prep, see [ADB setup](../adb/README.md).

---

## 4. FAQ

| Symptom | What to do |
|---------|------------|
| Bot does not reply on phone | Verify you are not mixing **production** vs **test** tenant environments |
| Chat only, no port 8787 | `fs_enable_external_alert_server = False` |
| Secret storage | Only commit templates; keep real `mykey.py` local or via `HEALTHCLAW_MYKEY_*` |

---

## 5. Suggested troubleshooting order

1. Dependencies and `mykey.py` (`oai_config` + `fs_app_id` / `fs_app_secret`)
2. Open platform: bot, long connection, `im.message.receive_v1`, scopes, **publish**
3. Run `fsapp.py` → `/help`, `/status` → then normal chat
4. Finally enable cross-device alerts, bindings, on-device Meituan as needed

---

[← Back to main README](../../README.md)
