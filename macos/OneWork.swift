import SwiftUI
import AppKit
import CoreImage.CIFilterBuiltins
import ServiceManagement

@MainActor final class Companion: ObservableObject {
    @Published var state: [String:Any] = [:]
    @Published var notice = "正在读取 Mac 状态…"
    @Published var error = false
    @Published var busy = false
    @Published var qr: NSImage?
    @Published var expires: Date?
    @Published var page = "概览"
    @Published var versions: [String:Any] = [:]
    @Published var authURL: String?
    @Published var authQR: NSImage?
    @Published var replacementRequest: [String:Any]?
    @Published var replacementError = ""
    private var polling = false
    private var stateGeneration = 0
    private var published: NetService?
    private var publishedPin = ""
    private var qrOrigin: String?
    private var timer: Timer?
    init() {
        timer=Timer.scheduledTimer(withTimeInterval:3,repeats:true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.refresh() }
        }
    }
    var service: [String:Any] { state["service"] as? [String:Any] ?? [:] }
    var ready: Bool { service["bridge"] as? String == "ready" }
    var devices: [[String:Any]] { state["devices"] as? [[String:Any]] ?? [] }
    var pending: [[String:Any]] { state["pending"] as? [[String:Any]] ?? [] }
    var configuration: [String:Any] { state["configuration"] as? [String:Any] ?? [:] }

    nonisolated static func invoke(_ args: [String]) -> [String:Any] {
        guard let resources=Bundle.main.resourceURL else { return ["ok":false,"error":"应用资源缺失"] }
        let task=Process(), out=Pipe(), err=Pipe()
        task.executableURL=resources.appendingPathComponent("python/bin/python3")
        task.arguments=["-m","agent_views.bridge.desktop"]+args
        task.currentDirectoryURL=resources.appendingPathComponent("runtime")
        var env=ProcessInfo.processInfo.environment
        env["PYTHONPATH"]=resources.appendingPathComponent("runtime").path
        env["PYTHONDONTWRITEBYTECODE"]="1"
        env["PATH"]="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        task.environment=env; task.standardOutput=out; task.standardError=err
        do {
            try task.run()
            let data=out.fileHandleForReading.readDataToEndOfFile()
            task.waitUntilExit()
            return (try? JSONSerialization.jsonObject(with:data)) as? [String:Any] ?? ["ok":false,"error":"本机服务响应异常，请检查诊断信息"]
        } catch { return ["ok":false,"error":error.localizedDescription] }
    }

    func refresh() {
        guard !polling else { return }; polling=true
        let generation=stateGeneration
        Task {
            let result=await Task.detached { Self.invoke(["status"]) }.value
            polling=false
            // An in-flight pre-replacement snapshot must not resurrect old UI.
            guard generation==stateGeneration else { refresh(); return }
            if result["ok"] as? Bool == true {
                state=result["data"] as? [String:Any] ?? [:]
                if notice == "正在读取 Mac 状态…" { notice="连接状态每 3 秒更新；数据源异常可分别处理。" }
                let lanURL=service["lanUrl"] as? String
                if qr != nil, let origin=qrOrigin, lanURL != origin {
                    qr=nil; expires=nil; qrOrigin=nil
                    notice="网络已变化，旧配对码已收起；已有设备会自动重连。"
                }
                if ready, let endpoint=lanURL, let pin=state["fingerprint"] as? String, publishedPin != pin+"@"+endpoint {
                    published?.stop()
                    let ns=NetService(domain:"local.",type:"_onework._tcp.",name:"OneWork-"+String(pin.prefix(10)),port:8766)
                    ns.setTXTRecord(NetService.data(fromTXTRecord:["fingerprint":Data(pin.utf8),"origin":Data(endpoint.utf8)])); ns.publish()
                    published=ns; publishedPin=pin+"@"+endpoint
                } else if !ready || lanURL == nil { published?.stop(); published=nil; publishedPin="" }
            }
            if let expires, expires < Date() { qr=nil; self.expires=nil }
        }
    }

    func act(_ args:[String]) {
        guard !busy else { return }; busy=true; error=false
        Task {
            let result=await Task.detached { Self.invoke(args) }.value
            busy=false
            guard result["ok"] as? Bool == true else {
                notice=result["error"] as? String ?? "操作失败"; error=true; return
            }
            let data=result["data"] as? [String:Any] ?? [:]
            if args.first=="pair", let raw=try? JSONSerialization.data(withJSONObject:data,options:[.sortedKeys]) {
                let filter=CIFilter.qrCodeGenerator(); filter.message=raw; filter.correctionLevel="M"
                if let output=filter.outputImage?.transformed(by:CGAffineTransform(scaleX:7,y:7)),
                   let cg=CIContext().createCGImage(output,from:output.extent) {
                    qr=NSImage(cgImage:cg,size:NSSize(width:300,height:300))
                }
                expires=Date(timeIntervalSince1970:data["expiresAt"] as? Double ?? 0)
                qrOrigin=data["origin"] as? String
                notice="请用平板扫码，然后在下方确认设备权限。"
                page="设备"
                if let pin=data["fingerprint"] as? String, let endpoint=qrOrigin, pin+"@"+endpoint != publishedPin {
                    published?.stop()
                    let ns=NetService(domain:"local.",type:"_onework._tcp.",name:"OneWork-"+String(pin.prefix(10)),port:8766)
                    ns.setTXTRecord(NetService.data(fromTXTRecord:["fingerprint":Data(pin.utf8),"origin":Data(endpoint.utf8)])); ns.publish()
                    published=ns; publishedPin=pin+"@"+endpoint
                }
            } else if args.first=="auth-start" {
                authURL=data["url"] as? String
                if let path=data["qr"] as? String { authQR=NSImage(contentsOfFile:path) }
                notice=data["message"] as? String ?? "请完成飞书授权"
            } else if args.first=="auth-finish" { authURL=nil; authQR=nil; notice="授权已核实，请刷新平板待办" }
            else if args.first=="probe" { versions=data; notice="环境检测完成" }
            else { notice=data["message"] as? String ?? "检查完成" }
            refresh()
        }
    }

    func choose(_ key:String) {
        let picker=NSOpenPanel(); picker.canChooseDirectories=key=="workspace"; picker.canChooseFiles=key != "workspace"
        picker.message=key=="workspace" ? "选择默认工作目录" : "选择本机 \(key) CLI 可执行文件"
        picker.showsHiddenFiles=true
        if picker.runModal() == .OK, let url=picker.url { act(["configure","--key",key,"--value",url.path]) }
    }

    func replacePairing(_ old:[String:Any], confirmOnline:Bool) {
        guard !busy, let request=replacementRequest else { return }
        busy=true; replacementError=""
        var args=["replace","--id",request.requestID,"--old-id",old.deviceID,
                  "--expected-role",old["role"] as? String ?? "control"]
        if confirmOnline { args.append("--confirm-online") }
        let command=args
        Task {
            let result=await Task.detached { Self.invoke(command) }.value
            stateGeneration += 1
            busy=false
            guard result["ok"] as? Bool == true else {
                replacementError=(result["error"] as? String ?? "响应未能确认")+"。请刷新核实；若替换已提交，旧连接不会恢复。"
                refresh(); return
            }
            replacementRequest=nil
            state["pending"]=pending.filter { $0.requestID != request.requestID }
            let data=result["data"] as? [String:Any] ?? [:]
            if let device=data["device"] as? [String:Any] {
                state["devices"]=devices.filter { $0.deviceID != old.deviceID && $0.deviceID != request.requestID } + [device]
            }
            notice=data["message"] as? String ?? "配对已替换，请核对设备列表"
            error=false; refresh()
        }
    }

    func confirm(_ title:String,_ detail:String,_ action:@escaping ()->Void) {
        let alert=NSAlert(); alert.messageText=title; alert.informativeText=detail
        alert.addButton(withTitle:"确认"); alert.addButton(withTitle:"取消")
        if alert.runModal() == .alertFirstButtonReturn { action() }
    }
}

struct CompanionView: View {
    @ObservedObject var model: Companion
    let pages=["概览","设备","数据源","诊断"]
    var body: some View {
        HStack(spacing:0) {
            VStack(alignment:.leading,spacing:10) {
                HStack(spacing:10) {
                    Image(nsImage:OneWorkApp.brandIcon).resizable().frame(width:32,height:32)
                    Text("OneWork").font(.title2.bold())
                }.padding(.bottom,24)
                ForEach(pages,id:\.self) { page in
                    Button { model.page=page } label: {
                        Text(page)
                            .frame(maxWidth:.infinity,alignment:.leading)
                            .padding(10)
                            // Plain buttons must include the label's empty space in hit testing.
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain).background(model.page==page ? Color.accentColor.opacity(0.12) : .clear).cornerRadius(8)
                }
                Spacer()
                Text("OneWork \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—")\n开发者内测版 · 凭证留在 Mac").font(.caption).foregroundStyle(.secondary)
            }.padding(24).frame(width:190).background(Color(nsColor:.controlBackgroundColor))
            Divider()
            ScrollView {
                VStack(alignment:.leading,spacing:22) {
                    HStack { Text(model.page).font(.largeTitle.bold()); Spacer(); if model.busy { ProgressView().controlSize(.small) }; Button("刷新") { model.refresh() } }
                    Text(model.notice).foregroundStyle(model.error ? .red : .secondary).textSelection(.enabled)
                    switch model.page {
                    case "设备": devices
                    case "数据源": sources
                    case "诊断": diagnostics
                    default: overview
                    }
                }.padding(30).frame(maxWidth:.infinity,alignment:.leading)
            }.frame(minWidth:580)
        }.frame(minWidth:850,minHeight:600)
        .task { model.refresh() }
        .sheet(isPresented:Binding(get:{ model.replacementRequest != nil },set:{ if !$0 && !model.busy { model.replacementRequest=nil } })) {
            ReplacePairingView(model:model)
        }
    }
    func statusRow(_ title:String,_ state:String,_ detail:String) -> some View {
        HStack(alignment:.top) {
            Circle().fill(state=="ready" ? .green : .orange).frame(width:9,height:9).padding(.top,7)
            VStack(alignment:.leading,spacing:6) { Text(title).font(.headline); Text(detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
            Spacer(); Text(state=="ready" ? "正常" : state=="offline" ? "未运行" : "需要检查").font(.caption)
        }.padding(18).background(Color(nsColor:.controlBackgroundColor)).cornerRadius(12)
    }
    var overview: some View {
        VStack(alignment:.leading,spacing:18) {
            statusRow("Mac 服务",model.ready ? "ready" : "offline",model.ready ? "局域网加密连接 · \(model.state["address"] as? String ?? "地址未知")" : "启动后，平板才能读取工作状态")
            let network=model.service["network"] as? [String:Any] ?? [:]
            if model.ready {
                statusRow("平板网络",network["state"] as? String ?? "ready",network["message"] as? String ?? "当前服务来自旧版；更新服务后支持网络自动恢复")
                Text("自动发现需保持菜单栏应用运行；可以关闭窗口。Mac 休眠期间暂停同步，唤醒后恢复。").font(.caption).foregroundStyle(.secondary)
            }
            let codex=model.service["codex"] as? [String:Any] ?? [:]
            statusRow("Codex",codex["state"] as? String ?? "offline",codex["error"] as? String ?? codex["version"] as? String ?? "等待检测")
            let lark=model.service["lark"] as? [String:Any] ?? [:]
            statusRow("飞书",lark["state"] as? String ?? "offline",lark["error"] as? String ?? "待办每五分钟刷新，可在平板手动刷新")
            Text("\(model.devices.filter { $0["online"] as? Bool == true }.count) 台在线 · \(model.devices.count) 台已配对 · 同时在线上限 3 台").foregroundStyle(.secondary)
            HStack {
                Button(model.ready ? "添加平板" : "启动服务") { model.act([model.ready ? "pair" : "start"]) }.buttonStyle(.borderedProminent).disabled(model.busy)
                if model.state["managed"] as? Bool == true {
                    Button("停止服务") { model.confirm("停止 OneWork 服务？","平板将断开。正在提交的操作可能需要人工核实；不会停止其他 Codex 进程。") { model.act(["stop"]) } }
                }
            }
            Button("设置登录后自动打开") {
                do { try SMAppService.mainApp.register(); model.notice="已请求登录启动；如需批准，请前往系统登录项设置" }
                catch { model.notice=error.localizedDescription }
            }
            Button("打开系统登录项设置") { SMAppService.openSystemSettingsLoginItems() }
        }
    }
    var devices: some View {
        VStack(alignment:.leading,spacing:16) {
            Text("每条记录代表一个配对客户端；重装或更换包名可能产生新身份。型号不能证明是同一台物理设备。").font(.caption).foregroundStyle(.secondary)
            Button("生成新的配对二维码") { model.act(["pair"]) }.disabled(!model.ready || model.busy)
            if let image=model.qr {
                Image(nsImage:image).interpolation(.none).resizable().frame(width:290,height:290).padding(16).background(.white).cornerRadius(12)
                if let expires=model.expires { Text("有效至 \(expires.formatted(date:.omitted,time:.standard)) · 仅供你的设备扫描").font(.caption).foregroundStyle(.secondary) }
            }
            ForEach(model.pending,id:\.requestID) { item in
                GroupBox("待确认：\(item.deviceTitle)") {
                    Text(item.deviceDetail).font(.caption).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading)
                    HStack {
                        Button("允许查看") { model.act(["approve","--id",item.requestID,"--role","view"]) }
                        Button("允许操作") { model.confirm("允许此平板操作？","它将能发送会话消息、响应审批和完成你的飞书待办。") { model.act(["approve","--id",item.requestID,"--role","control"]) } }
                        Button("拒绝") { model.act(["approve","--id",item.requestID,"--role","denied"]) }
                        if !model.devices.isEmpty {
                            Button("替换已有配对") { model.replacementError=""; model.replacementRequest=item }
                        }
                    }.padding(10).disabled(model.busy)
                }
            }
            ForEach(model.devices,id:\.deviceID) { item in
                GroupBox {
                    Text(item.deviceDetail).font(.caption).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading).textSelection(.enabled)
                    Text(item.deviceTimes).font(.caption).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading)
                    HStack {
                        Text(item["online"] as? Bool == true ? "在线" : "离线").foregroundStyle(.secondary)
                        Text(item["role"] as? String == "control" ? "允许操作" : "仅查看")
                        Spacer()
                        Button(item["role"] as? String == "control" ? "改为仅查看" : "允许操作") {
                            let role=item["role"] as? String == "control" ? "view" : "control"
                            model.confirm("修改设备权限？","允许操作包含发送消息、响应审批和完成飞书待办。") { model.act(["role","--id",item.deviceID,"--role",role]) }
                        }
                        Button("撤销访问") { model.confirm("撤销这台设备？","后续请求会被拒绝，已执行的操作不会撤回。") { model.act(["revoke","--id",item.deviceID]) } }
                    }.padding(10)
                } label: { Label(item.deviceTitle,systemImage:item.deviceSymbol) }
            }
            if model.devices.isEmpty { Text("尚未配对设备。生成二维码后，用平板 OneWork 扫描。").foregroundStyle(.secondary) }
        }
    }
    var sources: some View {
        VStack(alignment:.leading,spacing:20) {
            Text("复用已有 CLI；OneWork 不会自动升级或降级你的开发环境。").foregroundStyle(.secondary)
            ForEach(["codex","lark","workspace"],id:\.self) { key in
                GroupBox(key=="workspace" ? "默认工作目录" : key=="lark" ? "飞书 CLI" : "Codex CLI") {
                    VStack(alignment:.leading,spacing:10) {
                        Text(model.configuration[key] as? String ?? "未配置").textSelection(.enabled).font(.system(.callout,design:.monospaced))
                        if let version=model.versions[key] as? [String:Any] { Text(version["version"] as? String ?? "未找到可用版本").foregroundStyle(.secondary) }
                        Button("选择路径") { model.choose(key) }
                    }.frame(maxWidth:.infinity,alignment:.leading).padding(8)
                }
            }
            HStack { Button("检测 CLI 版本") { model.act(["probe"]) }; Button("核实飞书授权") { model.act(["auth-check"]) } }
            Button("修复飞书待办授权") { model.confirm("发起飞书待办授权？","仅申请任务业务域，不申请通讯录、聊天和云文档；最终权限请在飞书页面核对。") { model.act(["auth-start"]) } }
            if let raw=model.authURL, let url=URL(string:raw) {
                Link("打开飞书授权页面",destination:url)
                if let image=model.authQR { Image(nsImage:image).resizable().interpolation(.none).frame(width:220,height:220) }
                Button("我已完成授权") { model.act(["auth-finish"]) }
            }
            if let auth=model.state["auth"] as? [String:Any], !auth.isEmpty {
                Text("飞书身份：\(auth["userName"] as? String ?? "未确认") · \(auth["status"] as? String ?? auth["error"] as? String ?? "请查看 CLI 授权状态")")
            }
            Text("Codex 当前版本准入：0.144.0 ≤ 版本 < 0.154.0。版本准入不等同于所有功能均已验证。").font(.caption).foregroundStyle(.secondary)
        }
    }
    var diagnostics: some View {
        VStack(alignment:.leading,spacing:16) {
            GroupBox("安装与版本") {
                VStack(alignment:.leading,spacing:8) {
                    Text("版本 \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—") · 构建 \(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—")")
                    Text(Bundle.main.bundleURL.path).font(.system(.caption,design:.monospaced)).textSelection(.enabled)
                    Text(Bundle.main.bundleURL.path == NSHomeDirectory()+"/Applications/OneWork.app" ? "已安装到固定位置；升级保留配对和历史数据" : "当前运行的是候选包；请安装到用户 Applications 后使用").foregroundStyle(.secondary)
                    Button("在访达中显示应用") { NSWorkspace.shared.activateFileViewerSelecting([Bundle.main.bundleURL]) }
                    Text("本轮使用本地安装工具升级和回退，尚不提供联网自动更新。").font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth:.infinity,alignment:.leading).padding(8)
            }
            Text("结果待确认的操作不会自动重发。请先在原应用核实；不要通过重启绕过确认。").foregroundStyle(.secondary)
            let operations=model.service["operations"] as? [[String:Any]] ?? []
            ForEach(operations,id:\.operationID) { item in
                VStack(alignment:.leading,spacing:5) {
                    Text("\(item["target"] as? String ?? "") · \(item["state"] as? String ?? "")").font(.headline)
                    Text(item.operationID).font(.system(.caption,design:.monospaced)).textSelection(.enabled)
                    if item["state"] as? String == "unknown" {
                        Button("已在原应用核实") {
                            model.confirm("确认已核实实际结果？","此操作不会重新发送；解除阻塞后才能提交新操作。请确保已在 Codex 或飞书检查是否执行成功。") { model.act(["acknowledge","--id",item.operationID]) }
                        }
                    }
                }
                Divider()
            }
            if operations.isEmpty { Text("暂无操作记录").foregroundStyle(.secondary) }
            Text("本机验收包 · 尚未 Developer ID 公证\nMac 睡眠时不会维持实时同步；无需关闭系统安全设置。").font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct ReplacePairingView: View {
    @ObservedObject var model:Companion
    @State private var selected:[String:Any]?
    @State private var confirmOnline=false
    var targetOnline:Bool {
        guard let selected else { return false }
        return selected["online"] as? Bool == true || model.devices.first(where:{ $0.deviceID==selected.deviceID })?["online"] as? Bool == true
    }
    var body: some View {
        VStack(alignment:.leading,spacing:16) {
            Text("替换已有配对").font(.title2.bold())
            Text("新客户端：\(model.replacementRequest?.deviceTitle ?? "Android")").font(.headline)
            Text("请选择要替换的记录。不会按型号自动合并，也不会修改其他设备的权限。").foregroundStyle(.secondary)
            ScrollView {
                VStack(spacing:10) {
                    ForEach(model.devices,id:\.deviceID) { item in
                        Button {
                            selected=item; confirmOnline=false; model.replacementError=""
                        } label: {
                            HStack(alignment:.top,spacing:12) {
                                Image(systemName:selected?.deviceID==item.deviceID ? "largecircle.fill.circle" : "circle")
                                VStack(alignment:.leading,spacing:6) {
                                    Text(item.deviceTitle).font(.headline)
                                    Text(item.deviceDetail)
                                    Text(item.deviceTimes)
                                    Text("\(item["role"] as? String == "control" ? "允许操作" : "仅查看") · \(item["online"] as? Bool == true ? "在线" : "离线")")
                                }.font(.callout)
                                Spacer()
                            }.padding(14).frame(maxWidth:.infinity,alignment:.leading).contentShape(Rectangle())
                        }.buttonStyle(.plain).background(Color(nsColor:.controlBackgroundColor)).cornerRadius(10)
                    }
                }
            }.frame(minHeight:160,maxHeight:300).disabled(model.busy)
            if let selected {
                Text("将替换：\(selected.deviceTitle) · 编号 \(selected.deviceID.suffix(6).uppercased())").font(.headline)
                Text("新连接将继承此配对的权限（\(selected["role"] as? String == "control" ? "允许操作" : "仅查看")），旧连接将失效；不会删除平板本地数据。已受理操作不会撤回。")
                if targetOnline {
                    Label("旧客户端当前在线，替换会使其后续请求被拒绝。",systemImage:"exclamationmark.triangle.fill").foregroundStyle(.orange)
                    Toggle("我确认断开这个在线客户端",isOn:$confirmOnline).disabled(model.busy)
                }
            }
            if !model.replacementError.isEmpty { Text(model.replacementError).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Button("刷新列表") { model.refresh() }.disabled(model.busy)
                Spacer()
                Button("取消") { model.replacementRequest=nil }.disabled(model.busy)
                Button(model.busy ? "正在替换…" : "确认替换") {
                    if let selected { model.replacePairing(selected,confirmOnline:confirmOnline) }
                }.buttonStyle(.borderedProminent)
                    .disabled(model.busy || selected == nil || (targetOnline && !confirmOnline))
            }
        }.padding(26).frame(width:650).interactiveDismissDisabled(model.busy)
    }
}

extension Dictionary where Key==String, Value==Any {
    var deviceTimes:String {
        func date(_ key:String,_ missing:String) -> String {
            guard let seconds=self[key] as? Double, seconds>0 else { return missing }
            return Date(timeIntervalSince1970:seconds).formatted(date:.abbreviated,time:.shortened)
        }
        return "配对时间：\(date("pairedAt","未知")) · 最后在线：\(date("lastSeen","尚未连接"))"
    }
    var deviceMetadata:[String:Any] { self["metadata"] as? [String:Any] ?? [:] }
    var deviceTitle:String {
        let data=deviceMetadata
        if let product=data["productName"] as? String, !product.isEmpty { return product }
        let brand=data["brand"] as? String ?? ""
        let model=data["model"] as? String ?? self["name"] as? String ?? "Android 设备"
        return brand.isEmpty || model.lowercased().hasPrefix(brand.lowercased()) ? model : brand+" "+model
    }
    var deviceSymbol:String { deviceMetadata["category"] as? String == "tablet" ? "ipad.landscape" : "display" }
    var deviceDetail:String {
        let data=deviceMetadata
        let category=["tablet":"平板","phone":"手机","tv":"电视","watch":"手表","car":"车载设备"][data["category"] as? String ?? ""] ?? "Android 设备"
        var fields=[category]
        if let model=data["model"] as? String, !model.isEmpty { fields.append("型号 "+model) }
        if let version=data["androidVersion"] as? String, !version.isEmpty { fields.append("Android "+version) }
        let id=deviceID.isEmpty ? requestID : deviceID
        if !id.isEmpty { fields.append("编号 "+String(id.suffix(6)).uppercased()) }
        return fields.joined(separator:" · ")
    }
    var deviceID:String { self["deviceId"] as? String ?? "" }
    var requestID:String { self["requestId"] as? String ?? "" }
    var operationID:String { self["operationId"] as? String ?? "" }
}

@main struct OneWorkApp: App {
    @StateObject private var model=Companion()
    var body: some Scene {
        Window("OneWork",id:"main") { CompanionView(model:model) }.defaultSize(width:940,height:700)
        MenuBarExtra {
            MenuContent(model:model)
        } label: { Image(nsImage: Self.menuIcon).accessibilityLabel("OneWork") }
    }
    static var menuIcon:NSImage {
        let image=Bundle.main.url(forResource:"OneWorkMenu",withExtension:"pdf").flatMap { NSImage(contentsOf:$0) } ?? NSImage(size:NSSize(width:18,height:18))
        image.size=NSSize(width:18,height:18); image.isTemplate=true
        return image
    }
    static var brandIcon:NSImage {
        Bundle.main.url(forResource:"OneWorkIcon",withExtension:"png").flatMap { NSImage(contentsOf:$0) } ?? NSImage(size:NSSize(width:32,height:32))
    }
}

struct MenuContent: View {
    @ObservedObject var model:Companion
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Text(model.ready ? "Mac 服务正常" : "Mac 服务未运行")
        Button("打开 OneWork") {
            openWindow(id:"main")
            if let window=NSApp.windows.first(where:{ $0.title=="OneWork" && !($0 is NSPanel) }) {
                if window.isMiniaturized { window.deminiaturize(nil) }
                window.makeKeyAndOrderFront(nil)
            }
            NSApp.activate(ignoringOtherApps:true)
        }
        Divider()
        Button("退出 OneWork 界面") { NSApp.terminate(nil) }
    }
}
