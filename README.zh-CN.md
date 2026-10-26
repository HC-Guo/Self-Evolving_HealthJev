**语言：** [English](README.md) | 简体中文

<div align="center">

<h1>🩺 HealthJev：自进化个人健康管家</h1>

面向医学咨询辅助、个人健康管理与多模态健康分析的自进化 Agent 系统

<br/>

<img src="./docs/readme_media/healthclaw.png" alt="HealthJev" width="220" />

<br/>
<br/>

<img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
<img src="https://img.shields.io/badge/Frontend-Streamlit%20%7C%20Feishu%20%7C%20CLI-CA8A04?style=flat-square" alt="Frontend" />
<img src="https://img.shields.io/badge/Domain-Health%20%7C%20Wearable%20%7C%20Omics-DC2626?style=flat-square" alt="Domain" />
<a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License MIT" /></a>

</div>

<br/>

> 从“你问一句、它答一句”的对话式助手，进化为可长期在线运行的健康 Agent：
>
> 它贯穿日常监测、报告解读与慢病管理全过程，将个人健康管理与跨端协同整合进同一链路——让每一步动作可见、每项依据可查、每个结论可解释、每段经验可沉淀。
>
> 在需要时，可引入临床数据、医学影像与组学证据，辅助开展风险评估与鉴别分析，提升决策参考的系统性与可靠性。
>
> 本系统仅用于辅助决策，不构成医疗建议，不能替代专业诊疗。

---

## 📣 最新动态

- **2026 年 10 月 26 日**：HealthJev 发布：MIMIC-IV 纵向基准与六任务生物医学评测套件已开放于 [`release/`](./release)；JevGate-9B 权重托管于 Hugging Face（校验信息与下载说明见 `release/jevgate_9b/`）。
- **2026 年 3 月 22 日**：首次公开发布。

---

## 🎬 演示
### 饮食计划制定与推荐

<p align="center">
  <video
    poster="./docs/readme_media/meal_plan_recommendation_demo_cover.png"
    src="https://github.com/user-attachments/assets/8d0623d6-ffa8-4012-8f16-c8e8afc337d6"
    controls
    muted
    playsinline
    preload="metadata"
    width="800"
  >
    若未出现播放器，请<a href="https://github.com/user-attachments/assets/8d0623d6-ffa8-4012-8f16-c8e8afc337d6">点此直接打开视频</a>。
  </video>
</p>

<p align="left"><em>☝️ 想这周吃得更稳，却不想只在对话框里收到几条「少吃油腻」？<br/>
HealthJev 会像靠谱的饭搭子：你随口说说这周想怎么吃，它听懂后先铺一张整月的饮食底稿；<br/>
到点要吃晚餐时，它不甩一句建议就结束，而是真的去外卖 App 里搜一圈，把过程和结论留在日志里，再把饮食推荐推送到聊天界面——你只管拍板。</em></p>

<br/>

### 一键健康分析

<p align="center">
  <video
    poster="./docs/readme_media/one_click_health_demo_cover.png"
    src="https://github.com/user-attachments/assets/a3ae7af3-3176-475c-90a9-2dfc97c30a81"
    controls
    muted
    playsinline
    preload="metadata"
    width="800"
  >
    若未出现播放器，请<a href="https://github.com/user-attachments/assets/a3ae7af3-3176-475c-90a9-2dfc97c30a81">点此直接打开视频</a>。
  </video>
</p>

<p align="left"><em>☝️ 如果你懒得在多个 App 之间来回切换、自己对着各端的碎片猜近况，一键健康分析就像一个真正懂生活节奏的健康搭子：<br/>
你勾上要跑的分析项、圈定最近几天，还能按需添加子任务；它便按顺序从手机里把各端数据读齐，一次落成一份综合健康报告——<br/>
分项有评分和要点，再做一层交叉对照，补上「这两天先盯什么、本周再调什么」的行动提示，让原本零散的信号，顺手变成你能立刻跟进的健康管理下一步。</em></p>

<br/>

### 疾病风险评估

<p align="center">
  <video
    poster="./docs/readme_media/showcase_risk_prediction_cover.png"
    src="https://github.com/user-attachments/assets/f096dd86-905e-451d-a577-b1d4e5613ebc"
    controls
    muted
    playsinline
    preload="metadata"
    width="800"
  >
    若未出现播放器，请<a href="https://github.com/user-attachments/assets/f096dd86-905e-451d-a577-b1d4e5613ebc">点此直接打开视频</a>。
  </video>
</p>

<p align="left"><em>☝️ 很多人不止关心“现在有没有病”，更关注“未来几年我该防范什么”。<br/>
HealthJev 可串接病例记录，帮你看清未来患病风险，繁杂的病例信息变成更为直观的风险判断，<br/>
也能辅助医生进一步分析，为你的未来健康保驾护航。</em></p>

<p align="center">
  <a href="./docs/showcase/README.zh-CN.md">全部演示</a>
</p>

---

## 🧩 核心能力一览

| 方向 | 说明 |
|------|------|
| **医学咨询辅助** | 日常监测、报告解读与慢病跟踪；需要时整合临床、影像与组学等证据做风险评估与鉴别参考。<br>（不替代诊疗，重证据与就医引导。） |
| **健康分析与规划** | 汇总可穿戴与跨设备状态，做异常识别与飞书等通道告警。<br>围绕饮食与健康目标做解读，并支持月度与单餐层面的规划，可按需联动手机搜索与推送。 |
| **多层记忆与自进化** | **L0** 约束总体行为与写入规则；**L1** 疾病与工具索引，便于按需检索与路由；**L2** 环境与长期事实；**L3** 领域流程与可复用脚本；**L4** 运行期案例与情景记忆。<br>任务收尾时沉淀案例、蒸馏策略并做自我评估，使经验可复用、可迭代。 |
| **多入口与多模型** | 提供 Streamlit、飞书、CLI 等入口；可配置多套大模型，失败时自动重试或切换备用后端。<br>健康任务可跨时段、跨设备、跨角色，运行以工具调用与可审计轨迹为主。 |


---

## 🏗️ 架构

> **核心回路**：`[个人健康记忆 + 多设备数据 + 场景任务]` → Agent 按需推理与工具调用 → 多端输出（Web / 飞书 / 节点）→ 五层记忆沉淀与自进化。

---

## 🚀 快速开始

### 📦 1. 安装基础依赖

```bash
pip install requests streamlit lark-oapi 
```

除上述最小集合外，**请按你实际启用的能力补全依赖与环境**，不必一次装全：

| 能力 | 说明 |
|------|------|
| **手机 ADB（真机控制 / 饮食真机搜索等）** | 在电脑上安装并配置 `adb`，连接并授权 Android 设备。步骤与验证见 [《ADB 安装与准备》](./docs/adb/README.zh-CN.md)。 |
| **飞书 Bot（`fsapp.py`）** | 依赖中的 `lark-oapi` 已覆盖基础 SDK；另需在飞书开放平台创建应用、开启机器人与事件权限，并在 `mykey.py` 填写应用凭证。从零配置、排错到跨设备告警与真机推送，见 [《飞书 Bot 配置说明》](./docs/feishu/README.zh-CN.md)。 |
| **浏览器自动化** | 例如 Playwright 等，按所用脚本与工具模块另行安装。 |
| **生信 / 组学流水线** | 按实际调用的子模块与外部工具链准备环境，读取PLINK`.bed`基因型文件数据需安装`bed-reader`。 |

### 🔐 2. 配置模型与密钥

大模型的推理、规划与遵循指令能力会直接影响 Agent 的工具选择、记忆沉淀与任务完成质量；**同等条件下，更强的模型通常能显著提升「智能感」与稳定性**。在成本与延迟可接受时，**建议优先选用能力更强的模型**（本地部署时亦尽量选择与硬件匹配的较高规格权重）。

**步骤 1：复制模板**

```bash
cp mykey_template.py mykey.py
```

**步骤 2：在 `mykey.py` 中填写 API 凭证**

至少启用一组可用大模型后端；完整可选字段以 `mykey_template.py` 为准。最常用的是 **OpenAI 兼容** 接口，示例：

```python
oai_config = {
    'apikey': 'YOUR_API_KEY',
    'apibase': 'https://your-api-endpoint/v1',
    'model': 'gpt-5.4',
}
```

**步骤 3：按实际使用的后端选择配置键**

| 后端类型 | 配置键 | 说明 |
|----------|--------|------|
| OpenAI 兼容 | `oai_config` | 任意 OpenAI 兼容 API 或中转服务 |
| Claude（Anthropic） | `claude_config` | 官方 Anthropic API |
| Gemini | `google_api_key` | Google Generative AI |
| Xai（Grok） | `xai_config` | 通过 `xai_sdk` |
| Sider | `sider_cookie` | 浏览器 Cookie 鉴权 |
| 本地模型 | 见步骤 4 | 任意本地 OpenAI 兼容推理服务 |

各后端在运行时可配合自动重试（如 429/5xx 退避）与跨后端降级（以实际代码为准）。

**步骤 4（可选）：本地模型**

```bash
export SEDA_LLM_MODE=local
export SEDA_LOCAL_API_BASE=http://localhost:8000/v1
export SEDA_LOCAL_MODEL_NAME=your-local-model
```

> **勿将 `mykey.py` 提交到版本库**（模板文件可提交，真实密钥仅留在本地）。

### ▶️ 3. 启动入口

完成 **§1** 依赖与 **§2** 模型配置后，可按需启动对应入口。综合易用性与可视化，**推荐顺序**为：**Web UI → 飞书 Bot → CLI**。

**步骤 1（最推荐）：Web UI**

在仓库根目录执行，浏览器将打开 Streamlit 界面（默认本地端口多为 `8501`，具体以终端输出为准）：

```bash
streamlit run stapp.py
```

适合本地试用、调试界面与健康相关流程，无需飞书侧额外配置。

**步骤 2：飞书 Bot**

需在 `mykey.py` 中配置 `fs_app_id` / `fs_app_secret`，并完成开放平台侧机器人与事件配置（详见 [《飞书 Bot 配置说明》](./docs/feishu/README.zh-CN.md)）：

```bash
python fsapp.py
```

适合日常在飞书内收发消息、告警与饮食推送等能力。

**步骤 3：CLI 主 Agent**

终端交互式运行主 Agent 循环，便于脚本化或远程环境：

```bash
python seda_main.py --verbose
```

---

## ⚠️ 注意事项

- 勿将 `mykey.py` 提交到版本库。
- 飞书推送、手机 ADB、浏览器桥接需提前配置环境与权限。
- 涉及真实医疗决策时，本仓库更适合作为辅助分析，不能替代专业诊疗。

---

## 🔭 未来开发方向

- 测试与接入**更多大模型后端**，完善兼容与降级策略。
- 扩展**可穿戴设备**数据接入与适配（更多厂商 / 导出格式 / API）。

---

## 📜 License

见仓库根目录 `LICENSE`。

---

## 🙏 致谢
我们感谢以下项目：
- **[OpenClaw](https://docs.openclaw.ai/zh-CN)** — documentation and the gateway-oriented agent stack that informs multi-surface health workflows.
- **[GenericAgent](https://github.com/lsdefine/GenericAgent)** — a minimal, self-evolving PC agent framework whose layered memory and execution patterns influenced our design.
- **[OpenClaw Medical Skills](https://github.com/FreedomIntelligence/OpenClaw-Medical-Skills)** — a large open medical skills library that complements clinical and omics-oriented agent capabilities.

