# Agent Views（Android 本地版）

> 历史说明：本文保留迁移前的命令和第一版边界，不作为当前构建指南。现行入口见 [OneWork 开发说明](development.md)。

Agent Views 通过 USB 在 Android 平板上监督最近一小时的全部本机 Codex 会话，并在状态安全时直接推进原会话。会话可以来自 Codex Desktop、IDE、CLI、`codex exec` 或 Agent Views；来源只用于标注，不再决定是否可操作。服务只监听 Mac 回环地址，不开放局域网端口。

## 横屏指挥台

- 顶部持续显示等待介入、运行中、最近完成和 Mac 连接状态。
- 左侧按“需要你处理 → 可能需要你 → 运行中 → 最近完成”组织会话，而不是按来源或聊天时间平铺。
- 平板任意触控后进入 15 秒冷却；冷却结束后，新异常、明确卡点、可能卡点和新运行会话会按优先级自动切入内容区，普通完成不抢焦点。
- 冷却期间状态仍立即更新并显示一次性事件反馈；多次变化会合并，输入草稿按会话分别保存，自动切换不会丢失或串用草稿。
- 新卡点使用固定位置和暖橙边缘光；当前明确卡点不会被较低优先级的普通运行会话覆盖。
- 右侧直接展示协议问题或从最终回复中提取的自然语言卡点、真实选项、审批影响和必要上下文，底部固定保留语音、文字及 `1`、`2`、`A`、`B`、`继续` 快捷响应。
- 在线前台保持屏幕常亮；离线五分钟后释放常亮，连接恢复后自动同步且无需重启 App。

## 首次启用实时监听

安装用户级 Codex Hook。安装器只合并 Agent Views 自己的条目，不覆盖既有 Hook；配置变化时会在 `~/.codex/` 下保留带时间戳的备份：

```bash
npm run install:agent-views-hooks
```

然后在 Codex CLI 中执行 `/hooks`，核对命令只指向 `~/.agent-views/hooks/forward_event.py`，再选择信任。Hook 只向 `127.0.0.1:8765` 发送有限的会话状态字段；Bridge 不在线时会在 0.6 秒内失败退出，Codex 任务继续运行。

如需移除：

```bash
npm run uninstall:agent-views-hooks
```

## 日常启动

Agent Views 当前验证支持 Codex CLI `>=0.144.0` 且 `<0.154.0`；超出范围会拒绝启动，避免在未验证的 App Server 协议上静默运行。

在仓库根目录启动 Mac Bridge：

```bash
npm run start:agent-views -- --workspace "$PWD"
```

看到 `"ready": true` 后，在另一个终端建立 USB 隧道：

```bash
adb reverse tcp:8765 tcp:8765
adb reverse --list
```

然后打开平板上的 **Agent Views**。USB 重连或平板重启后需要重新执行 `adb reverse`。Bridge 会把已安全接入的 thread ID 和按消息记录的“不是卡点”决定保存在 `~/.agent-views/state.json`，Hook 令牌保存在权限为 `0600` 的 `~/.agent-views/hook-token`。

首次点击“语音”时，HyperOS 可能先显示“系统语音引擎”的用户协议和录音权限页；同意后才会进入语音识别。Agent Views 自身只声明 `INTERNET` 权限，录音由系统识别器处理。

## 首次构建和安装

本机 shell 默认 Java 版本较旧，构建时显式使用 Homebrew JDK 21：

```bash
cd agent_views/android
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
ANDROID_HOME=/opt/homebrew/share/android-commandlinetools \
./gradlew testDebugUnitTest assembleDebug lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

若 HyperOS 拒绝安装，请解锁平板，并在开发者选项中启用“USB 调试（安全设置）”和“通过 USB 安装”，再重试 `adb install -r`。

也可以不扩大 ADB 权限，改由系统安装器确认：

```bash
adb push app/build/outputs/apk/debug/app-debug.apk /sdcard/Download/AgentViews-debug.apk
```

然后在平板文件管理器中打开“内部存储设备 → Download → AgentViews-debug.apk”，按系统提示安装。

## 第一版边界

- Bridge 当前连接收到的 `requestUserInput` 和审批可原位结构化响应；权限审批只回传 Codex 原始请求中的权限集合。
- Desktop、IDE 和 CLI 会话的最新 turn 已结束时，Bridge 会在发送前用 `messageId + updatedAt` 复核，再通过 `thread/resume + turn/start` 继续同一个 thread。原客户端仍在运行或持有输入请求时只提示卡点，不冒险重复写入。
- 自然语言识别只分析最新已结束 turn 的最终 Agent 回复；commentary、代码块、引用、日志和“已完成，无需回复”等闭合结果不会触发强提醒。证据不足的问句进入“可能需要你”，可按消息选择“不是卡点”。
- 平板只投影用户与 Agent 文本；原始推理、命令输出、Hook transcript 路径、工具输入和环境变量不会通过 HTTP API 暴露。
- 固定快捷按钮向自然语言会话发送字面值 `1`、`2`、`A`、`B`、`继续`；结构化问题则匹配真实选项。
- 平板新建会话的首轮使用 Plan 模式；规划完成后点“继续”切到 Default 模式执行任务。
- 另一个 Codex 客户端连接中尚未结束的原始 server request 不能被第二条 App Server 连接冒充响应；这类会话会明确标为暂不可接管。
