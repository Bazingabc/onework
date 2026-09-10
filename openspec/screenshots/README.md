# README 模拟截图

[返回产品介绍](../../README.md)

## 图片来源

- `android-home.png`：当前原生首页，填入虚构飞书待办、Agent 状态和额度。
- `android-agents.png`：当前原生 Agent 页面，填入虚构任务和等待回复的问题。
- `MockData.java`：公开的固定业务数据；展示时间根据捕获时刻生成，不读取真实账户。
- `DocsCapture.java`：将演示应用自己的 View 渲染为 PNG，并附加 DEMO 标识；不截取设备桌面、系统通知或其他应用。
- `capture.py`：准备临时 Android 源码副本、构建、捕获并清理演示应用。

这些是真实 UI 使用模拟数据的截图，**不是 AI 重绘、不是线上数据，也不是网络或业务操作验收结果**。

## 重新生成

要求 Python、项目 Android 构建环境，以及一台已授权 ADB、解锁并横置的 Android 设备。先按[安装指南](../installation.md)确认 JDK 21、SDK 36 和 Gradle 实际使用的 JVM。

```sh
# 从仓库根目录执行；必须明确选择设备
adb devices -l
python3 openspec/screenshots/capture.py --serial YOUR_DEVICE_SERIAL
```

ADB 不在 PATH 时可添加 `--adb /absolute/path/to/adb`。默认更新此目录的两张 PNG；用 `--output /path/to/output` 可以先输出到其他目录供检查。

脚本会在设备上临时安装并前台启动 `com.one.onework.docs` 及其 `.test` 测试包，结束后卸载这两个包。**不会覆盖正式包 `com.one.onework`，也不会读取或复制正式包的数据。** 如果目标演示包已存在，脚本会拒绝覆盖，需先检查其用途。

截图副本移除了 INTERNET 和 CAMERA 权限，禁用了局域网发现；API 传输只返回本地 fixture，写请求一律拒绝。它不是可供实际使用的离线产品版本，不能用于发送消息、审批或完成待办。

源码中的插入位置变化时，脚本会明确失败，而不是跳过隔离步骤继续截图。执行中断后如遗留演示包，应先确认包名和用途，再明确卸载这两个演示包；不要卸载正式应用。

## 发布前检查

逐张打开 PNG，确认：内容均为虚构数据；首页和 Agent 页面确实不同；字体、图标和布局无裁切；底部 DEMO 标识可见。隐私保护来自输入隔离与逐图检查，不是事后给真实截图打码。
