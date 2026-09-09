# OneWork 开发说明

## 独立仓库

仓库包含 Android、macOS 和桥接服务源码，不需要外层竞争洞察项目。

| 目录 | 内容 |
| --- | --- |
| android/ | Android 原生应用和 Gradle Wrapper |
| macos/ | SwiftUI 伴侣应用、构建和本地安装工具 |
| bridge/ | Python HTTP/TLS 桥接、Codex 协议客户端、飞书适配 |
| hooks/ | 可选 Codex 状态观察 Hook |
| brand/ | 与 Android 图标同源的品牌资源 |
| agent_views/ | 兼容既有 Python 模块名的入口 |
| openspec/ | 项目文档 |

项目目录改为 `onework`，但 Python 模块名 `agent_views`、Android applicationId 和 `~/.agent-views` 数据目录保持兼容，避免重装与重新配对。原工作树中的 `agent_views` 路径保留为指向本仓库的本地兼容链接；这个链接不是远程仓库依赖。

`bridge/codex_protocol.py` 从原项目的协议客户端收拢，已同时收拢对应回归测试；不依赖兄弟目录或其数据库代码。

## Python

在仓库根目录执行，建议 Python 3.12。只有启用 TLS 配对需要 requirements-wifi.txt 中的依赖。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r bridge/requirements-wifi.txt
.venv/bin/python -m agent_views.bridge --help
.venv/bin/python -m agent_views.bridge --workspace "$PWD"
```

默认是回环连接；需要同局域网连接时优先使用 macOS 伴侣应用的配对流程，不把服务端口公开到公网。

```sh
python3 -m unittest discover -s bridge/tests -v
python3 -m unittest discover -s hooks/tests -v
python3 -m unittest discover -s macos/tests -v
python3 macos/sync_brand.py --check
```

可选 Hook 安装会修改用户 Codex Hook 配置，仅在确认需要时执行：

```sh
python3 -m agent_views.hooks.install --install
python3 -m agent_views.hooks.install --uninstall
```

## Android

源码 Java 17；本次迁移验证使用 JDK 21 和 Gradle Wrapper 9.1.0。先确认 JAVA_HOME 是正确 JDK，ANDROID_HOME 指向已安装 Android SDK（compileSdk 36），不要提交 local.properties。

```sh
cd android
./gradlew --version
./gradlew testDebugUnitTest assembleDebug lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

保留现有 applicationId，以覆盖升级方式保留配对。实际设备行为和多设备容错仍需实机验收。

## macOS

依赖 Xcode Command Line Tools 和一个可复制的完整 Python 3.12 运行时目录（不是虚拟环境）；把该目录作为 `--python-home` 传入。输出目录中不能已有 OneWork.app。

```sh
python3 macos/build.py --python-home /path/to/python-home --output macos/dist/local-build
python3 macos/install.py --source macos/dist/local-build/OneWork.app
```

安装器默认只做预检；显式 `--apply` 才更新固定安装位置。更新前停止 OneWork 自己的服务并退出其界面。它保留旧应用备份，不清除 `~/.agent-views`。

此构建只做 ad-hoc 签名；不代表 Developer ID 签名、公证或可对外直接分发。

## 当前限制

- Codex 支持范围由 bridge/__main__.py 的版本门禁控制。
- 列表和详情 HTTP 使用后台快照；发送前仍严格读取最新状态并验证 revision，未知操作结果不自动重放。
- 后台卡点分析仍可能读取长任务历史。整批读取过慢或失败时会显示过期状态并禁用相关操作，不保证任意规模任务的实时性。
- 本仓库不包含应用安装包、运行时凭证、个人配对信息或本机验收截图。
