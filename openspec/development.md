# OneWork 开发指南

[返回 README](../README.md) · [安装与配对](installation.md) · [参与共建](contributing.md)

## 独立仓库

仓库包含 Android、macOS 和桥接服务源码，不依赖外层工作树或兄弟项目。

| 目录 | 内容 |
| --- | --- |
| android/ | Android 原生应用和 Gradle Wrapper |
| macos/ | SwiftUI 伴侣应用、构建和本地安装工具 |
| bridge/ | Python HTTP/TLS 桥接、Codex 协议客户端、飞书适配 |
| hooks/ | 可选 Codex 状态观察 Hook |
| brand/ | 与 Android 图标同源的品牌资源 |
| agent_views/ | 兼容既有 Python 模块名的入口 |
| openspec/ | 项目文档 |

项目目录为 `onework`，Android namespace、源码包名和 applicationId 已统一为 `com.one.onework`。旧版 `com.oneripple.agentviews` 与新版可并存，但新版需要重新配对，配对信息与草稿不会自动迁移；确认新版可用前不要卸载旧版。Python 模块名 `agent_views`、Mac Bundle ID 和 `~/.agent-views` 数据目录保持不变。

`bridge/codex_protocol.py` 从原项目的协议客户端收拢，已同时收拢对应回归测试；不依赖兄弟目录或其数据库代码。

## 选择开发入口

| 想改什么 | 从哪里开始 | 最小验证 |
| --- | --- | --- |
| 任务列表、卡点和输入交互 | Android `MainActivity`、`SessionRailAdapter`、`AutoFocusPolicy` | Android 单元测试、构建、lint，再做平板交互验收 |
| 首页待办和额度 | Android `HomePane` + `bridge/work.py` | 适配器测试、模拟数据展示，必要时授权实测 |
| 状态识别和任务控制 | `bridge/service.py`、`attention.py`、`codex_protocol.py` | Bridge 测试；检查旧状态和原客户端占用场景 |
| Mac 窗口和设备管理 | `macos/OneWork.swift` + `bridge/desktop.py` | Mac 构建、安装器测试、窗口与设备权限实测 |
| 配对和网络恢复 | `bridge/devices.py`、`network.py`、`lan.py` + Android 连接类 | 配对/网络测试，再做断网、换网和撤权实测 |
| README 截图 | `openspec/screenshots/` | 运行独立模拟截图脚本并逐图检查 |

## Python

在仓库根目录执行，建议 Python 3.12。`requirements-wifi.txt` 当前固定 `qrcode[pil]==8.2`，用于 Python 二维码图片生成工具；HTTP/TLS 本身主要使用标准库，Mac 界面通过原生 Core Image 生成配对码。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r bridge/requirements-wifi.txt
.venv/bin/python -m agent_views.bridge --help
```

### USB 调试运行

若已有 Mac OneWork 服务运行，先在其界面停止服务，避免默认端口冲突，再启动源码服务：

```sh
.venv/bin/python -m agent_views.bridge --workspace "$PWD"
```

另开终端，选择已授权的验收设备，并在平板连接设置中切到 USB：

```sh
adb -s YOUR_DEVICE_SERIAL reverse tcp:8765 tcp:8765
```

默认只监听 `127.0.0.1:8765`。同局域网连接优先使用 macOS 伴侣应用的配对流程，不把服务端口公开到公网，也不要为了连接平板直接暴露未加密 HTTP。

结束时在服务终端按 `Ctrl+C`，移除本次端口转发：

```sh
adb -s YOUR_DEVICE_SERIAL reverse --remove tcp:8765
```

**该模式会接入真实本机 Codex，不是 mock。** 需要公开演示或截图时，请使用[独立模拟截图工具](screenshots/README.md)。

### 自动化测试

以下测试可独立运行，不要求连接平板或先启动真实 Bridge：

```sh
.venv/bin/python -m unittest discover -s bridge/tests -v
.venv/bin/python -m unittest discover -s hooks/tests -v
.venv/bin/python -m unittest discover -s macos/tests -v
.venv/bin/python macos/sync_brand.py --check
```

可选 Hook 安装会修改用户 Codex Hook 配置，仅在确认需要时执行：

```sh
.venv/bin/python -m agent_views.hooks.install --install
.venv/bin/python -m agent_views.hooks.install --uninstall
```

Hook 补充原客户端生命周期事件，不代表获得任意任务的操作权限。安装器保留其他 Hook；仍应核对安装输出和备份，不要在用户不知情时启用。

## Android

源码 Java 17；本次迁移验证使用 JDK 21 和 Gradle Wrapper 9.1.0。先确认 JAVA_HOME 是正确 JDK，ANDROID_HOME 指向已安装 Android SDK（compileSdk 36），不要提交 local.properties。

```sh
cd android
./gradlew --version
./gradlew testDebugUnitTest assembleDebug lintDebug
adb -s YOUR_DEVICE_SERIAL install -r app/build/outputs/apk/debug/app-debug.apk
```

先核对 `./gradlew --version` 的 Launcher JVM 与 Daemon JVM。SDK 缺失、JDK 不匹配和依赖下载失败属于环境问题，不应通过改源码规避。

- 单元测试：`app/src/test/java/`，优先覆盖状态转换、自动聚焦、草稿和快照新鲜度。
- 报告：`app/build/reports/tests/testDebugUnitTest/` 和 `app/build/reports/lint-results-debug.html`。
- 实机测试：`app/src/androidTest/java/`。现有 `WifiSmokeInstrumentation` 读取设备真实配对并执行只读协议/TLS 检查，不发送业务消息；仅在专门的验收设备上运行。
- 保留现有 applicationId 以兼容升级，但覆盖安装仍需相同签名。正式开发包目前没有独立 applicationId 后缀；文档截图脚本的 `.docs` 隔离仅作用于临时副本。不要默认卸载正式包，以免丢失配对和草稿。

## macOS

依赖 Xcode Command Line Tools 和一个可复制的完整 Python 3.12 运行时目录（不是虚拟环境）；把该目录作为 `--python-home` 传入。具体准备方法见[安装指南](installation.md#2-构建和安装-mac-伴侣)。输出目录中不能已有 OneWork.app。

```sh
python3 macos/build.py --python-home /path/to/python-home --output macos/dist/local-build
python3 macos/install.py --source macos/dist/local-build/OneWork.app
```

安装器默认只做预检；显式 `--apply` 才更新固定安装位置。更新前停止 OneWork 自己的服务并退出其界面。它保留旧应用备份，不清除 `~/.agent-views`。

此构建只做 ad-hoc 签名；不代表 Developer ID 签名、公证或可对外直接分发。

SwiftUI 入口是 `macos/OneWork.swift`，构建脚本直接调用 `swiftc`。打包会排除 Python `site-packages`；新增第三方运行依赖时必须同时处理打包和干净环境验证，不能只在开发环境安装后认为交付完成。

品牌以 Android `ic_onework_mark.xml` 为图形来源，经过 `sync_brand.py` 和 `render_icons.swift` 同步/渲染。修改时同时核对 Android 桌面、应用内、Mac 菜单栏与窗口，避免各端自行重画。

## 关键开发约束

1. **状态与操作分开。** 列表和详情展示后台快照；写操作前仍需验证最新状态和 revision，旧问题不能直接授权提交。
2. **识别与权限分开。** 推断“可能需要回复”不代表获得原客户端审批通道或任务控制权。
3. **未知结果不盲目重发。** 不绕过幂等标识、操作记录和人工核实流程。
4. **权限在服务端检查。** 查看设备不能构造请求获得操作权限；撤销、过期和证书不匹配必须拒绝访问。
5. **依赖可独立失败。** 飞书不可用不应阻止 Codex 总览，旧快照不可包装成新数据。
6. **兼容范围需要证据。** 修改 CLI 版本门禁、协议字段或恢复行为时，更新测试和文档，并验证两端版本错配。

## 实机验证清单

根据改动范围检查：首次配对、扫码待确认、撤销权限、只读设备、断网重连、Mac 休眠、CLI 不兼容、飞书授权失效、快照过期、重复点击和操作结果待确认。

自动化测试不能替代多机型、多设备和真实授权链路验收。提交时区分单元测试、实机结果和未验证项；模拟截图不是业务联通证据。

## 当前限制

- Codex 支持范围由 bridge/__main__.py 的版本门禁控制。
- 列表和详情 HTTP 使用后台快照；发送前仍严格读取最新状态并验证 revision，未知操作结果不自动重放。
- 后台卡点分析仍可能读取长任务历史。整批读取过慢或失败时会显示过期状态并禁用相关操作，不保证任意规模任务的实时性。
- 本仓库不包含应用安装包、运行时凭证、个人配对信息或本机验收截图。
