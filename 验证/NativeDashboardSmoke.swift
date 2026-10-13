// GUI component verifier only. Never launches the production AppDelegate or a school page.
import AppKit
import WebKit

final class SmokeDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    let address: URL
    let output: URL
    var window: NSWindow!
    var view: WKWebView!
    var timer: Timer?
    var checking = false
    var finished = false
    var actions: [String] = []
    var findings: [String: Any] = ["scope": "candidate dashboard component in isolated AppKit/WKWebView; production AppDelegate not launched",
        "productionNativeLifecycleValidated": false, "guiLoginValidated": false, "realCanvasValidated": false]
    let started = Date()

    init(address: URL, output: URL) { self.address = address; self.output = output }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "polyu")
        configuration.userContentController.addUserScript(WKUserScript(source: """
            window.polyuSmokeErrors=[];
            window.addEventListener('error', e=>window.polyuSmokeErrors.push(String(e.message)));
            window.addEventListener('unhandledrejection', e=>window.polyuSmokeErrors.push(String(e.reason)));
            """, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1250, height: 850),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "PolyU 1.8.2 · 隔离 GUI 组件检查（无真实账号）"
        window.contentView = view
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        let rules = """
            [{"trigger":{"url-filter":"^https?://","unless-domain":["127.0.0.1"]},"action":{"type":"block"}}]
            """
        let ruleDirectory = output.appendingPathComponent("content-rules", isDirectory: true)
        do { try FileManager.default.createDirectory(at: ruleDirectory, withIntermediateDirectories: true) }
        catch { fail("Test rule directory unavailable: \(error)"); return }
        WKContentRuleListStore(url: ruleDirectory).compileContentRuleList(forIdentifier: "polyu-loopback-only", encodedContentRuleList: rules) { list, error in
            guard let list = list else { self.fail("Loopback rule could not be compiled: \(String(describing: error))"); return }
            configuration.userContentController.add(list)
            self.findings["loopbackContentRuleInstalled"] = true
            self.findings["nonPersistentWebKit"] = !configuration.websiteDataStore.isPersistent
            self.view.load(URLRequest(url: self.address))
            self.timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { _ in self.waitForInitialView() }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = action.request.url
        let allowed = url?.scheme == "http" && url?.host == "127.0.0.1" && url?.port == address.port
        if !allowed { findings["nonLoopbackNavigationBlocked"] = true }
        decisionHandler(allowed ? .allow : .cancel)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.webView === view, message.frameInfo.isMainFrame,
              message.frameInfo.request.url?.host == "127.0.0.1",
              message.frameInfo.request.url?.port == address.port,
              let body = message.body as? [String: Any], let action = body["action"] as? String else {
            fail("Unexpected bridge origin"); return
        }
        actions.append(action)
        // Record the product button's bridge action. No production native action is invoked.
    }

    func waitForInitialView() {
        guard !checking, !finished else { return }
        if Date().timeIntervalSince(started) > 45 { fail("Dashboard did not render within 45 seconds"); return }
        checking = true
        view.evaluateJavaScript("""
            (()=>({ready:document.getElementById('metricCourses')?.textContent==='0',
              version:document.getElementById('appVersion')?.textContent,
              accountPanelVisible:document.getElementById('accountSection')?.hidden===false,
              settingsOpen:document.getElementById('settingsDialog')?.open,
              navigationEntries:document.querySelectorAll('.side-nav a').length,
              loginText:document.getElementById('loginButton')?.textContent,
              logoutHidden:document.getElementById('logoutButton')?.hidden,
              courseCards:document.querySelectorAll('.course-card').length,
              errors:window.polyuSmokeErrors || []}))()
            """) { result, _ in
            self.checking = false
            guard let data = result as? [String: Any], data["ready"] as? Bool == true else { return }
            self.timer?.invalidate()
            guard data["version"] as? String == "v1.8.2", data["accountPanelVisible"] as? Bool == false,
                  data["settingsOpen"] as? Bool == false, data["navigationEntries"] as? Int == 4,
                  data["loginText"] as? String == "登录并授权 Canvas", data["logoutHidden"] as? Bool == true,
                  data["courseCards"] as? Int == 0, (data["errors"] as? [String] ?? ["unknown"]).isEmpty else {
                self.fail("Initial empty view did not match: \(data)"); return
            }
            self.findings["initialEmptyView"] = data
            self.findings["nativeTestWindowVisible"] = self.window.isVisible
            self.snapshot("initial-empty.png") { self.checkSearch() }
        }
    }

    func checkSearch() {
        view.evaluateJavaScript("""
            (()=>{
              window.openSettings('account');
              document.getElementById('loginButton').click();
              window.openSettings('documents');
              document.querySelector('[data-preserve="document-ledger"]').open=true;
              document.getElementById('documents').scrollIntoView();
              const input=document.getElementById('documentSearch'); input.value='synthetic deadline';
              input.dispatchEvent(new Event('input',{bubbles:true}));
              return true;
            })()
            """) { _, error in
            if let error = error { self.fail("Search input failed: \(error)"); return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                self.view.evaluateJavaScript("""
                    (()=>({query:document.getElementById('documentSearch').value,
                      list:document.getElementById('documentList').textContent,
                      matches:document.getElementById('documentMatches').textContent,
                      notice:document.getElementById('notice').textContent}))()
                    """) { result, error in
                    guard error == nil, let data = result as? [String: Any],
                          data["query"] as? String == "synthetic deadline",
                          (data["list"] as? String ?? "").contains("暂无符合筛选条件的资料"),
                          data["matches"] as? String == "", self.actions.contains("login") else {
                        self.fail("Unauthenticated empty search/bridge failed: \(String(describing: result))"); return
                    }
                    self.findings["emptySearch"] = data
                    self.findings["loginButtonRoutedOnlyToTestBridge"] = true
                    self.snapshot("search-empty.png") { self.checkErrorAndReturn() }
                }
            }
        }
    }

    func checkErrorAndReturn() {
        view.evaluateJavaScript("""
            (()=>{
              window.polyuNativeNotice('合成错误提示：尚未登录 Canvas','error');
              return {visible:!document.getElementById('notice').hidden,
                errorClass:document.getElementById('notice').classList.contains('error'),
                text:document.getElementById('notice').textContent};
            })()
            """) { result, error in
            guard error == nil, let data = result as? [String: Any],
                  data["visible"] as? Bool == true, data["errorClass"] as? Bool == true,
                  (data["text"] as? String ?? "").contains("合成错误提示") else {
                self.fail("Error notice failed"); return
            }
            self.findings["syntheticNativeErrorNotice"] = data
            self.view.evaluateJavaScript("""
                (()=>{
                  // Only a synthetic frontend connection is displayed. The backend remains unauthenticated.
                  const temporary=structuredClone(state.data);
                  temporary.connection.authorized=true; temporary.connection.needsLogin=false;
                  temporary.connection.name='Synthetic GUI fixture'; renderConnection(temporary);
                  window.openSettings('account');
                  document.getElementById('logoutButton').click();
                  document.getElementById('settingsDialog').close();
                  window.polyuReload();
                  location.hash='#top'; window.scrollTo(0,0);
                  return true;
                })()
                """) { _, error in
                if let error = error { self.fail("Synthetic logout/return failed: \(error)"); return }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.75) {
                    self.view.evaluateJavaScript("""
                        (()=>({logoutHidden:document.getElementById('logoutButton').hidden,
                          accountTitle:document.getElementById('accountTitle').textContent,
                          query:document.getElementById('documentSearch').value,
                          matches:document.getElementById('documentMatches').textContent,
                          settingsOpen:document.getElementById('settingsDialog').open,
                          hash:location.hash,scrollY:window.scrollY,errors:window.polyuSmokeErrors}))()
                        """) { result, error in
                        guard error == nil, let data = result as? [String: Any],
                              data["logoutHidden"] as? Bool == true, data["query"] as? String == "",
                              data["matches"] as? String == "", data["hash"] as? String == "#top",
                              data["settingsOpen"] as? Bool == false,
                              (data["errors"] as? [String] ?? ["unknown"]).isEmpty,
                              self.actions.contains("logout") else {
                            self.fail("Return to empty frontend failed: \(String(describing: result))"); return
                        }
                        self.findings["returnToEmptyFrontend"] = data
                        self.findings["logoutActionRoutedOnlyToTestBridge"] = true
                        self.snapshot("return-empty.png") { self.finish(ok: true) }
                    }
                }
            }
        }
    }

    func snapshot(_ name: String, completion: @escaping () -> Void) {
        let configuration = WKSnapshotConfiguration()
        configuration.afterScreenUpdates = true
        view.takeSnapshot(with: configuration) { image, error in
            guard error == nil, let tiff = image?.tiffRepresentation,
                  let bitmap = NSBitmapImageRep(data: tiff), let png = bitmap.representation(using: .png, properties: [:]) else {
                self.fail("Owned webview snapshot failed: \(String(describing: error))"); return
            }
            do { try png.write(to: self.output.appendingPathComponent(name)); completion() }
            catch { self.fail("Snapshot write failed: \(error)") }
        }
    }

    func fail(_ message: String) { findings["error"] = message; finish(ok: false) }

    func finish(ok: Bool) {
        guard !finished else { return }
        finished = true
        timer?.invalidate()
        findings["ok"] = ok
        findings["bridgeActions"] = actions
        findings["schoolLoginWindowOpened"] = false
        findings["windowWasVisible"] = window?.isVisible == true
        view?.stopLoading()
        view?.configuration.userContentController.removeScriptMessageHandler(forName: "polyu")
        window?.contentView = nil
        window?.close()
        findings["testWindowClosed"] = window?.isVisible == false
        do {
            let data = try JSONSerialization.data(withJSONObject: findings, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: output.appendingPathComponent("component-evidence.json"))
            FileHandle.standardOutput.write(data)
        } catch { FileHandle.standardError.write(Data("Evidence write failed: \(error)\n".utf8)) }
        NSApp.terminate(nil)
    }
}

guard CommandLine.arguments.count == 3, let address = URL(string: CommandLine.arguments[1]),
      address.scheme == "http", address.host == "127.0.0.1" else { fatalError("Only owned loopback URL accepted") }
let output = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
let app = NSApplication.shared
let delegate = SmokeDelegate(address: address, output: output)
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
