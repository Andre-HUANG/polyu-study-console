// Opt-in, offline checks of the production delegate. Only validated temporary data is used.
import AppKit
import WebKit
import Darwin

final class NativeGUISmoke {
    unowned let app: AppDelegate
    let isolation: TestIsolation
    let phase: String
    var done = false
    var report: [String: Any] = [:]
    var sentinels: [URL] = []

    init(app: AppDelegate, isolation: TestIsolation) {
        self.app = app; self.isolation = isolation; phase = isolation.phase!
    }

    func start() {
        report = ["phase": phase, "nativePID": getpid(), "candidateBundle": Bundle.main.bundleURL.path,
                  "version": applicationVersion, "dataRoot": isolation.data.path,
                  "productionNativeAppLaunched": true, "realAccountDirectoryAccessed": false,
                  "realCanvasValidated": false, "guiLoginValidated": false,
                  "persistentLoginStoreValidated": false]
        progress("waiting for production dashboard")
        waitForEmpty { self.initialChecks() }
    }

    func progress(_ message: String) {
        try? Data(message.utf8).write(to: isolation.control.appendingPathComponent("progress-" + phase + ".txt"), options: .atomic)
    }

    func later(_ action: @escaping () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { if !self.done { action() } }
    }

    func wait(_ label: String, until condition: @escaping () -> Bool, then action: @escaping () -> Void) {
        let deadline = Date().addingTimeInterval(35)
        func poll() {
            guard !done else { return }
            if condition() { action() }
            else if Date() > deadline { fail("Timeout: " + label) }
            else { later { poll() } }
        }
        poll()
    }

    func js(_ script: String, then action: @escaping (Any?) -> Void) {
        app.console.evaluateJavaScript(script) { value, error in
            guard !self.done else { return }
            if let error = error { self.fail("JavaScript: " + error.localizedDescription) }
            else { action(value) }
        }
    }

    func waitForEmpty(_ action: @escaping () -> Void) {
        let deadline = Date().addingTimeInterval(40)
        func poll() {
            guard !done else { return }
            app.console.evaluateJavaScript("""
                (()=>({ready:document.getElementById('metricCourses')?.textContent==='0',
                version:document.getElementById('appVersion')?.textContent,
                panel:document.getElementById('accountSection')?.hidden===true,
                settingsClosed:document.getElementById('settingsDialog')?.open===false,
                navigationCount:document.querySelectorAll('.side-nav a').length,
                logoutHidden:document.getElementById('logoutButton')?.hidden===true,
                courseCards:document.querySelectorAll('.course-card').length}))()
                """) { value, _ in
                guard !self.done else { return }
                if let value = value as? [String: Any], value["ready"] as? Bool == true,
                   value["version"] as? String == "v1.8.2", value["panel"] as? Bool == true,
                   value["logoutHidden"] as? Bool == true, value["courseCards"] as? Int == 0,
                   value["settingsClosed"] as? Bool == true, value["navigationCount"] as? Int == 4,
                   !self.app.resettingAccount, !self.app.console.isLoading {
                    self.report["emptyDashboard"] = value
                    action()
                } else if Date() > deadline { self.fail("Production dashboard did not become empty and ready") }
                else { self.later { poll() } }
            }
        }
        poll()
    }

    func menu(_ title: String) -> Bool {
        for root in NSApp.mainMenu?.items ?? [] {
            if let menu = root.submenu, let index = menu.items.firstIndex(where: { $0.title == title }) {
                menu.performActionForItem(at: index); return true
            }
        }
        fail("Missing native menu: " + title); return false
    }

    func snapshot(_ name: String, then action: @escaping () -> Void) {
        app.console.takeSnapshot(with: nil) { image, error in
            guard !self.done else { return }
            guard error == nil, let image = image, let tiff = image.tiffRepresentation,
                  let bitmap = NSBitmapImageRep(data: tiff), let bytes = bitmap.representation(using: .png, properties: [:]) else {
                self.fail("Own-window snapshot failed: \(String(describing: error))"); return
            }
            do { try bytes.write(to: self.isolation.control.appendingPathComponent(self.phase + "-" + name + ".png")) }
            catch { self.fail("Snapshot write failed: \(error)"); return }
            action()
        }
    }

    func initialChecks() {
        progress("native menus, About, bridge and empty search")
        guard app.window.isVisible, !app.console.configuration.websiteDataStore.isPersistent,
              !app.schoolDataStore().isPersistent, app.dataDirectory == isolation.data,
              app.testRule != nil else { fail("Native isolation/window precondition failed"); return }
        report["nativeWindowVisible"] = true
        report["nonPersistentConsoleAndLoginStores"] = true
        report["loopbackOnlyContentRule"] = true
        snapshot("initial-empty") {
            guard self.menu("关于 " + applicationName) else { return }
            self.later {
                let panels = NSApp.windows.filter { $0.isVisible && $0 !== self.app.window }
                func strings(_ view: NSView) -> [String] {
                    (view as? NSTextField).map { [$0.stringValue] } ?? view.subviews.flatMap { strings($0) }
                }
                let text = panels.flatMap { $0.contentView.map { strings($0) } ?? [] }.joined(separator: "\n")
                guard text.contains("1.8.2") else { self.fail("Native About panel did not show 1.8.2: " + text); return }
                self.report["nativeAboutVersion"] = text
                panels.forEach { $0.close() }
                self.settingsChecks {
                  self.js("openSettings('account');document.getElementById('loginButton').click();true") { _ in
                    self.wait("isolated login sheet", until: { self.app.currentAlert?.messageText == "隔离测试模式" }) {
                        self.report["productionLoginBridgeReached"] = true
                        self.app.currentAlert!.buttons[0].performClick(nil)
                        self.wait("login sheet dismissal", until: { self.app.currentAlert == nil }) { self.searchAndError() }
                    }
                  }
                }
            }
        }
    }


    func waitForJS(_ label: String, _ expression: String, then action: @escaping () -> Void) {
        let deadline = Date().addingTimeInterval(15)
        func poll() {
            guard !done else { return }
            js(expression) { value in
                if value as? Bool == true { action() }
                else if Date() > deadline { self.fail("UI timeout: " + label) }
                else { self.later { poll() } }
            }
        }
        poll()
    }

    func settingsChecks(_ action: @escaping () -> Void) {
        progress("settings open/repeated open/close/focus, course entry and narrow layout")
        js("""
            (()=>{const d=document.getElementById('settingsDialog');return !d.open &&
            ['accountSection','documents','courses'].every(id=>d.contains(document.getElementById(id)) && document.getElementById(id).hidden) &&
            d.contains(document.getElementById('softwareUpdateButton')) &&
            (()=>{const a=document.querySelector('.overview-summary').getBoundingClientRect(),b=document.getElementById('timetablePanel').getBoundingClientRect(),c=document.querySelector('.academic-calendar').getBoundingClientRect(),r=document.querySelector('.recent-row').getBoundingClientRect();
              return Math.abs(a.top-b.top)<2 && b.left>=a.right && Math.abs(c.top-a.top)<2 && r.top>=Math.max(a.bottom,b.bottom,c.bottom)})() &&
            document.querySelectorAll('.side-nav a').length===4 && !document.querySelector('.sidebar #appVersion')})()
            """) { value in
            guard value as? Bool == true else { self.fail("Settings-only modules or four-link navigation failed"); return }
            self.report["modulesHiddenUntilSettingsOpens"] = true
            self.js("document.getElementById('settingsButton').click();document.getElementById('settingsButton').click();document.getElementById('settingsDialog').open && !document.getElementById('accountSection').hidden && document.getElementById('documents').hidden") { value in
                guard value as? Bool == true else { self.fail("Repeated settings click did not stay open"); return }
                self.report["settingsRepeatedOpenStable"] = true
                self.snapshot("settings-account") {
                    self.js("document.getElementById('closeSettingsButton').click();true") { _ in
                        self.waitForJS("settings close/focus", "!document.getElementById('settingsDialog').open && document.activeElement.id==='settingsButton' && !document.body.classList.contains('settings-open')") {
                            self.report["settingsCloseReturnsFocus"] = true
                            self.js("document.getElementById('coursesNav').click();document.getElementById('settingsDialog').open && !document.getElementById('courses').hidden && document.getElementById('accountSection').hidden && document.getElementById('settings-courses-tab').getAttribute('aria-selected')==='true'") { value in
                                guard value as? Bool == true else { self.fail("Course overview did not open its Settings tab"); return }
                                self.report["courseNavigationOpensSettings"] = true
                                self.snapshot("settings-courses") {
                                    self.app.window.makeKeyAndOrderFront(nil)
                                    self.app.window.makeFirstResponder(self.app.console)
                                    guard let escape = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: self.app.window.windowNumber, context: nil, characters: "\u{1b}", charactersIgnoringModifiers: "\u{1b}", isARepeat: false, keyCode: 53) else { self.fail("Could not create own-window Escape"); return }
                                    self.app.window.sendEvent(escape)
                                    self.waitForJS("native Escape closes settings", "!document.getElementById('settingsDialog').open") {
                                        self.report["nativeEscapeClosesSettings"] = true
                                        self.narrowSettings { self.syntheticSchedule(action) }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    func narrowSettings(_ action: @escaping () -> Void) {
        app.window.setContentSize(NSSize(width: 860, height: 660))
        js("openSettings('documents');true") { _ in
            self.later {
                self.js("""
                    (()=>{const d=document.getElementById('settingsDialog'),r=d.getBoundingClientRect(),b=document.querySelector('.settings-body');
                    return d.open && !document.getElementById('documents').hidden && r.left>=8 && r.right<=innerWidth-8 && r.bottom<=innerHeight && b.scrollWidth<=b.clientWidth+1})()
                    """) { value in
                    guard value as? Bool == true else { self.fail("Narrow Settings overflows the native viewport"); return }
                    self.report["narrowSettingsFitsNativeViewport"] = true
                    self.snapshot("settings-narrow-documents") {
                        self.js("document.getElementById('closeSettingsButton').click();true") { _ in
                            self.app.window.setContentSize(NSSize(width: 1360, height: 900))
                            self.later(action)
                        }
                    }
                }
            }
        }
    }

    func syntheticSchedule(_ action: @escaping () -> Void) {
        progress("production renderer: synthetic final filter, unpublished date and hidden evidence details")
        js("""
            (()=>{window._smokeOriginalReload=window.polyuReload;window._smokeOriginalData=state.data;
            const course={id:'1',code:'GUI_TEST',name:'合成界面测试课程'};
            const sources=Array.from({length:14},(_,i)=>({documentId:'synthetic-'+i,title:'Synthetic notice '+i,url:'https://canvas.polyu.edu.hk/courses/1/pages/synthetic',page:1,quote:'Synthetic original excerpt '+i}));
            const base={course:course.code,date:'2026-12-15',daysLeft:69,url:'https://canvas.polyu.edu.hk/courses/1/pages/synthetic',editable:true,weight:'30%',sourceLabel:'Canvas 课程文件 · 自动识别',warnings:[],sources:[]};
            window._smokeData={...state.data,courseCount:1,courses:[course],
            events:[{...base,id:'synthetic-final',title:'合成期末考试',rawTitle:'合成期末考试',kind:'final',time:'14:00',timeText:'14:00–16:00',location:'ZS301',sources,warnings:[historicalNotice,'地点信息需要核对']},
            {...base,id:'synthetic-midterm',title:'合成期中考试',rawTitle:'合成期中考试',kind:'midterm'},
            {...base,id:'synthetic-project',title:'Final project',rawTitle:'Final project',kind:'assignment'}],
            undated:[{id:'synthetic-unpublished',course:course.code,title:'期末考试日期未公布',kind:'final',reason:'尚未公布日期',sources:[]},
            {id:'synthetic-undated-project',course:course.code,title:'Project date pending',kind:'assignment',sources:[]}]};
            window.polyuReload=async()=>render(window._smokeData);render(window._smokeData);
            document.querySelector('[data-filter="final"]').click();
            const list=document.getElementById('eventList'),details=list.querySelector('details.evidence-details');
            return {checks:{oneRow:list.querySelectorAll('.event-row').length===1,finalBadge:list.querySelector('.kind-final')?.textContent==='期末考试',
            collapsed:!!details && !details.open,summary:details?.querySelector('summary').textContent==='查看详情',
            sourceQuiet:!list.innerText.includes('核对来源') && !list.innerText.includes('自动识别'),historyQuiet:!list.innerText.includes(historicalNotice),
            conflictVisible:list.innerText.includes('地点信息需要核对'),timeVisible:list.innerText.includes('14:00'),venueVisible:list.innerText.includes('ZS301'),
            onePending:document.getElementById('undatedCount').textContent==='1',unpublished:document.getElementById('undatedList').textContent.includes('尚未公布日期')},
            visibleText:list.innerText,pendingText:document.getElementById('undatedList').innerText}})()
            """) { value in
            guard let result = value as? [String: Any], let checks = result["checks"] as? [String: Bool], checks.values.allSatisfy({$0}) else {
                self.report["syntheticDisplayChecks"] = value
                self.snapshot("synthetic-display-failed") { self.fail("Final filter or concise event/evidence display failed") }; return
            }
            self.report["syntheticDisplayChecks"] = result
            self.report["syntheticFinalFilterAndUnpublishedStatus"] = true
            self.report["ordinarySourceMetadataCollapsedAndConflictVisible"] = true
            self.js("document.getElementById('schedule').scrollIntoView();true") { _ in
                self.later {
                    self.snapshot("synthetic-final-filter") {
                        self.js("""
                            (()=>{const d=document.querySelector('#eventList details.evidence-details');d.open=true;
                            const retained=d.textContent.includes('Synthetic original excerpt 13') && d.textContent.includes(historicalNotice) && d.textContent.includes('30%');
                            openEventDialog();const select=document.querySelector('#eventForm select[name="kind"]');select.value='final';
                            const selected=select.value==='final';document.getElementById('eventDialog').close();return retained && selected})()
                            """) { value in
                            guard value as? Bool == true else { self.fail("Source excerpts/weights or personal final option lost"); return }
                            self.report["sourceExcerptsAndWeightsRetainedOnDemand"] = true
                            self.report["personalFinalCategoryAvailable"] = true
                            self.js("window.polyuReload=window._smokeOriginalReload;render(window._smokeOriginalData);document.querySelector('[data-filter=\"all\"]').click();window._smokeData=null;window._smokeOriginalData=null;true") { _ in action() }
                        }
                    }
                }
            }
        }
    }

    func searchAndError() {
        js("""
            (()=>{selectSettingsTab('documents');document.querySelector('[data-preserve="document-ledger"]').open=true;
            document.getElementById('documents').scrollIntoView();
            const input=document.getElementById('documentSearch');input.value='synthetic deadline';
            input.dispatchEvent(new Event('input',{bubbles:true}));
            return {query:input.value,list:document.getElementById('documentList').textContent}})()
            """) { result in
                    // Filtering is synchronous. Inspect the same JS turn so the
                    // anonymous-account refresh cannot clear the synthetic input.
                    guard let result = result as? [String: Any], result["query"] as? String == "synthetic deadline",
                          (result["list"] as? String ?? "").contains("暂无符合筛选条件的资料") else { self.fail("Empty native search failed"); return }
                    self.report["emptySearch"] = result
                    self.app.postNative("session", payload: ["cookies": []]) { result in
                        guard case .failure(let error) = result else { self.fail("Empty-cookie session should fail locally"); return }
                        self.app.notice(error.localizedDescription, type: "error")
                        self.later {
                            self.js("({visible:!document.getElementById('notice').hidden,error:document.getElementById('notice').classList.contains('error'),text:document.getElementById('notice').textContent})") { result in
                                guard let result = result as? [String: Any], result["visible"] as? Bool == true,
                                      result["error"] as? Bool == true, (result["text"] as? String ?? "").contains("尚未完成学校登录") else { self.fail("Native error notice failed"); return }
                                self.report["nativeLocalErrorVisible"] = result
                                self.snapshot("native-error") {
                                  self.checkDiagnosticView {
                                    guard self.menu("返回学习总览") else { return }
                                    self.waitForEmpty {
                                        self.js("document.getElementById('documentSearch').value") { value in
                                            guard value as? String == "" else { self.fail("Return-home did not clear search"); return }
                                            self.report["nativeReturnHome"] = true
                                            if self.phase == "logout" { self.checkLogout() }
                                            else if self.phase == "first" { self.checkBackendRecovery() }
                                            else { self.finish(true) }
                                        }
                                    }
                                  }
                                }
                            }
                        }
                    }
        }
    }


    func checkDiagnosticView(_ action: @escaping () -> Void) {
        progress("settings logs: native login attempt, local verification failure, filter and clear")
        js("selectSettingsTab('logs');true") { _ in
            self.waitForJS("login failure logs", "state.logs?.events.some(e=>e.event==='login.open' && e.result==='failure' && e.code==='TEST_MODE') && state.logs?.events.some(e=>e.event==='login.verify' && e.result==='failure' && e.code==='INVALID_INPUT')") {
                self.report["nativeLoginAttemptsAndLocalFailureLogged"] = true
                self.snapshot("settings-logs") {
                    self.js("const f=document.getElementById('logFilter');f.value='problems';f.dispatchEvent(new Event('change'));document.querySelectorAll('#logList .log-row:not(.log-failure):not(.log-partial)').length===0 && document.querySelectorAll('#logList .log-failure').length>=2") { value in
                        guard value as? Bool == true else { self.fail("Problem log filter failed"); return }
                        self.report["diagnosticProblemFilter"] = true
                        self.js("document.getElementById('clearLogsButton').click();true") { _ in
                            self.waitForJS("logs clear", "state.logs?.events.length===0 && !document.getElementById('clearLogsButton').disabled") {
                                self.report["diagnosticClearViaProtectedLocalAPI"] = true
                                self.js("document.getElementById('logFilter').value='all';document.getElementById('logFilter').dispatchEvent(new Event('change'));true") { _ in action() }
                            }
                        }
                    }
                }
            }
        }
    }

    func groupGone(_ pid: pid_t) -> Bool { kill(-pid, 0) != 0 && errno == ESRCH }

    func checkBackendRecovery() {
        progress("actual backend failure alert and native reload")
        guard let backend = app.backend else { fail("No production backend"); return }
        let previous = backend.processIdentifier
        backend.terminate()
        wait("actual backend failure sheet", until: { self.app.currentAlert?.messageText == "控制台未能继续运行" }) {
            self.report["nativeBackendFailureAlert"] = true
            self.app.currentAlert!.buttons[0].performClick(nil)
            self.wait("backend sheet dismissal", until: { self.app.currentAlert == nil }) {
                guard self.menu("重新载入控制台") else { return }
                self.waitForEmpty {
                    guard self.app.backend?.processIdentifier != previous, self.groupGone(previous) else { self.fail("Failed backend was not replaced/stopped"); return }
                    self.report["nativeBackendReloadRecovered"] = true
                    self.js("openSettings('logs');true") { _ in
                        self.waitForJS("backend recovery log", "state.logs?.events.some(e=>e.event==='backend.stop' && e.result==='failure' && e.code==='SERVICE_UNAVAILABLE')") {
                            self.report["backendFailureLoggedAfterRecovery"] = true
                            self.snapshot("settings-backend-recovery-log") { self.finish(true) }
                        }
                    }
                }
            }
        }
    }

    func checkLogout() {
        progress("synthetic account files and nonpersistent cookie; logout cancel and confirm")
        do {
            try isolation.validate()
            let accountFixtures: [String: [String: Any]] = [
                "account/config.json": ["user": ["id": "gui-synthetic-account"], "courses": []],
                "account/login.json": ["user_id": "gui-synthetic-account", "transport": "webkit"],
                "account/app-settings.json": ["autoSync": false, "syncIntervalSeconds": 3600]]
            for (relative, object) in accountFixtures {
                let file = isolation.data.appendingPathComponent(relative)
                try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                try JSONSerialization.data(withJSONObject: object).write(to: file, options: .atomic)
                sentinels.append(file)
            }
            for relative in ["account/manual-sentinel.txt", "account/documents/files/Test/notice.txt",
                             "account/documents/text/cache-sentinel.json", "account/documents/web/page-sentinel.txt",
                             "account/diagnostics/old-log-sentinel.json"] {
                let file = isolation.data.appendingPathComponent(relative)
                try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                try Data("synthetic account-free sentinel\n".utf8).write(to: file)
                sentinels.append(file)
            }
        } catch { fail("Synthetic fixture setup failed: \(error)"); return }
        app.recordDiagnostic("settings.sync", "success")
        let cookie = HTTPCookie(properties: [.domain: "canvas.polyu.edu.hk", .path: "/", .name: "polyu-isolated-test",
                                            .value: "synthetic-only", .secure: "TRUE", .expires: Date().addingTimeInterval(3600)])!
        app.schoolDataStore().httpCookieStore.setCookie(cookie) {
            self.app.schoolDataStore().httpCookieStore.getAllCookies { cookies in
                guard cookies.contains(where: { $0.name == cookie.name }) else { self.fail("Synthetic nonpersistent cookie not set"); return }
                guard self.menu("退出登录…") else { return }
                self.wait("logout confirmation", until: { self.app.currentAlert?.buttons.count == 2 }) {
                    self.app.currentAlert!.buttons[1].performClick(nil)
                    self.wait("logout cancellation", until: { self.app.currentAlert == nil }) {
                        guard self.sentinels.allSatisfy({ FileManager.default.fileExists(atPath: $0.path) }) else { self.fail("Cancelled logout deleted data"); return }
                        self.app.schoolDataStore().httpCookieStore.getAllCookies { cookies in
                            guard cookies.contains(where: { $0.name == cookie.name }) else { self.fail("Cancelled logout cleared cookie"); return }
                            guard FileManager.default.fileExists(atPath: self.isolation.data.appendingPathComponent("account/diagnostics/events.json").path) else { self.fail("Synthetic account log was not saved before logout"); return }
                            self.report["syntheticAccountDiagnosticsPersistedBeforeLogout"] = true
                            self.report["logoutCancelPreservedSyntheticData"] = true
                            self.js("openSettings('logs');true") { _ in
                                self.waitForJS("logout cancellation log", "state.logs?.persistent===true && state.logs.events.some(e=>e.event==='logout' && e.result==='cancelled')") {
                                    self.report["logoutCancellationLogged"] = true
                                    self.snapshot("settings-cancelled-logout-log") {
                                        self.js("document.getElementById('closeSettingsButton').click();true") { _ in self.confirmLogout() }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    func confirmLogout() {
        guard let previous = app.backend?.processIdentifier, menu("退出登录…") else { fail("No backend before logout"); return }
        wait("second logout confirmation", until: { self.app.currentAlert?.buttons.count == 2 }) {
            self.app.currentAlert!.buttons[0].performClick(nil)
            self.wait("actual logout began", until: { self.app.resettingAccount || self.app.backend?.processIdentifier != previous }) {
                self.waitForEmpty {
                    self.app.schoolDataStore().httpCookieStore.getAllCookies { cookies in
                        guard cookies.isEmpty, self.sentinels.allSatisfy({ !FileManager.default.fileExists(atPath: $0.path) }),
                              !FileManager.default.fileExists(atPath: self.isolation.data.appendingPathComponent("logout-pending").path),
                              self.app.loginWindow == nil, self.app.auxiliaryWindows.isEmpty, self.groupGone(previous),
                              self.app.backend?.processIdentifier != previous else { self.fail("Actual logout cleanup failed"); return }
                        self.report["productionLogoutClearedSyntheticAccountAndWebKit"] = true
                        self.report["oldBackendGroupStoppedBeforeEmptyRestart"] = true
                        self.snapshot("after-logout-empty") {
                            self.js("openSettings('logs');true") { _ in
                                self.waitForJS("logout outcome logs", "state.logs?.persistent===false && state.logs.events.some(e=>e.event==='logout' && e.result==='success') && state.logs.events.some(e=>e.event==='logout.cleanup' && e.result==='success') && !state.logs.events.some(e=>e.event.startsWith('login.'))") {
                                    guard !FileManager.default.fileExists(atPath: self.isolation.data.appendingPathComponent("account/diagnostics/events.json").path) else { self.fail("Logged-out diagnostics were persisted"); return }
                                    self.report["logoutSuccessVisibleOnlyInProcessMemory"] = true
                                    self.report["accountDiagnosticHistoryDeleted"] = true
                                    self.snapshot("settings-after-logout-log") { self.finish(true) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    func fail(_ message: String) { report["error"] = message; finish(false) }

    func finish(_ success: Bool) {
        guard !done else { return }; done = true
        report["ok"] = success
        report["backendPIDs"] = app.backendPIDs
        report["productionNativeLifecycleValidated"] = success
        report["windowWasVisible"] = app.window.isVisible
        if let bytes = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? bytes.write(to: isolation.control.appendingPathComponent("report-" + phase + ".json"), options: .atomic)
        }
        if let sheet = app.window.attachedSheet { app.window.endSheet(sheet, returnCode: .abort) }
        NSApp.windows.filter { $0 !== app.window }.forEach { $0.close() }
        app.window.performClose(nil)
    }
}
