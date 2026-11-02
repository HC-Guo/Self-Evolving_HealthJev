**语言：** [English](README.md) | 简体中文

# HealthJev 飞书 Bot 配置说明

> **快速入口**：主文档 [README.zh-CN.md](../../README.zh-CN.md)「快速开始 → 安装基础依赖」中有 pip 与能力对照；本文给出**可操作的完整配置路径**，含跨设备告警与真机美团链路。

**入口文件**：`fsapp.py`（健康问答、飞书收发、可选跨设备告警、可选饮食推送）。事件链路为 **WebSocket 长连接 + API 轮询回退**，飞书后台**无需**配置公网 HTTP 回调。

实现以 `fsapp.py`、`mykey_template.py`、`cross_device_node.py`、`binding_cli.py` 为准。

---

## 1. 最小聊天 Bot（建议先做）

### 1.1 依赖

仓库未单独维护仅飞书用的 `requirements.txt`。与主 README 一致至少需：`requests`、`streamlit`、`lark-oapi`（飞书收发核心为 `lark-oapi`）。若希望贴近本仓库全量能力，可参考：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install requests streamlit lark-oapi playwright beautifulsoup4 bottle PyYAML simple-websocket-server
```

### 1.2 `mykey.py`

```bash
cp mykey_template.py mykey.py
```

至少填写：

```python
oai_config = {
    "apikey": "YOUR_OPENAI_OR_PROXY_API_KEY",
    "apibase": "https://your-openai-compatible-endpoint/v1",
    "model": "your-model-name",
}

fs_app_id = "cli_xxxxx"
fs_app_secret = "xxxxxxxx"
# fs_allowed_users = ["ou_xxxxx"]  # 可选，不配置则不限用户

fs_enable_external_alert_server = False  # 先关告警监听，不占 8787
```

密钥勿提交 Git；可放在仓库外并通过 `HEALTHCLAW_MYKEY_PATH`、`HEALTHCLAW_MYKEY_DIR` 或 `HEALTHCLAW_SHARED_HOME` 加载，例如：`export HEALTHCLAW_MYKEY_PATH=/path/to/secure/mykey.py`。

### 1.3 飞书开放平台（<https://open.feishu.cn/>）

按顺序检查：

| 步骤 | 内容 |
|------|------|
| 应用类型 | **企业自建应用** |
| 能力 | 开启 **机器人** |
| 事件与回调 | 接收方式选 **长连接**；至少订阅 **`im.message.receive_v1`**（与代码主链路一致） |
| 可选事件 | `im.chat.access_event.bot_p2p_chat_entered_v1` 非硬依赖，仅为兼容历史部署 |
| 权限 | `im:message`、`im:message:send_as_bot`、`im:message.p2p_msg:readonly`、`application:application:self_manage`（轮询回退会读应用详情，`self_manage` 建议保留） |
| 发布 | 配置后必须 **发布版本** 才会生效 |

### 1.4 启动与验证

```bash
source .venv/bin/activate
python fsapp.py
```

日志出现 `WebSocket + 轮询回退`、`等待消息...` 即正常。若后台保存长连接提示「未检测到应用连接」，请先本地启动 Bot，待日志出现 `connected to wss://msg-frontier.feishu.cn/ws/v2...` 后再保存。

在飞书中先发 **`/help`**、**`/status`**（可先不依赖完整 LLM），日志中可见 `P2ImMessageReceiveV1` 等。若出现「WebSocket 已连接但尚未收到事件，启动轮询回退…」属预期容错。

**行为边界**：仅处理**文本**；**群聊**需 **@ 机器人**。

内置命令：`/help`、`/status`、`/stop`、`/new`、`/restore`。

### 1.5 饮食推送能复现到哪一层

- **基础推荐**：无真机也可基于内置候选库推送到飞书。
- **真机美团 live**（搜美团、截图、加购等）：需 ADB、OCR、环境变量等，见下文 **§3**。

---

## 2. 跨设备告警（可选）

在 **§1.2** 基础上改为启用监听，并配置节点相关字段（示例值请按环境修改）：

```python
fs_enable_external_alert_server = True
fs_external_alert_host = "0.0.0.0"
fs_external_alert_port = 8787
fs_external_alert_token = "CHANGE_ME_BEFORE_PROD"
cross_device_node_id = "child_01"
fs_external_alert_targets = {"child_01": "ou_xxxxx"}
```

- `fs_external_alert_*`：随 `fsapp.py` 启动 `/external_alert`；`token` 供其他节点调用鉴权。
- `cross_device_node_id`：当前飞书节点逻辑 ID。
- `fs_external_alert_targets`：`target_id → open_id`；若上游已在 payload 里带 `recipient_open_id`，可不依赖此映射。

健康检查：`curl http://127.0.0.1:8787/healthz`。

**绑定发送侧与飞书节点**（发送侧节点与 `binding_cli` 示例）：

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

## 3. 真机美团推送（可选）

需：`adb` 可用、设备已授权、`ADB_SERVER_SOCKET` **显式**指向实际服务（`meal_plan_service` 与 demo 脚本默认端口可能不一致，勿依赖默认值）、`HEALTHCLAW_ENABLE_PHONE_MEITUAN=1`、以及 `rapidocr_onnxruntime` 等。`fsapp.py` 可能默认打开 `HEALTHCLAW_ENABLE_PHONE_MEITUAN`，但 **`ADB_SERVER_SOCKET` 务必自行导出**。

```bash
export ADB_SERVER_SOCKET=tcp:127.0.0.1:15038
export HEALTHCLAW_ENABLE_PHONE_MEITUAN=1
# 可选：export HEALTHCLAW_SHARED_HOME=/path/to/shared/healthjev
python fsapp.py
```

手机链路未就绪时通常会**回退**到内置候选推荐（仍有文字推送，但未必含截图/live 搜索）。判断是否走真机：推送是否含搜索/店铺/购物车截图，日志是否出现 ADB/搜索/加购相关输出。ADB 安装与设备准备见 [《ADB 安装与准备》](../adb/README.zh-CN.md)。

---

## 4. 常见问题

| 现象 | 处理 |
|------|------|
| 手机打开机器人无回复 | 核对是否混用**正式企业**与**测试企业**环境 |
| 只想聊天、不开 8787 | `fs_enable_external_alert_server = False` |
| 密钥存放 | 仅用模板入库，`mykey.py` 本地或 `HEALTHCLAW_MYKEY_*` 外置 |

---

## 5. 推荐排错顺序

1. 依赖与 `mykey.py`（`oai_config` + `fs_app_id` / `fs_app_secret`）  
2. 开放平台机器人、长连接、`im.message.receive_v1`、权限、**发布**  
3. 启动 `fsapp.py` → `/help`、`/status` → 再测普通聊天  
4. 最后按需开跨设备告警、绑定、真机美团  

---

[← 返回主 README](../../README.zh-CN.md)
