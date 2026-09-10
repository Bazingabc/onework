# Codex 503 循环断连修复

日期：2026-09-10。状态：代码修复、独立审查、自动化回归及现场只读验收通过，已部署；未提交推送。

## 根因与现场证据

平板与 Mac 的 Wi-Fi/TLS 配对正常，但 Bridge 通过 `thread/read(includeTurns=true)` 读取长会话整份历史。实际响应超过 JSONL 客户端 16 MiB 单行安全限制，触发 `App Server 单行输出超过安全上限` 并关闭自有 Codex App Server。自动重连后同一请求再次超限，形成重启循环。

现场健康接口复现 HTTP 503，会话快照长期 stale/loading；恢复期间 generation 改变。Android 原错误解析只提取 `error.message`，健康接口仅有 `lastError`，因此用户只看到笼统的“Bridge 请求失败（503）”。

未删除或压缩用户历史，未修改 Codex 安全设置，未重置配对。

## 修复

- 当前 CLI 0.153.4 使用 `thread/read(includeTurns=false)` 读取元信息，`thread/turns/list(limit=4, sortDirection=desc, itemsView=summary)` 获取最近四轮，再恢复为正序，保留近期用户和助手消息用于状态识别与写前校验。
- 列表详情、独立详情、写前读取均统一走此路径。读取前后比较元信息的 updatedAt/status；变化则重试一次，仍变化则拒绝使用不一致结果。
- `thread/resume(excludeTurns=true)` 避免重新加载整份历史；返回空 turns 不覆盖刚检查的近期历史。
- 只有明确的远端 `-32601`（方法不存在）允许旧版本回退；超时或其他错误不会回退完整历史。
- 保留 16 MiB 防护。健康接口 503 增加标准 `error.code/message`，重连后的 `lastDisconnect` 保留最近断线原因。

协议依据：本机 0.153.4 生成 schema 及 [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)。独立只读探针中，两项长会话最近两轮摘要分别约 2.8 KB、28 KB，耗时 5–6 ms。

## 验证与部署

- Bridge 129 项、Mac 安装器 9 项、Hook 3 项，共 141 项通过；覆盖分页、旧协议回退、错误不回退、元信息变化、resume、服务入口、健康错误契约及重连后诊断保留。配对替换 19 项包含在 Bridge 回归中。
- 两位独立审查者检查协议/服务入口及恢复/Android 错误契约，未发现阻塞问题。
- Swift 编译、品牌校验、ad-hoc 签名与严格签名验证通过。候选为 `macos/dist/connection-recovery-20260910/OneWork.app`，安装至 `~/Applications/OneWork.app`；安装器保留旧应用备份 `~/Applications/.onework-backups/20260910T020938Z-3cf3a33a/OneWork.app`，保留 `~/.agent-views`。
- 仅重启 OneWork 自有 Bridge 与窗口进程，未终止其他 Codex 客户端进程。
- 平板现有 `com.one.onework` 未重装或清空；仅安装匹配包名的只读 instrumentation 测试。通过真实加密配对进行 TLS、协议 v2、列表/详情快照、非法凭证和错误证书固定值拒绝检查。10 次快照请求最慢 100 ms，无业务消息/审批/待办写入。
- 持续 48 秒采样 16 次，健康和列表均为 HTTP 200，列表 stale=false，generation 不变，lastError/lastDisconnect 为空。平板恢复前台后显示“Mac 已连接 · Wi-Fi”；Mac 概览显示 Codex 0.153.4 正常，一台在线。

## 未覆盖边界

- 四轮是轮数限制，不是严格字节限制；单轮含极大正文时仍可能触发既有 16 MiB 防护。此次解决的是整份长历史反复加载。
- updatedAt/status 比较是乐观校验，不是原子版本锁；既有同秒变更或校验后变更竞态未在本轮扩展解决。
- 实机兼容证据覆盖 CLI 0.153.4；旧版本回退有单测，无版本矩阵实测。
- 现场验收不发送业务消息，不处理审批。真实配对替换需要用户明确测试目标，本轮没有撤销任何现用凭证。
- Mac 概览读取成功；设备页点击时桌面操作工具断开，替换弹窗的真实点击流程尚待人工验收。
