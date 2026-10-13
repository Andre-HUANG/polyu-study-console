
import AppKit
import Sparkle
import Foundation
import Darwin
let applicationVersion = Bundle.main.infoDictionary!["CFBundleShortVersionString"] as! String
let testRoot = URL(fileURLWithPath: Bundle.main.infoDictionary!["TestRoot"] as! String)
func record(_ key:String,_ value:Any=true) {
    let file=testRoot.appendingPathComponent("events-"+applicationVersion+".json")
    var data=(try? JSONSerialization.jsonObject(with:Data(contentsOf:file))) as? [String:Any] ?? [:]
    data[key]=value
    try? JSONSerialization.data(withJSONObject:data,options:[.sortedKeys]).write(to:file,options:.atomic)
}
final class Driver:NSObject,SPUUserDriver {
    func show(_ request:SPUUpdatePermissionRequest,reply:@escaping(SUUpdatePermissionResponse)->Void) {reply(SUUpdatePermissionResponse(automaticUpdateChecks:false,sendSystemProfile:false))}
    func showUserInitiatedUpdateCheck(cancellation:@escaping()->Void) { record("check") }
    func showUpdateFound(with appcastItem:SUAppcastItem,state:SPUUserUpdateState,reply:@escaping(SPUUserUpdateChoice)->Void) {record("found",appcastItem.displayVersionString);reply(.install)}
    func showUpdateReleaseNotes(with downloadData:SPUDownloadData) {}
    func showUpdateReleaseNotesFailedToDownloadWithError(_ error:Error) {}
    func showUpdateNotFoundWithError(_ error:Error,acknowledgement:@escaping()->Void) {record("notFound",String(describing:error));acknowledgement();NSApp.terminate(nil)}
    func showUpdaterError(_ error:Error,acknowledgement:@escaping()->Void) {record("error",String(describing:error));acknowledgement();NSApp.terminate(nil)}
    func showDownloadInitiated(cancellation:@escaping()->Void) {record("download")}
    func showDownloadDidReceiveExpectedContentLength(_ expectedContentLength:UInt64) {}
    func showDownloadDidReceiveData(ofLength length:UInt64) {}
    func showDownloadDidStartExtractingUpdate() {record("extracting")}
    func showExtractionReceivedProgress(_ progress:Double) {}
    func showReady(toInstallAndRelaunch reply:@escaping(SPUUserUpdateChoice)->Void) {record("ready");reply(.install)}
    func showInstallingUpdate(withApplicationTerminated applicationTerminated:Bool,retryTerminatingApplication:@escaping()->Void) {record("installing")}
    func showUpdateInstalledAndRelaunched(_ relaunched:Bool,acknowledgement:@escaping()->Void) {record("installed");acknowledgement()}
    func dismissUpdateInstallation() {}
}
final class Delegate:NSObject,NSApplicationDelegate,SPUUpdaterDelegate {
    var updater:SPUUpdater?
    let driver=Driver()
    var window:NSWindow!
    func applicationDidFinishLaunching(_ note:Notification) {
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:420,height:200),styleMask:[.titled,.closable],backing:.buffered,defer:false)
        window.title="PolyU isolated updater validation "+applicationVersion
        window.makeKeyAndOrderFront(nil)
        record("launch",Int(getpid()))
        if applicationVersion=="1.8.3" {
            UpdateRecovery.recordLaunch()
            if Bundle.main.infoDictionary!["TestCrash"] as? Bool == true { exit(7) }
            UpdateRecovery.acknowledge()
            record("acknowledged")
            DispatchQueue.main.asyncAfter(deadline:.now()+2) {NSApp.terminate(nil)}
            return
        }
        if FileManager.default.fileExists(atPath:testRoot.appendingPathComponent("events-1.8.3.json").path) {
            record("rolledBack");NSApp.terminate(nil);return
        }
        updater=SPUUpdater(hostBundle:Bundle.main,applicationBundle:Bundle.main,userDriver:driver,delegate:self)
        do {try updater!.start();updater!.checkForUpdates()}
        catch {record("error",String(describing:error));NSApp.terminate(nil)}
    }
    func updater(_ updater:SPUUpdater,shouldPostponeRelaunchForUpdate item:SUAppcastItem,untilInvokingBlock installHandler:@escaping()->Void)->Bool {
        do {try UpdateRecovery.prepare(version:item.displayVersionString);record("recoveryPrepared");installHandler()}
        catch {record("error",String(describing:error));NSApp.terminate(nil)}
        return true
    }
}
@main struct SmokeMain {
 static func main() {
    guard testRoot.path.hasPrefix(NSTemporaryDirectory()) || testRoot.path.hasPrefix("/private"+NSTemporaryDirectory()) else {exit(8)}
    if CommandLine.arguments.count==3 && CommandLine.arguments[1]=="--update-watchdog" {exit(UpdateRecovery.watch(CommandLine.arguments[2]))}
    let app=NSApplication.shared;let delegate=Delegate();app.delegate=delegate;app.setActivationPolicy(.regular);app.run()
 }
}
