**Languages:** English | [简体中文](README.zh-CN.md)

# Android Debug Bridge (ADB): Installation and Setup

Meal-planning on a real device, mobile toolchains, one-click health analysis, and related HealthJev features require a working `adb` on your machine and a connected, authorized Android device. This guide covers installation and verification on common platforms.

---

## 1. Install platform-tools (includes `adb`)

### Windows

1. Download the Windows zip from the official [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) page and extract it to a fixed path (e.g. `C:\Android\platform-tools`).
2. Add that directory to your system **PATH**, open a new terminal, and run:

   ```bash
   adb version
   ```

   You should see the version printed.

Alternatively, if you use [winget](https://learn.microsoft.com/windows/package-manager/winget/):

```bash
winget install --id Google.PlatformTools
```

(Exact package id may vary; check `winget search platform-tools`.)

### macOS

```bash
brew install android-platform-tools
```

### Linux (Debian/Ubuntu example)

```bash
sudo apt update
sudo apt install android-tools-adb
```

---

## 2. Device and connection

1. On the phone, enable **Developer options** → **USB debugging** (some devices also need “USB debugging (security settings)” or similar).
2. Connect via USB and accept the debugging authorization prompt on the phone.
3. In a terminal:

   ```bash
   adb devices
   ```

   The device should appear as `device`, not `unauthorized` or blank.

### Wireless debugging (optional)

On the same LAN you can use USB once with `adb tcpip 5555`, then `adb connect <phone-ip>:5555`. Steps vary by OEM and Android version; follow official docs.

---

## 3. Environment variables relevant to HealthJev (as needed)

For Meituan push on a real device, meal plans, and `fsapp.py` integration, scripts may assume different default ADB ports. **Prefer setting** the address your setup actually uses, for example:

```bash
export ADB_SERVER_SOCKET=tcp:127.0.0.1:15038
export HEALTHCLAW_ENABLE_PHONE_MEITUAN=1
```

(On Windows PowerShell: `$env:ADB_SERVER_SOCKET="tcp:127.0.0.1:15038"`.)

---

## 4. Verification

```bash
adb devices
adb shell echo ok
```

If these run without errors, `adb` and the device can communicate.

---

[← Back to main README](../../README.md)
