**语言：** [English](README.md) | 简体中文

# Android Debug Bridge（ADB）安装与准备

HealthJev 中饮食计划真机搜索、手机端工具链、一键健康分析等能力依赖本机可用的 `adb` 与已授权连接的 Android 设备。本文说明如何在常见环境下安装并验证。

---

## 1. 安装 platform-tools（含 `adb`）

### Windows

1. 从 [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) 官方页下载 Windows 压缩包，解压到固定目录（例如 `C:\Android\platform-tools`）。
2. 将该目录加入系统 **PATH**，新开终端执行：

   ```bash
   adb version
   ```

   应输出版本信息。

也可使用包管理器（若已安装 [winget](https://learn.microsoft.com/windows/package-manager/winget/)）：

```bash
winget install --id Google.PlatformTools
```

（具体包名以 `winget search platform-tools` 结果为准。）

### macOS

```bash
brew install android-platform-tools
```

### Linux（Debian/Ubuntu 示例）

```bash
sudo apt update
sudo apt install android-tools-adb
```

---

## 2. 手机端与连接

1. 手机开启 **开发者选项** → **USB 调试**（部分机型还需「USB 调试（安全设置）」等）。
2. USB 连接电脑，手机上允许本机调试授权。
3. 终端执行：

   ```bash
   adb devices
   ```

   列表中设备应为 `device` 状态，而非 `unauthorized` / 空白。

### 无线调试（可选）

同一局域网内可先 USB 执行 `adb tcpip 5555`，再 `adb connect <手机IP>:5555`，具体步骤因机型与 Android 版本略有差异，请以官方文档为准。

---

## 3. 与 HealthJev 相关的环境变量（按需）

真机美团推送、饮食计划与 `fsapp.py` 联调时，仓库内不同脚本对默认 ADB 端口约定可能不一致，**建议显式指定**当前实际可用的服务地址，例如：

```bash
export ADB_SERVER_SOCKET=tcp:127.0.0.1:15038
export HEALTHCLAW_ENABLE_PHONE_MEITUAN=1
```

（Windows PowerShell 可使用 `$env:ADB_SERVER_SOCKET="tcp:127.0.0.1:15038"`。）

---

## 4. 验证

```bash
adb devices
adb shell echo ok
```

无报错即表示本机 `adb` 与设备通信基本正常。

---

[← 返回主 README](../../README.zh-CN.md)
