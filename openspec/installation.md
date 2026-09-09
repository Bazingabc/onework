# 安装与配对

[返回 README](../README.md) · [开发指南](development.md)

当前路径是“从源码构建 → 本机安装 → 局域网配对”。无需上架应用商店，也无需部署公网服务器。首次安装建议先接通 Codex，再按需配置飞书。

## 1. 准备环境

| 环境 | 要求与检查 |
| --- | --- |
| macOS | 应用声明最低 macOS 13；主要验证环境为 Apple Silicon。Intel 构建、最低系统版本仍需独立验收 |
| Mac 构建工具 | Xcode Command Line Tools；`xcode-select -p`、`xcrun swiftc --version` 可运行 |
| Python | 建议 3.12；运行源码可用 venv，打包 Mac 应用需要完整、可搬移的运行时，不能用 venv 代替 |
| Codex CLI | 已安装、已在本机登录并可正常工作；`codex --version` 应满足 `>= 0.144.0, < 0.154.0` |
| Android | 最低 API 28（Android 9），推荐横屏平板；扫码需要摄像头权限 |
| Android 构建 | 源码 Java 17，已验证 Gradle Wrapper 9.1.0 + JDK 21；SDK Platform 36、对应 Build Tools 和 Platform Tools |
| 网络 | Mac 与平板处于可互访的同一局域网，不是启用了客户端隔离的访客网络 |
| 飞书（可选） | Mac 上可用且具有当前用户任务权限的 `lark-cli`；妙记入口还需平板安装飞书 |

安装 Android 开发环境可使用 [Android Studio](https://developer.android.com/studio)。通过 SDK Manager 安装上述 SDK 组件；先完成 SDK 许可确认。本文不会自动改写 shell 配置或升级已有 CLI。

```sh
git clone https://github.com/Bazingabc/onework.git
cd onework
xcode-select -p
xcrun swiftc --version
codex --version
```

若缺少 Apple 命令行工具，可执行 `xcode-select --install` 并完成系统安装界面。

## 2. 构建和安装 Mac 伴侣

### 准备完整 Python 运行时

一种方式是使用已安装的 [uv 管理 Python 3.12](https://docs.astral.sh/uv/guides/install-python/)。它提供的 standalone Python 可作为打包来源。以下步骤下载 Python，但不安装或更改 Codex / 飞书 CLI：

```sh
uv python install 3.12
ONEWORK_PYTHON="$(uv python find --managed-python 3.12)"
ONEWORK_PYTHON_HOME="$("$ONEWORK_PYTHON" -c 'import sys; print(sys.base_prefix)')"
"$ONEWORK_PYTHON_HOME/bin/python3" --version
```

也可以自行提供完整 Python 3.12 运行时：目录内必须有可执行的 `bin/python3` 及其标准库，并与目标 Mac 架构匹配。不要把 `.venv` 或某个 Python 可执行文件本身当作 `--python-home`。

### 构建、预检、安装

在仓库根目录、同一个终端中继续：

```sh
"$ONEWORK_PYTHON" macos/build.py \
  --python-home "$ONEWORK_PYTHON_HOME" \
  --output macos/dist/local-build

# 只预检，不改变现有安装
"$ONEWORK_PYTHON" macos/install.py --source macos/dist/local-build/OneWork.app

# 确认预检无误后，显式安装
"$ONEWORK_PYTHON" macos/install.py --source macos/dist/local-build/OneWork.app --apply

open "$HOME/Applications/OneWork.app"
```

默认安装到用户的 `~/Applications/OneWork.app`。构建目录中不能已经存在 `OneWork.app`；再次构建请选择新的 `--output`，例如 `macos/dist/local-build-2`。

应用目前仅做本地 ad-hoc 签名，不是已公证发行包。若系统阻止运行，请先核实构建来源与系统提示；不要关闭 Gatekeeper 或全局安全设置。

打开后进入「数据源」，选择本机 Codex CLI 和默认工作目录，点击「检测 CLI 版本」，再到「概览」启动服务。如果找不到 CLI，可先用 `command -v codex` 确认其路径。暂不使用飞书时，可以保留飞书未配置状态。

## 3. 构建并安装 Android 应用

在终端为**本次构建**设置真实路径，不要原样使用占位符：

```sh
export JAVA_HOME="/absolute/path/to/jdk-21/Contents/Home"
export ANDROID_HOME="/absolute/path/to/Android/sdk"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
```

Android Studio 的 SDK 常见位置为 `~/Library/Android/sdk`，其他安装方式的目录可能不同；以 SDK Manager 显示的路径为准。

```sh
cd android
./gradlew --version
./gradlew testDebugUnitTest assembleDebug lintDebug
adb devices -l
```

确认 `./gradlew --version` 中 **Launcher JVM 和 Daemon JVM** 使用预期 JDK。平板开启开发者选项和 USB 调试，通过支持数据传输的 USB 线连接，解锁屏幕并确认调试授权。设备状态应为 `device`，而非 `unauthorized` 或 `offline`。参见 [ADB 官方指南](https://developer.android.com/tools/adb)。

```sh
# 将 YOUR_DEVICE_SERIAL 替换为 adb devices -l 显示的目标设备序列号
adb -s YOUR_DEVICE_SERIAL install -r app/build/outputs/apk/debug/app-debug.apk
cd ..
```

也可将自己构建的 APK 传到平板，通过系统文件管理器安装；只为可信安装来源授予必要权限。

**覆盖升级注意：** 当前正式包名仍为 `com.oneripple.agentviews`。不同开发者的 debug 签名可能不同，因此“他人提供的 APK”不一定能被“自己构建的 APK”覆盖。遇到签名不一致时，不要直接卸载正式应用：卸载会丢失其本地配对和草稿。优先使用同一签名来源继续升级，或在开发分支中使用独立 applicationId。参见 [Android 应用签名说明](https://developer.android.com/studio/publish/app-signing)。

## 4. 扫码建立日常连接

1. 将 Mac 与平板连接到可互访的同一局域网。
2. Mac OneWork「概览」启动服务，确认网络和 Codex 状态正常。
3. 点击「添加平板」，或在「设备」页生成配对二维码。
4. 平板打开 OneWork 的连接设置，选择「扫描 Mac 配对码」，允许摄像头访问并扫码。
5. **回到 Mac 确认设备申请**：核对设备信息，选择「允许查看」或「允许操作」。需要发消息或完成待办时选择操作权限。
6. 等待平板同步，左右滑动查看首页和 Agent 页面。

扫码仅发起申请，**不等于配对已经完成**。二维码过期需重新生成；不要把配对码截图发到公开 Issue。

配对完成后可拔掉 USB。这里使用的是 OneWork 的 TLS 局域网连接，不是 Android 无线调试配对；日常使用无需 `adb tcpip`、无线 ADB 或持续开启 USB 调试。

当前同时在线上限为 3 台。Mac「设备」页可以查看类别、型号和权限，以及撤销不再使用的设备；设备上报信息只是辨认线索，不能代替对申请来源的核实。

## 5. 可选：启用飞书

OneWork 复用用户自己的飞书 CLI，不代管统一的飞书应用凭证。由于不同 CLI 分发、企业应用和权限策略可能不同，这里不提供未经验证的“一条安装命令”。

1. 按你使用的 CLI 分发说明完成安装，确认 `lark-cli --version` 可运行。
2. 在 Mac OneWork「数据源」中选择该可执行文件，点击「核实飞书授权」。
3. 若授权缺失，点击「修复飞书待办授权」，在飞书页面核对权限并完成授权，再点击界面上的完成确认按钮。
4. 回到平板手动刷新待办。授权不足可能需要企业管理员介入，重复扫码不能绕过管理员限制。

适配器以当前用户身份调用任务接口；授权流程依赖 `auth status`、`auth login --domain task` 等 CLI 能力，具体调用见 [desktop.py](../bridge/desktop.py) 与 [work.py](../bridge/work.py)。仅有同名可执行文件不代表协议兼容。

妙记入口调用平板飞书的 `feishu://applink.feishu.cn/minutes/home`，目标包名为 `com.ss.android.lark`。它只打开入口，不开始录音；若未安装、未登录或客户端不支持该链接，请在飞书应用内打开妙记。

## 6. 更新与回退

- 同步源码后重新构建，优先同时更新 Mac 与 Android，避免协议错配。
- 更新 Mac 前，先在 OneWork 停止自己的服务，再退出菜单栏应用；无需停止其他 Codex 进程。
- 先运行安装器预检，再添加 `--apply`。旧应用备份位于安装目录下的 `.onework-backups`，用户数据 `~/.agent-views` 保留。
- 回退使用 `python3 macos/install.py --rollback EXACT_BACKUP_DIRECTORY_NAME` 先预检，确认后再添加 `--apply`。备份名称应从实际目录获取，不使用猜测值。
- 回退只回退应用，不还原业务操作或外部 Codex / 飞书数据。结果待确认的操作必须先去原应用核实，不能靠回退后重发来“修复”。

## 常见问题

| 现象 | 优先检查 |
| --- | --- |
| `adb devices -l` 为空或未授权 | USB 线能否传数据、平板是否解锁、USB 调试和授权弹窗；ADB 仅用于安装调试 |
| 扫码后“连接未完成” | Mac 是否有待确认设备、二维码是否过期、两端能否互访；不要只在平板重复扫码 |
| Mac 没有可用局域网地址 | 是否连接 Wi-Fi / 有线局域网，是否被 VPN、访客网络或防火墙隔离 |
| 换网后不恢复 | 保持菜单栏应用运行，等待网络恢复；必要时重开连接页。未先核实前不要删除配对资料 |
| CLI 版本不支持 | 核对「数据源」实际路径和版本门禁；不要仅为了试用而盲目降级常用环境 |
| 飞书不可用但 Agent 正常 | 飞书是独立可选源；检查 CLI 路径、当前用户授权和任务权限 |
| 平板数据过期、按钮禁用 | 等待权威快照恢复，查看 Mac 诊断；长历史读取或协议故障可能阻塞后台同步 |
| 按钮提示结果待确认 | 去 Codex / 飞书核实实际结果，再使用 Mac 诊断页的确认入口；不要反复发送 |
| 语音入口无法使用 | 检查平板是否安装并启用了兼容的系统语音识别服务；OneWork 不内置 ASR 引擎 |
| APK 无法覆盖安装 | 检查签名、包名及版本；不要默认卸载，避免丢失配对和草稿 |

仍有问题时，请按[反馈模板](contributing.md#反馈问题)提交经过脱敏的信息。以上是基于当前实现和已有环境整理的步骤，尚未完成所有芯片架构、最低系统版本和全新 Mac 的安装验证。
