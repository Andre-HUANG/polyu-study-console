import AppKit
import WebKit
import Sparkle

extension AppDelegate: SPUUpdaterDelegate {
    func initializeUpdater() {
        guard testIsolation == nil else { return }
        updaterController = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        updaterController?.updater.automaticallyDownloadsUpdates = false
        updaterController?.updater.automaticallyChecksForUpdates = true
        updaterController?.updater.updateCheckInterval = 86400
        updaterController?.startUpdater()
    }

    func checkSoftwareUpdates() {
        guard testIsolation == nil, !resettingAccount, !terminating else { return }
        updaterController?.checkForUpdates(nil)
    }

    func feedURLString(for updater: SPUUpdater) -> String? {
        return "https://github.com/Andre-HUANG/polyu-study-console/releases/latest/download/appcast-macos.xml"
    }

    func feedParameters(for updater: SPUUpdater, sendingSystemProfile: Bool) -> [[String: String]] { return [] }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem, untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        // Download and validation run while the user keeps using the dashboard.
        // Restart only after all account writes/recognition reach a safe end.
        pendingUpdateVersion = item.displayVersionString
        prepareUpdateRestart(installHandler)
        return true
    }

    func prepareUpdateRestart(_ completion: @escaping () -> Void) {
        guard !terminating, !resettingAccount else { return }
        updateGeneration += 1
        let generation = updateGeneration
        console.evaluateJavaScript("window.polyuPrepareUpdate?.() ?? false") { [weak self] result, _ in
            guard let self = self, generation == self.updateGeneration else { return }
            if result as? Bool != true {
                self.alert("请先保存安排", "课程表或安排表单尚未保存。保存或关闭表单后再安装更新。")
                self.pendingUpdateInstall = completion
                return
            }
            do { try UpdateRecovery.prepare(version: self.pendingUpdateVersion ?? applicationVersion) }
            catch { self.alert("更新准备未完成",error.localizedDescription);self.pendingUpdateInstall=completion;return }
            self.waitForSafeUpdate(completion, generation: generation)
        }
    }

    func waitForSafeUpdate(_ completion: @escaping () -> Void, generation: Int) {
        guard generation == updateGeneration else { return }
        postNative("update/prepare", payload: [:]) { [weak self] result in
            guard let self = self, !self.terminating, !self.resettingAccount else { return }
            if generation != self.updateGeneration {
                if case .success = result, self.backend != nil {
                    self.stopBackend(); self.showStartup("更新已取消，正在恢复学习服务…"); self.startBackend()
                }
                return
            }
            switch result {
            case .success:
                self.console.stopLoading()
                self.stopBackend()
                self.pendingUpdateInstall = nil
                completion()
            case .failure:
                self.console.evaluateJavaScript("window.polyuNativeNotice?.('更新包已校验；正在等待当前同步与识别安全完成。')")
                DispatchQueue.main.asyncAfter(deadline: .now()+2) { [weak self] in self?.waitForSafeUpdate(completion, generation: generation) }
            }
        }
    }

    func acknowledgeUpdateWhenReady() {
        guard testIsolation == nil else { return }
        console.evaluateJavaScript("typeof state !== 'undefined' && state.data?.version") { [weak self] value, _ in
            guard let self = self, !self.terminating else { return }
            if value as? String == applicationVersion { UpdateRecovery.acknowledge() }
            else { DispatchQueue.main.asyncAfter(deadline:.now()+1) { [weak self] in self?.acknowledgeUpdateWhenReady() } }
        }
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        guard !terminating, !resettingAccount else { return }
        updateGeneration += 1
        pendingUpdateInstall = nil
        pendingUpdateVersion = nil
        _ = UpdateRecovery.abortUncommitted()
        guard backend == nil else { return }
        showStartup("更新未完成，正在恢复学习服务…")
        startBackend()
    }

    func updaterWillRelaunchApplication(_ updater: SPUUpdater) {
        stopBackend()
    }
}
