import AppKit
import WebKit
import Darwin
import CryptoKit
import Sparkle

let applicationName = "PolyU study控制台"
let applicationVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.9.1"
let canvasHost = "https://canvas.polyu.edu.hk"

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, URLSessionTaskDelegate {
    var updaterController: SPUStandardUpdaterController?
    var pendingUpdateVersion: String?
    var updateGeneration = 0
    var pendingUpdateInstall: (() -> Void)?
    var window: NSWindow!
    var console: WKWebView!
    var loginWindow: NSWindow?
    var loginView: WKWebView?
    var loginAddress: NSTextField?
    var auxiliaryWindows: [NSWindow] = []
    var backend: Process?
    var diagnosticQueue: [[String: String]] = []
    var diagnosticSending = false
    var loginCompleted = false
    var credentialAttempt: LoginCredentialAttempt?
    var credentialVault: CredentialVault { CredentialVault(root: dataDirectory) }
    var readyTimer: Timer?
    var baseURL: URL?
    var nativeSecret = UUID().uuidString + UUID().uuidString
    var connecting = false
    var stopping = false
    var launchTime = Date()
    var resettingAccount = false
    var terminating = false
    var accountGeneration = UUID()
    var nativeSession = URLSession(configuration: .ephemeral)
    var pendingNotice: String?
    var readyFile: URL!
    var dataDirectory: URL!
    var testIsolation: TestIsolation?
    var testRule: WKContentRuleList?
    var loginDataStore: WKWebsiteDataStore?
    var guiSmoke: NativeGUISmoke?
    var currentAlert: NSAlert?
    var backendPIDs: [pid_t] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        var startupValidated=false
        defer { if startupValidated { initializeUpdater(); if testIsolation == nil { UpdateRecovery.recordLaunch() } } }
        do { testIsolation = try TestIsolation.parse(ProcessInfo.processInfo.arguments) }
        catch {
            FileHandle.standardError.write(Data("隔离测试拒绝启动：\(error.localizedDescription)\n".utf8))
            NSApp.terminate(nil)
            return
        }
        startupValidated=true
        if let isolation = testIsolation {
            do { try isolation.prepareControl() }
            catch { FileHandle.standardError.write(Data("隔离测试拒绝启动：\(error.localizedDescription)\n".utf8)); NSApp.terminate(nil); return }
            dataDirectory = isolation.data
            if let phase = isolation.phase,
               let bytes = try? JSONSerialization.data(withJSONObject: ["nativePID": getpid(), "candidateBundle": Bundle.main.bundleURL.path]) {
                try? bytes.write(to: isolation.control.appendingPathComponent("started-" + phase + ".json"), options: .atomic)
            }
            loginDataStore = .nonPersistent()
            let configuration = URLSessionConfiguration.ephemeral
            configuration.connectionProxyDictionary = [:]
            nativeSession.invalidateAndCancel()
            nativeSession = URLSession(configuration: configuration)
            let rules = "[{\"trigger\":{\"url-filter\":\"^https?://\",\"unless-domain\":[\"127.0.0.1\"]},\"action\":{\"type\":\"block\"}}]"
            let ruleDirectory = isolation.control.appendingPathComponent("web-rules", isDirectory: true)
            do { try FileManager.default.createDirectory(at: ruleDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700]) }
            catch { NSApp.terminate(nil); return }
            WKContentRuleListStore(url: ruleDirectory)
                .compileContentRuleList(forIdentifier: "polyu-isolated-loopback", encodedContentRuleList: rules) { rule, error in
                    guard let rule = rule else {
                        FileHandle.standardError.write(Data("隔离 WebKit 规则不可用：\(String(describing: error))\n".utf8))
                        NSApp.terminate(nil); return
                    }
                    self.testRule = rule
                    self.finishLaunching()
                }
        } else {
            dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent(applicationName, isDirectory: true)
            finishLaunching()
        }
    }

    func finishLaunching() {
        makeMenu()
        makeConsole()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = applicationName + (testIsolation == nil ? "" : " · 隔离测试（不连接学校）")
        window.minSize = NSSize(width: 850, height: 620)
        window.contentView = console
        window.delegate = self
        NotificationCenter.default.addObserver(self, selector: #selector(runtimeStateChanged), name: NSWindow.didChangeOcclusionStateNotification, object: window)
        NotificationCenter.default.addObserver(self, selector: #selector(runtimeStateChanged), name: NSNotification.Name.NSProcessInfoPowerStateDidChange, object: nil)
        window.isReleasedWhenClosed = false
        if testIsolation == nil { window.setFrameAutosaveName("PolyUConsoleMain") }
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let isolation = testIsolation, isolation.phase != nil {
            guiSmoke = NativeGUISmoke(app: self, isolation: isolation)
            guiSmoke?.start()
        }
        if FileManager.default.fileExists(atPath: dataDirectory.appendingPathComponent("logout-pending").path) {
            resetAccountStorage(message: "正在完成上次退出登录的账号清理…", logoutRequested: true)
            return
        }
        let format = try? String(contentsOf: dataDirectory.appendingPathComponent("framework-version"), encoding: .utf8)
        if format == "3\n" { startBackend() }
        else if hasAuthenticatedAccount() {
            do {
                try Data("3\n".utf8).write(to: dataDirectory.appendingPathComponent("framework-version"), options: .atomic)
                startBackend()
            } catch { alert("无法更新账号工作区", error.localizedDescription) }
        }
        else {
            // Unknown/corrupt profiles must never be erased as an initialization shortcut.
            do {
                if (try? dataDirectory.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
                    throw NSError(domain: "PolyU", code: 1)
                }
                try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true,
                                                        attributes: [.posixPermissions: 0o700])
                try Data("3\n".utf8).write(to: dataDirectory.appendingPathComponent("framework-version"), options: .atomic)
                startBackend()
            } catch { alert("现有资料已保留", "无法初始化控制台，请先核对资料目录。") }
        }
    }

    func schoolDataStore() -> WKWebsiteDataStore {
        if let existing = loginDataStore { return existing }
        let store = WKWebsiteDataStore.nonPersistent()
        loginDataStore = store
        return store
    }

    func hasAuthenticatedAccount() -> Bool {
        func object(_ directory: String, _ name: String) -> [String: Any] {
            let file = dataDirectory.appendingPathComponent(directory + "/" + name)
            guard let bytes = try? Data(contentsOf: file),
                  let value = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any] else { return [:] }
            return value
        }
        for directory in ["account", ".jiujiastudy"] {
            let login = object(directory, "login.json")
            let config = object(directory, "config.json")
            if login["transport"] as? String == "webkit", let currentID = login["user_id"],
               let ownerID = (config["user"] as? [String: Any])?["id"],
               String(describing: currentID) == String(describing: ownerID) { return true }
        }
        return false
    }

    func makeConsole() {
        console?.stopLoading()
        console?.configuration.userContentController.removeScriptMessageHandler(forName: "polyu")
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        if let rule = testRule { configuration.userContentController.add(rule) }
        configuration.userContentController.add(self, name: "polyu")
        console = WKWebView(frame: .zero, configuration: configuration)
        console.navigationDelegate = self
        console.uiDelegate = self
        if window != nil { window.contentView = console }
    }

    func makeMenu() {
        let bar = NSMenu()
        let root = NSMenuItem()
        bar.addItem(root)
        let applicationMenu = NSMenu(title: applicationName)
        root.submenu = applicationMenu
        applicationMenu.addItem(withTitle: "关于 " + applicationName, action: #selector(about), keyEquivalent: "")
        applicationMenu.addItem(withTitle: "设置…", action: #selector(openSettings), keyEquivalent: ",")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "连接 PolyU Canvas…", action: #selector(openLogin), keyEquivalent: "l")
        applicationMenu.addItem(withTitle: "退出登录…", action: #selector(logout), keyEquivalent: "")
        applicationMenu.addItem(withTitle: "打开资料文件夹", action: #selector(openDataFolder), keyEquivalent: "")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "隐藏 " + applicationName, action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        applicationMenu.addItem(withTitle: "退出 " + applicationName, action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editRoot = NSMenuItem()
        bar.addItem(editRoot)
        let edit = NSMenu(title: "编辑")
        editRoot.submenu = edit
        edit.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let viewRoot = NSMenuItem()
        bar.addItem(viewRoot)
        let view = NSMenu(title: "显示")
        viewRoot.submenu = view
        view.addItem(withTitle: "重新载入控制台", action: #selector(reloadConsole), keyEquivalent: "r")
        view.addItem(withTitle: "返回学习总览", action: #selector(goHome), keyEquivalent: "1")
        view.addItem(withTitle: "进入全屏", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "")
        NSApp.mainMenu = bar
    }

    func showStartup(_ message: String) {
        let logoURL = Bundle.main.resourceURL?.appendingPathComponent("console/assets/polyu-logo.png")
        let logoData = logoURL.flatMap { try? Data(contentsOf: $0) }?.base64EncodedString() ?? ""
        console.loadHTMLString("""
        <html lang="zh"><body style="margin:0;background:#f5f5f7;font-family:-apple-system;text-align:center;padding-top:20vh;color:#751431">
        <div style="display:inline-block;background:white;border-radius:12px;padding:20px 26px"><img src="data:image/png;base64,\(logoData)" alt="香港理工大学" style="width:330px;max-width:75vw;height:auto"></div>
        <h2 style="font-size:22px">\(message)</h2><p style="color:#887680">你的个人学习工作区</p></body></html>
        """, baseURL: nil)
    }

    func startBackend() {
        guard !terminating else { return }
        showStartup("正在打开学习控制台…")
        stopping = false
        launchTime = Date()
        nativeSecret = UUID().uuidString + UUID().uuidString
        nativeSession.invalidateAndCancel()
        let localConfiguration = URLSessionConfiguration.ephemeral
        localConfiguration.connectionProxyDictionary = [:]
        nativeSession = URLSession(configuration: localConfiguration, delegate: self, delegateQueue: nil)
        do {
            try testIsolation?.validate()
            try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            guard let resources = Bundle.main.resourceURL else { throw NSError(domain: "PolyU", code: 1) }
            let architecture = "arm64"
            let executable = resources.appendingPathComponent("runtime/\(architecture)/bin/python3")
            let server = resources.appendingPathComponent("console/server.py")
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                alert("应用的运行环境不完整", "请重新复制完整的 \(applicationName).app。")
                return
            }
            readyFile = dataDirectory.appendingPathComponent(".ready-\(UUID().uuidString).json")
            // Retire the raw HTTP log; only allowlisted structured events remain.
            try? FileManager.default.removeItem(at: dataDirectory.appendingPathComponent("app.log"))
            var environment = ProcessInfo.processInfo.environment
            environment["POLYU_NATIVE_APP"] = "1"
            environment["POLYU_APP_VERSION"] = applicationVersion
            environment["POLYU_NATIVE_SECRET"] = nativeSecret
            environment["POLYU_CONSOLE_DATA_DIR"] = dataDirectory.path
            environment["POLYU_DOCUMENT_READER"] = resources.appendingPathComponent("read-tools/DocumentReader").path
            environment["POLYU_READY_FILE"] = readyFile.path
            environment["POLYU_SESSION_VAULT"] = resources.appendingPathComponent("read-tools/SessionVault").path
            environment["PYTHONNOUSERSITE"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONUNBUFFERED"] = "1"
            environment["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
            environment.removeValue(forKey: "PYTHONHOME")
            environment.removeValue(forKey: "PYTHONPATH")
            environment.removeValue(forKey: "POLYU_CONSOLE_PORT")
            let process = Process()
            process.executableURL = executable
            if testIsolation != nil {
                // Diagnostic mode is offline: this guard runs before the shared backend imports.
                let guardedLaunch = """
                import os,runpy,sys
                def guard(event,args):
                    if event in {'socket.bind','socket.connect'}:
                        address=args[1]
                        if not isinstance(address,tuple) or address[0]!='127.0.0.1': raise RuntimeError('Isolated test blocks external network')
                    if event=='socket.getaddrinfo' and args[0]!='127.0.0.1': raise RuntimeError('Isolated test blocks external DNS')
                sys.addaudithook(guard)
                script=sys.argv[1];sys.path.insert(0,os.path.dirname(script));sys.argv=[script,'--no-open']
                runpy.run_path(script,run_name='__main__')
                """
                process.arguments = ["-B", "-c", guardedLaunch, server.path]
            } else { process.arguments = [server.path, "--no-open"] }
            process.environment = environment
            process.currentDirectoryURL = dataDirectory
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            process.terminationHandler = { [weak self] child in
                DispatchQueue.main.async {
                    guard let self = self, !self.stopping, self.backend === child else { return }
                    self.stopBackend()
                    self.baseURL = nil
                    self.recordDiagnostic("backend.stop", "failure", "SERVICE_UNAVAILABLE")
                    self.showStartup("学习服务已停止")
                    self.alert("控制台未能继续运行", "请重新载入控制台，或退出后重新打开应用。恢复后可在设置中查看运行日志。")
                }
            }
            backend = process
            try process.run()
            backendPIDs.append(process.processIdentifier)
            if let isolation = testIsolation, let phase = isolation.phase,
               let bytes = try? JSONSerialization.data(withJSONObject: ["backendPIDs": backendPIDs]) {
                try? bytes.write(to: isolation.control.appendingPathComponent("backends-" + phase + ".json"), options: .atomic)
            }
            readyTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.checkReady() }
        } catch { recordDiagnostic("backend.start", "failure", "IO_ERROR"); alert("无法启动控制台", error.localizedDescription) }
    }

    func checkReady() {
        if let bytes = try? Data(contentsOf: readyFile),
           let json = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any],
           let address = json["url"] as? String, let url = URL(string: address), url.host == "127.0.0.1",
           url.scheme == "http", url.user == nil, url.password == nil, url.path == "/",
           url.query == nil, url.fragment == nil, let port = url.port, port > 0, port < 65536,
           let pid = json["pid"] as? Int, pid == Int(backend?.processIdentifier ?? -1) {
            readyTimer?.invalidate()
            baseURL = url
            flushDiagnostics()
            loadConsole()
            try? FileManager.default.removeItem(at: readyFile)
        } else if Date().timeIntervalSince(launchTime) > 25 {
            readyTimer?.invalidate()
            recordDiagnostic("backend.start", "failure", "TIMEOUT")
            alert("控制台启动超时", "请退出后重新打开应用。恢复后可在设置中查看运行日志。")
        }
    }

    func stopBackend() {
        stopping = true
        readyTimer?.invalidate()
        if let process = backend, process.isRunning {
            // The native backend creates its own process group, including sync workers.
            if kill(-process.processIdentifier, SIGTERM) != 0 { process.terminate() }
        }
        if let ready = readyFile { try? FileManager.default.removeItem(at: ready) }
        backend = nil
    }

    func applicationWillTerminate(_ notification: Notification) {
        terminating = true
        credentialAttempt?.close()
        nativeSession.invalidateAndCancel()
        stopBackend()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "canvasCredentials" {
            receiveCredentials(message)
            return
        }
        if message.name == "canvasLogin" {
            guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.host == "canvas.polyu.edu.hk",
                  let body = message.body as? [String: Any], body["authenticated"] as? Bool == true else { return }
            exportSession()
            return
        }
        guard message.name == "polyu", message.webView === console, message.frameInfo.isMainFrame,
              message.frameInfo.request.url?.host == "127.0.0.1", message.frameInfo.request.url?.port == baseURL?.port,
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "softwareUpdate":
            if let install = pendingUpdateInstall { prepareUpdateRestart(install) } else { checkSoftwareUpdates() }
        case "rememberStatus": publishCredentialStatus()
        case "rememberEnable": enableRememberedPassword()
        case "rememberForget": forgetRememberedPassword()
        case "login": openLogin()
        case "logout": logout()
        case "dataFolder": openDataFolder()
        case "filesFolder": openFilesFolder()
        default: break
        }
    }

    @objc func openLogin() {
        guard baseURL != nil, !resettingAccount else { return }
        recordDiagnostic("login.open", "attempt")
        if testIsolation != nil {
            recordDiagnostic("login.open", "failure", "TEST_MODE")
            alert("隔离测试模式", "此模式使用临时目录和非持久登录数据域，不访问学校或验证真实 SSO。")
            return
        }
        if let existing = loginWindow, existing.isVisible { existing.makeKeyAndOrderFront(nil); recordDiagnostic("login.open", "success"); return }
        loginCompleted = false
        credentialAttempt?.close()
        let attempt = LoginCredentialAttempt()
        credentialAttempt = attempt
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = schoolDataStore()
        configuration.userContentController.add(self, name: "canvasLogin")
        configuration.userContentController.add(self, name: "canvasCredentials")
        if let script = credentialScript(attempt: attempt) {
            configuration.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        }
        let script = """
        (() => { if (location.origin !== '\(canvasHost)') return;
          const check = () => { try {
            const scripts = [...document.scripts].map(s => s.textContent || '').join(' ');
            const match = scripts.match(/"current_user_id":"?(\\d+)/);
            if (match && document.querySelector('#global_nav_dashboard_link'))
              window.webkit.messageHandlers.canvasLogin.postMessage({authenticated:true});
          } catch (_) {} }; check(); const timer = setInterval(check, 3000); window.polyuLoginStop = () => clearInterval(timer); })();
        """
        configuration.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        loginView = view
        let login = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1050, height: 790),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        login.title = "登录并授权 · PolyU Canvas"
        login.isReleasedWhenClosed = false
        login.delegate = self
        login.minSize = NSSize(width: 700, height: 570)
        let content = NSView()
        login.contentView = content
        let address = NSTextField(labelWithString: "在学校页面输入账号并完成验证；连接仅用于读取学习信息。")
        address.font = NSFont.systemFont(ofSize: 12)
        address.textColor = NSColor.secondaryLabelColor
        address.maximumNumberOfLines = 2
        loginAddress = address
        for child in [address, view] { child.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(child) }
        NSLayoutConstraint.activate([
            address.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            address.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            address.topAnchor.constraint(equalTo: content.topAnchor, constant: 10),
            address.heightAnchor.constraint(equalToConstant: 40),
            view.topAnchor.constraint(equalTo: address.bottomAnchor, constant: 8),
            view.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            view.leadingAnchor.constraint(equalTo: content.leadingAnchor), view.trailingAnchor.constraint(equalTo: content.trailingAnchor)
        ])
        loginWindow = login
        login.center()
        login.makeKeyAndOrderFront(nil)
        view.load(URLRequest(url: URL(string: canvasHost)!))
        recordDiagnostic("login.open", "success")
        notice("请在学校登录窗口完成账号登录和多重验证。")
    }

    func exportSession() {
        guard testIsolation == nil else { return }
        guard !connecting, let view = loginView, loginWindow?.isVisible == true else { return }
        connecting = true
        recordDiagnostic("login.export", "attempt")
        let generation = accountGeneration
        loginAddress?.stringValue = "已完成学校登录，正在验证 Canvas 授权并建立本机学习档案…"
        view.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self] cookies in
            guard let self = self, self.accountGeneration == generation, !self.resettingAccount, !self.terminating, self.loginView === view, self.loginWindow?.isVisible == true else { return }
            let rows: [[String: Any]] = cookies.filter {
                let domain = $0.domain.trimmingCharacters(in: CharacterSet(charactersIn: "."))
                return domain == "canvas.polyu.edu.hk" || domain == "polyu.edu.hk"
            }.map { cookie in ["name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                               "path": cookie.path, "secure": cookie.isSecure, "httpOnly": cookie.isHTTPOnly,
                               "expires": cookie.expiresDate?.timeIntervalSince1970 ?? -1] }
            self.postNative("session", payload: ["cookies": rows]) { result in
                self.connecting = false
                switch result {
                case .success(let response):
                    self.recordDiagnostic("login.export", "success")
                    self.saveLoginCredential(response: response) {
                        guard self.accountGeneration == generation, !self.resettingAccount, !self.terminating else { return }
                        self.loginCompleted = true
                        self.loginWindow?.close()
                        self.auxiliaryWindows.forEach { $0.close() }
                        self.auxiliaryWindows.removeAll()
                        self.notice(response["message"] as? String ?? "Canvas 已连接。", type: "success")
                        self.reloadData()
                        self.publishCredentialStatus()
                        self.window.makeKeyAndOrderFront(nil)
                    }
                case .failure(let error):
                    self.credentialAttempt?.candidate = nil
                    let code = (error as NSError).code
                    self.recordDiagnostic("login.export", "failure", [400,403,409,500,502].contains(code) ? "HTTP_\(code)" : "NETWORK_ERROR")
                    self.loginAddress?.stringValue = error.localizedDescription
                    self.notice(error.localizedDescription, type: "error")
                }
            }
        }
    }


    func credentialOwner() -> String? {
        let file = dataDirectory.appendingPathComponent("account/config.json")
        guard let data = try? Data(contentsOf: file), let config = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let user = config["user"] as? [String: Any], let id = user["id"] else { return nil }
        return String(describing: id)
    }
    func publishCredentialStatus() {
        guard dataDirectory != nil, console != nil, !resettingAccount else { return }
        if let bytes = try? JSONSerialization.data(withJSONObject: credentialVault.status(owner: credentialOwner())), let text = String(data: bytes, encoding: .utf8) {
            console.evaluateJavaScript("window.polyuRememberStatus?.(" + text + ")")
        }
    }
    func enableRememberedPassword() {
        guard !resettingAccount, currentAlert == nil else { publishCredentialStatus(); return }
        let dialog = NSAlert(); dialog.messageText = "在本机记住账号和密码？"; dialog.informativeText = CredentialVault.consentText
        dialog.addButton(withTitle: "同意并开启"); dialog.addButton(withTitle: "取消"); currentAlert = dialog
        dialog.beginSheetModal(for: window) { [weak self] response in
            guard let self = self else { return }; self.currentAlert = nil
            if response == .alertFirstButtonReturn {
                do {
                    try self.credentialVault.enable()
                    if let attempt = self.credentialAttempt, let script = self.credentialScript(attempt: attempt) {
                        self.loginView?.evaluateJavaScript(script)
                    }
                } catch { self.notice(CredentialFailure.unavailable.localizedDescription, type: "error") }
            }
            self.publishCredentialStatus()
        }
    }
    func forgetRememberedPassword() {
        guard !resettingAccount else { return }
        credentialAttempt?.close(); credentialAttempt = nil
        loginWindow?.close()
        auxiliaryWindows.forEach { $0.close() }; auxiliaryWindows.removeAll()
        do { try credentialVault.forget(); notice("已删除本机保存的密码并关闭记住密码。", type: "success") }
        catch { notice(CredentialFailure.unavailable.localizedDescription, type: "error") }
        publishCredentialStatus()
    }
    func credentialScript(attempt: LoginCredentialAttempt) -> String? {
        guard credentialVault.status()["enabled"] as? Bool == true,
              let file = Bundle.main.resourceURL?.appendingPathComponent("console/assets/remember-login.js"),
              let source = try? String(contentsOf: file, encoding: .utf8),
              let data = try? JSONSerialization.data(withJSONObject: ["platform": "mac", "nonce": attempt.nonce, "enabled": true]),
              let json = String(data: data, encoding: .utf8) else { return nil }
        return source.replacingOccurrences(of: "__POLYU_REMEMBER_CONFIG__", with: json)
    }
    func receiveCredentials(_ message: WKScriptMessage) {
        guard !resettingAccount, !terminating, message.webView === loginView, loginWindow?.isVisible == true,
              let view = loginView, let attempt = credentialAttempt, message.frameInfo.isMainFrame,
              LoginCredentialAttempt.schoolURL(message.frameInfo.request.url), let body = message.body as? [String: Any],
              body["nonce"] as? String == attempt.nonce, body["type"] as? String == "polyuRemember",
              attempt.receive(source: message.frameInfo.request.url, mainFrame: message.frameInfo.isMainFrame,
                  body: body, enabled: credentialVault.status()["enabled"] as? Bool == true) else { return }
        guard body["kind"] as? String == "ready", !attempt.filled else { return }
        do {
            guard let row = try credentialVault.record(), credentialOwner() == nil || credentialOwner() == row.verifiedUserId,
                  LoginCredentialAttempt.schoolURL(view.url), let document = attempt.documentId else { return }
            let value: [String: String] = ["nonce": attempt.nonce, "documentId": document, "username": row.username, "password": row.password]
            let data = try JSONSerialization.data(withJSONObject: value)
            guard let json = String(data: data, encoding: .utf8) else { return }
            let script = "(() => { if (window !== window.top || location.origin !== 'https://adfs.polyu.edu.hk' || location.pathname !== '/adfs/ls/') return; window.polyuRememberFill?.(" + json + "); })();"
            attempt.filled = true
            view.evaluateJavaScript(script) { [weak self] _, error in
                if error != nil { self?.notice("本机记住密码暂不可用，请在学校页面手动输入。") }
            }
        } catch { notice(CredentialFailure.unavailable.localizedDescription, type: "error") }
    }
    func saveLoginCredential(response: [String: Any], completion: @escaping () -> Void) {
        guard let attempt = credentialAttempt, attempt.active, let candidate = attempt.candidate,
              let id = response["verifiedUserId"] as? String, credentialVault.status()["enabled"] as? Bool == true else { completion(); return }
        let commit = { [weak self] in
            guard let self = self, self.credentialAttempt === attempt, attempt.active, !self.resettingAccount, !self.terminating else { completion(); return }
            do { try self.credentialVault.saveVerified(username: candidate.username, password: candidate.password, userId: id) }
            catch { self.alert("Canvas 已连接，但密码未能保存", CredentialFailure.unavailable.localizedDescription) }
            attempt.candidate = nil
            completion()
        }
        do {
            if let old = try credentialVault.record(), old.verifiedUserId != id {
                let dialog = NSAlert(); dialog.messageText = "替换本机记住的学校账号？"
                dialog.informativeText = "这次登录的是另一账号。是否用本次成功登录的账号密码替换已保存的密码？"
                dialog.addButton(withTitle: "替换已记住的密码"); dialog.addButton(withTitle: "保留原密码")
                dialog.beginSheetModal(for: window) { result in
                    if result == .alertFirstButtonReturn { commit() } else { attempt.candidate = nil; completion() }
                }
            } else { commit() }
        } catch { attempt.candidate = nil; alert("Canvas 已连接，但密码未能保存", CredentialFailure.unavailable.localizedDescription); completion() }
    }

    @objc func runtimeStateChanged(_ notification: Notification? = nil) {
        let value: [String: Bool] = ["hidden": window == nil || !window.isVisible || window.isMiniaturized || !window.occlusionState.contains(.visible),
                                    "lowPower": ProcessInfo.processInfo.isLowPowerModeEnabled]
        if let bytes = try? JSONSerialization.data(withJSONObject: value), let text = String(data: bytes, encoding: .utf8) {
            console?.evaluateJavaScript("window.polyuRuntimeState?.(" + text + ")")
        }
    }

    func windowWillClose(_ notification: Notification) {
        guard let closing = notification.object as? NSWindow else { return }
        if closing === loginWindow {
            credentialAttempt?.close(); credentialAttempt = nil
            loginView?.configuration.userContentController.removeScriptMessageHandler(forName: "canvasCredentials")
            if !loginCompleted && !resettingAccount { recordDiagnostic("login.close", "cancelled") }
            loginView?.evaluateJavaScript("window.polyuLoginStop?.()")
            loginView?.stopLoading()
            loginView?.configuration.userContentController.removeScriptMessageHandler(forName: "canvasLogin")
            loginView?.navigationDelegate = nil
            loginView?.uiDelegate = nil
            closing.contentView = nil
            loginView = nil; loginWindow = nil; loginAddress = nil; connecting = false
        } else if auxiliaryWindows.contains(where: { $0 === closing }) {
            let view = closing.contentView as? WKWebView
            view?.evaluateJavaScript("window.polyuLoginStop?.()")
            view?.configuration.userContentController.removeScriptMessageHandler(forName: "canvasLogin")
            view?.stopLoading(); view?.navigationDelegate = nil; view?.uiDelegate = nil
            closing.contentView = nil
            auxiliaryWindows.removeAll(where: { $0 === closing })
        }
    }

    func recordDiagnostic(_ event: String, _ result: String, _ code: String = "") {
        // Callers supply fixed identifiers only, never account/session data.
        diagnosticQueue.append(["event": event, "result": result, "code": code])
        if diagnosticQueue.count > 200 { diagnosticQueue.remove(at: diagnosticSending ? 1 : 0) }
        flushDiagnostics()
    }

    func flushDiagnostics() {
        guard !diagnosticSending, !resettingAccount, !terminating, let payload = diagnosticQueue.first,
              let url = baseURL?.appendingPathComponent("api/native/diagnostics") else { return }
        diagnosticSending = true
        let generation = accountGeneration
        var request = URLRequest(url: url, timeoutInterval: 5)
        request.httpMethod = "POST"
        request.setValue(nativeSecret, forHTTPHeaderField: "X-PolyU-Native")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        nativeSession.dataTask(with: request) { _, response, error in
            DispatchQueue.main.async {
                guard generation == self.accountGeneration else { return }
                self.diagnosticSending = false
                if error == nil, (response as? HTTPURLResponse)?.statusCode == 200 {
                    if !self.diagnosticQueue.isEmpty { self.diagnosticQueue.removeFirst() }
                    self.flushDiagnostics()
                    if self.diagnosticQueue.isEmpty {
                        self.console?.evaluateJavaScript("if (typeof pageVisible==='function' && pageVisible() && document.getElementById('settingsDialog')?.open && state.settingsTab==='logs') refreshLogs()")
                    }
                }
            }
        }.resume()
    }

    func postNative(_ operation: String, payload: [String: Any], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        guard let url = baseURL?.appendingPathComponent("api/native/" + operation) else { return }
        let generation = accountGeneration
        var request = URLRequest(url: url, timeoutInterval: 120)
        request.httpMethod = "POST"
        request.setValue(nativeSecret, forHTTPHeaderField: "X-PolyU-Native")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        nativeSession.dataTask(with: request) { data, response, error in
            let result: Result<[String: Any], Error>
            if let error = error { result = .failure(error) }
            else if let data = data, let dictionary = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
                if let error = dictionary["error"] as? String {
                    result = .failure(NSError(domain: "PolyU", code: (response as? HTTPURLResponse)?.statusCode ?? 0,
                                              userInfo: [NSLocalizedDescriptionKey: error]))
                } else { result = .success(dictionary) }
            } else { result = .failure(NSError(domain: "PolyU", code: 0, userInfo: [NSLocalizedDescriptionKey: "无法读取本机服务的响应，请重试。"])) }
            DispatchQueue.main.async {
                guard self.accountGeneration == generation, !self.resettingAccount, !self.terminating else { return }
                completion(result)
            }
        }.resume()
    }

    @objc func openDataFolder() {
        if testIsolation != nil { notice("隔离测试资料保存在临时目录。不会打开其他应用。"); return }
        NSWorkspace.shared.open(dataDirectory)
    }
    @objc func openFilesFolder() {
        if testIsolation != nil { notice("隔离测试资料保存在临时目录。不会打开其他应用。"); return }
        let folder = dataDirectory.appendingPathComponent("account/documents/files", isDirectory: true)
        NSWorkspace.shared.open(FileManager.default.fileExists(atPath: folder.path) ? folder : dataDirectory)
    }
    func loadConsole() {
        postNative("bootstrap", payload: [:]) { result in
            guard case .success(let value) = result, let ticket = value["ticket"] as? String,
                  let base = self.baseURL, var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
                self.alert("控制台授权失败", "请重新打开应用。"); return
            }
            components.queryItems = [URLQueryItem(name: "view", value: UUID().uuidString)]
            components.fragment = "bootstrap=" + ticket
            if let url = components.url { self.console.load(URLRequest(url: url)) }
        }
    }

    @objc func reloadConsole() {
        guard !resettingAccount, !terminating else { return }
        if baseURL != nil { loadConsole() } else { stopBackend(); startBackend() }
    }
    @objc func goHome() { if baseURL != nil { loadConsole() } }
    @objc func openSettings() { console.evaluateJavaScript("window.openSettings && window.openSettings()", completionHandler: nil) }
    func reloadData() { console.evaluateJavaScript("window.polyuReload && window.polyuReload()", completionHandler: nil) }
    func notice(_ message: String, type: String = "info") {
        let strings = try? JSONSerialization.data(withJSONObject: [message, type])
        if let strings = strings, let json = String(data: strings, encoding: .utf8) {
            console.evaluateJavaScript("window.polyuNativeNotice && window.polyuNativeNotice(...\(json))", completionHandler: nil)
        }
    }

    @objc func logout() {
        guard !resettingAccount, currentAlert == nil else { return }
        recordDiagnostic("logout", "attempt")
        let confirmation = NSAlert()
        confirmation.messageText = "退出登录并删除本账号的数据？"
        confirmation.informativeText = "这台 Mac 应用中的课程、成绩、考试安排、手动记录和登录会话会全部删除。再次登录后从 Canvas 重新采集。"
        let retain = NSButton(checkboxWithTitle: "在本机保留已记住的账号密码（仅限个人电脑）", target: nil, action: nil)
        retain.state = .off
        retain.isEnabled = (try? credentialVault.state().activeRef) != nil
        confirmation.accessoryView = retain
        confirmation.addButton(withTitle: "退出并删除")
        confirmation.addButton(withTitle: "取消")
        currentAlert = confirmation
        confirmation.beginSheetModal(for: window) { [weak self] response in
            self?.currentAlert = nil
            guard let self = self else { return }
            guard response == .alertFirstButtonReturn else { self.recordDiagnostic("logout", "cancelled"); return }
            let keep = retain.isEnabled && retain.state == .on
            self.pendingNotice = keep ? "已退出登录，学习资料和会话已删除；本机已记住的密码保留。" : "已退出登录，学习资料、会话和已记住的密码已删除。"
            self.resetAccountStorage(message: "正在退出登录并删除账号数据…", logoutRequested: true, keepRememberedPassword: keep)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // Native authorization must never leave the selected loopback endpoint.
        completionHandler(nil)
    }

    func clearLoginStores(logoutRequested: Bool, completion: @escaping () -> Void) {
        schoolDataStore().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) {
            if logoutRequested && self.testIsolation == nil {
                // Earlier releases used a persistent WebKit store; erase it only on explicit logout.
                WKWebsiteDataStore.default().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
                                                        modifiedSince: .distantPast, completionHandler: completion)
            } else { completion() }
        }
    }

    func clearSavedSession() throws {
        guard testIsolation == nil else { return }
        let file = dataDirectory.appendingPathComponent("account/browser/canvas-cookies.json").standardizedFileURL
        guard FileManager.default.fileExists(atPath: file.path) else { return }
        guard let helper = Bundle.main.resourceURL?.appendingPathComponent("read-tools/SessionVault") else {
            throw NSError(domain: "PolyU", code: 1)
        }
        let account = SHA256.hash(data: Data(file.path.utf8)).map { String(format: "%02x", $0) }.joined()
        let process = Process()
        process.executableURL = helper
        process.arguments = ["delete", account]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw NSError(domain: "PolyU", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "Canvas 钥匙串会话未能清除，账号资料保留且清理标记仍有效。请检查钥匙串授权后重启以继续退出。"])
        }
    }

    func resetAccountStorage(message: String, logoutRequested: Bool = false, keepRememberedPassword: Bool? = nil) {
        guard !resettingAccount else { return }
        let keep = keepRememberedPassword ?? CredentialVault.logoutPolicy(dataDirectory)
        do {
            try testIsolation?.validate()
            let manager = FileManager.default
            if (try? dataDirectory.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
                throw NSError(domain: "PolyUStudyConsole", code: 1, userInfo: [NSLocalizedDescriptionKey: "账号目录为链接，未开始清理。"])
            }
            try manager.createDirectory(at: dataDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let policy: [String: Any] = ["version": 2, "keepRememberedPassword": keep]
            try JSONSerialization.data(withJSONObject: policy).write(to: dataDirectory.appendingPathComponent("logout-pending"), options: .atomic)
        } catch {
            if logoutRequested { recordDiagnostic("logout.cleanup", "failure", "IO_ERROR") }
            showStartup("账号清理未开始")
            alert("无法记录退出清理", error.localizedDescription)
            return
        }
        resettingAccount = true
        credentialAttempt?.close(); credentialAttempt = nil
        accountGeneration = UUID()
        diagnosticQueue.removeAll()
        diagnosticSending = false
        if logoutRequested {
            diagnosticQueue.append(["event": "logout", "result": "attempt", "code": ""])
            diagnosticQueue.append(["event": "logout.cleanup", "result": "attempt", "code": ""])
        }
        connecting = false
        nativeSession.invalidateAndCancel()
        let child = backend
        stopBackend()
        baseURL = nil
        loginView?.stopLoading()
        loginView?.configuration.userContentController.removeScriptMessageHandler(forName: "canvasLogin")
        loginWindow?.close()
        loginWindow?.contentView = nil
        loginWindow = nil
        loginView = nil
        loginAddress = nil
        auxiliaryWindows.forEach {
            ($0.contentView as? WKWebView)?.stopLoading()
            $0.close()
            $0.contentView = nil
        }
        auxiliaryWindows.removeAll()
        makeConsole()
        showStartup(message)
        // Stop the sync worker before erasing its account data.
        DispatchQueue.global(qos: .userInitiated).async {
            if let child = child, child.processIdentifier > 0 {
                let deadline = Date().addingTimeInterval(2)
                while child.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
                if child.isRunning { kill(-child.processIdentifier, SIGKILL); child.terminate() }
                child.waitUntilExit()
            }
            do {
                if !keep { try self.credentialVault.forget() }
                try self.clearSavedSession()
            }
            catch {
                DispatchQueue.main.async {
                    self.resettingAccount = false
                    self.showStartup("钥匙串清理未完成")
                    self.alert("退出清理未完成", error.localizedDescription)
                }
                return
            }
            DispatchQueue.main.async {
                guard !self.terminating else { return }
                self.clearLoginStores(logoutRequested: logoutRequested) {
                    do {
                        let manager = FileManager.default
                        let marker = self.dataDirectory.appendingPathComponent("logout-pending")
                        // Keep recovery blocked until all account files are gone.
                        for item in try manager.contentsOfDirectory(at: self.dataDirectory, includingPropertiesForKeys: nil) {
                            if item.lastPathComponent != "logout-pending" && !(keep && item.lastPathComponent == "credentials") { try manager.removeItem(at: item) }
                        }
                        try manager.removeItem(at: marker)
                        if !keep && manager.fileExists(atPath: self.dataDirectory.path) { try manager.removeItem(at: self.dataDirectory) }
                        try manager.createDirectory(at: self.dataDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                        try Data("3\n".utf8).write(to: self.dataDirectory.appendingPathComponent("framework-version"), options: .atomic)
                        let configuration = URLSessionConfiguration.ephemeral
                        if self.testIsolation != nil { configuration.connectionProxyDictionary = [:] }
                        self.nativeSession = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
                        self.resettingAccount = false
                        if logoutRequested {
                            self.recordDiagnostic("logout.cleanup", "success")
                            self.recordDiagnostic("logout", "success")
                        }
                        self.startBackend()
                    } catch {
                        if FileManager.default.fileExists(atPath: self.dataDirectory.path) {
                            if let policy = try? JSONSerialization.data(withJSONObject: ["version": 2, "keepRememberedPassword": keep]) {
                                try? policy.write(to: self.dataDirectory.appendingPathComponent("logout-pending"), options: .atomic)
                            }
                        }
                        self.resettingAccount = false
                        if logoutRequested { self.recordDiagnostic("logout.cleanup", "failure", "IO_ERROR") }
                        self.showStartup("账号清理未完成")
                        self.alert("未能完成数据删除", "请关闭应用后重新打开以继续清理。\n" + error.localizedDescription)
                    }
                }
            }
        }
    }

    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: applicationName, .applicationVersion: applicationVersion,
            .credits: NSAttributedString(string: "个人学习控制台 · Canvas 课程与文件只读同步\n标识素材来自香港理工大学官网\n学生制作的学习工具，非学校官方应用")])
    }
    func alert(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "知道了")
        currentAlert = alert
        alert.beginSheetModal(for: window) { [weak self] _ in self?.currentAlert = nil }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if webView === loginView { credentialAttempt?.candidate = nil }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if webView === loginView { credentialAttempt?.candidate = nil }
    }
    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        if webView === loginView, let url = webView.url {
            loginAddress?.stringValue = "当前登录网站：\(url.host ?? "") · 在学校页面完成登录和验证"
        }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === console, webView.url?.host == "127.0.0.1" {
            runtimeStateChanged()
            publishCredentialStatus()
            acknowledgeUpdateWhenReady()
            if let text = pendingNotice {
                pendingNotice = nil
                notice(text, type: "success")
            }
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if testIsolation != nil {
            let url = action.request.url
            let local = url?.scheme == "http" && url?.host == "127.0.0.1" && url?.port == baseURL?.port
            decisionHandler(local || url?.scheme == "about" || url?.scheme == "data" ? .allow : .cancel)
            return
        }
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if webView === console {
            if url.scheme == "about" || (url.scheme == "http" && url.host == "127.0.0.1" && url.port == baseURL?.port) {
                decisionHandler(.allow)
            } else if ["https", "mailto"].contains(url.scheme ?? "") {
                NSWorkspace.shared.open(url); decisionHandler(.cancel)
            } else { decisionHandler(.cancel) }
        } else { decisionHandler(url.scheme == "https" || url.scheme == "about" ? .allow : .cancel) }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if testIsolation != nil { return nil }
        if webView === console {
            if let url = action.request.url {
                if url.scheme == "https" || url.scheme == "mailto" { NSWorkspace.shared.open(url) }
            }
            return nil
        }
        let handlers = WKUserContentController()
        configuration.userContentController.userScripts.filter { !$0.source.contains("polyuRemember") }.forEach { handlers.addUserScript($0) }
        handlers.add(self, name:"canvasLogin")
        configuration.userContentController = handlers
        let popup = WKWebView(frame: .zero, configuration: configuration)
        popup.navigationDelegate = self
        popup.uiDelegate = self
        let popupWindow = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 850, height: 720), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        popupWindow.delegate = self
        popupWindow.title = "学校身份验证"
        popupWindow.isReleasedWhenClosed = false
        popupWindow.contentView = popup
        auxiliaryWindows.append(popupWindow)
        popupWindow.center(); popupWindow.makeKeyAndOrderFront(nil)
        return popup
    }
    func webViewDidClose(_ webView: WKWebView) {
        auxiliaryWindows.first(where: { $0.contentView === webView })?.close()
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: webView.window ?? window) { completionHandler($0 == .alertFirstButtonReturn) }
    }
}

@main
struct PolyUApplication {
    static func main() {
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--update-watchdog" {
            exit(UpdateRecovery.watch(CommandLine.arguments[2]))
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
        _ = delegate
    }
}
