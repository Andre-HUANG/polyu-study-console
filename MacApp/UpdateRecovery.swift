import AppKit
import Foundation
import Darwin
import Security

// Only application bundles are backed up. No Application Support/account path
// enters this program-update transaction.
enum UpdateRecovery {
    static var container: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("local.polyu.studyconsole/program-updates", isDirectory: true)
    }
    static var marker: URL { container.appendingPathComponent("pending.json") }
    static let identifier = "local.polyu.studyconsole"

    static func metadata(_ app: URL) throws -> [String: Any] {
        let value = try Data(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
        guard let result = try PropertyListSerialization.propertyList(from: value, format: nil) as? [String: Any],
              result["CFBundleIdentifier"] as? String == identifier else {
            throw NSError(domain: "ProgramUpdate", code: 1, userInfo: [NSLocalizedDescriptionKey: "程序回退来源无效"])
        }
        return result
    }
    static func safe(_ value: URL, below root: URL? = nil) throws -> URL {
        let url = value.standardizedFileURL
        guard url.path == url.resolvingSymlinksInPath().path else {
            throw NSError(domain: "ProgramUpdate", code: 2, userInfo: [NSLocalizedDescriptionKey: "程序更新位置不能是链接"])
        }
        if let root = root {
            guard url.path.hasPrefix(root.standardizedFileURL.path + "/") else { throw NSError(domain:"ProgramUpdate",code:3) }
        }
        return url
    }
    static func write(_ value: [String: Any], at url: URL) throws {
        let data = try JSONSerialization.data(withJSONObject: value)
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
    static func prepare(version: String) throws {
        let manager=FileManager.default
        try manager.createDirectory(at: container, withIntermediateDirectories: true, attributes:[.posixPermissions:0o700])
        guard !manager.fileExists(atPath: marker.path) else {
            throw NSError(domain:"ProgramUpdate",code:4,userInfo:[NSLocalizedDescriptionKey:"上次程序更新尚未完成，请重新启动后重试。"])
        }
        let target=try safe(Bundle.main.bundleURL.resolvingSymlinksInPath())
        _ = try metadata(target)
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(target as CFURL, [], &code)==errSecSuccess,
              let code=code, SecStaticCodeCheckValidity(code, SecCSFlags(rawValue:kSecCSCheckAllArchitectures | kSecCSCheckNestedCode), nil)==errSecSuccess else {
            throw NSError(domain:"ProgramUpdate",code:7,userInfo:[NSLocalizedDescriptionKey:"当前程序签名校验未通过，不能安全创建回退副本。"])
        }
        let forbidden=Set(["account","browser","webview","documents","canvas-cookies.json","login.json","config.json","state.json","grading_schemes.json"])
        if let files=manager.enumerator(at:target,includingPropertiesForKeys:nil) {
            for case let file as URL in files where forbidden.contains(file.lastPathComponent.lowercased()) {
                throw NSError(domain:"ProgramUpdate",code:8,userInfo:[NSLocalizedDescriptionKey:"程序目录含账号资料，请重新安装完整程序后再更新。"])
            }
        }
        let folder=container.appendingPathComponent("program-"+UUID().uuidString,isDirectory:true)
        try manager.createDirectory(at: folder, withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
        let backup=folder.appendingPathComponent("previous.app")
        try manager.copyItem(at: target, to: backup)
        _ = try metadata(backup)
        let spec:[String:Any]=["schema":1,"target":target.path,"backup":backup.path,"folder":folder.path,"pid":getpid(),"version":version]
        try write(spec,at:folder.appendingPathComponent("transaction.json"))
        try write(spec,at:marker)
        let watcher=Process()
        watcher.executableURL=backup.appendingPathComponent("Contents/MacOS/PolyUConsole")
        watcher.arguments=["--update-watchdog",folder.appendingPathComponent("transaction.json").path]
        watcher.standardOutput=FileHandle.nullDevice;watcher.standardError=FileHandle.nullDevice
        do { try watcher.run() }
        catch {
            try? manager.removeItem(at:marker)
            try? manager.removeItem(at:folder)
            throw error
        }
    }
    static func abortUncommitted() -> Bool {
        do {
            let data=try Data(contentsOf:marker)
            guard let spec=try JSONSerialization.jsonObject(with:data) as? [String:Any],
                  spec["pid"] as? Int == Int(getpid()),
                  spec["target"] as? String == Bundle.main.bundleURL.resolvingSymlinksInPath().path,
                  let folderPath=spec["folder"] as? String, let backupPath=spec["backup"] as? String else { return false }
            let folder=try safe(URL(fileURLWithPath:folderPath),below:container)
            let backup=try safe(URL(fileURLWithPath:backupPath),below:folder)
            let original=try metadata(backup);let current=try metadata(Bundle.main.bundleURL.resolvingSymlinksInPath())
            guard original["CFBundleVersion"] as? String == current["CFBundleVersion"] as? String else { return false }
            try write(["pid":getpid()],at:folder.appendingPathComponent("aborted.json"))
            try FileManager.default.removeItem(at:marker)
            return true
        } catch { return false }
    }
    static func recordLaunch() {
        guard let data=try? Data(contentsOf:marker),var spec=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],
              spec["target"] as? String == Bundle.main.bundleURL.resolvingSymlinksInPath().path,
              spec["version"] as? String == applicationVersion,
              let folderPath=spec["folder"] as? String,
              let folder=try? safe(URL(fileURLWithPath:folderPath),below:container) else { return }
        spec["newPID"]=getpid()
        try? write(spec,at:folder.appendingPathComponent("launched.json"))
    }
    static func acknowledge() {
        guard let data=try? Data(contentsOf:marker),let spec=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],
              spec["target"] as? String == Bundle.main.bundleURL.resolvingSymlinksInPath().path,
              spec["version"] as? String == applicationVersion,
              let path=spec["folder"] as? String,
              let folder=try? safe(URL(fileURLWithPath:path),below:container) else { return }
        try? write(["version":applicationVersion,"pid":getpid()],at:folder.appendingPathComponent("ready.json"))
    }
    static func watch(_ path: String) -> Int32 {
        do {
            let specification=try safe(URL(fileURLWithPath:path),below:container)
            let data=try Data(contentsOf:specification)
            guard let spec=try JSONSerialization.jsonObject(with:data) as? [String:Any],
                  spec["schema"] as? Int == 1,
                  let targetPath=spec["target"] as? String,let backupPath=spec["backup"] as? String,
                  let pid=spec["pid"] as? Int,let version=spec["version"] as? String else { throw NSError(domain:"ProgramUpdate",code:5) }
            let folder=specification.deletingLastPathComponent()
            let target=try safe(URL(fileURLWithPath:targetPath))
            let backup=try safe(URL(fileURLWithPath:backupPath),below:folder)
            _=try metadata(backup)
            let limit=Date().addingTimeInterval(600)
            while kill(pid_t(pid),0)==0 && Date()<limit {
                if let data=try? Data(contentsOf:folder.appendingPathComponent("aborted.json")),
                   let cancelled=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],
                   cancelled["pid"] as? Int == pid {
                    if let markerData=try? Data(contentsOf:marker),
                       let pending=(try? JSONSerialization.jsonObject(with:markerData)) as? [String:Any],
                       pending["folder"] as? String == folder.path { try? FileManager.default.removeItem(at:marker) }
                    try FileManager.default.removeItem(at:folder)
                    return 0
                }
                Thread.sleep(forTimeInterval:0.5)
            }
            if kill(pid_t(pid),0)==0 { return 1 } // Original app still owns writes.
            let ready=folder.appendingPathComponent("ready.json")
            let deadline=Date().addingTimeInterval(120)
            while !FileManager.default.fileExists(atPath:ready.path) && Date()<deadline { Thread.sleep(forTimeInterval:0.5) }
            if let readyData=try? Data(contentsOf:ready),
               let result=(try? JSONSerialization.jsonObject(with:readyData)) as? [String:Any],
               result["version"] as? String == version {
                try FileManager.default.removeItem(at:marker)
                try FileManager.default.removeItem(at:folder)
                return 0
            }
            if let current=try? metadata(target), let old=try? metadata(backup),
               current["CFBundleVersion"] as? String == old["CFBundleVersion"] as? String {
                try FileManager.default.removeItem(at:marker)
                try FileManager.default.removeItem(at:folder)
                return 0 // Sparkle did not commit a replacement.
            }
            if let launched=try? Data(contentsOf:folder.appendingPathComponent("launched.json")),
               let value=(try? JSONSerialization.jsonObject(with:launched)) as? [String:Any],
               let newPID=value["newPID"] as? Int,let app=NSRunningApplication(processIdentifier:pid_t(newPID)),
               app.bundleURL?.resolvingSymlinksInPath()==target {
                app.terminate();Thread.sleep(forTimeInterval:3)
                if !app.isTerminated { app.forceTerminate() }
            }
            let failed=target.deletingLastPathComponent().appendingPathComponent(target.lastPathComponent+".failed-update")
            guard !FileManager.default.fileExists(atPath:failed.path) else { throw NSError(domain:"ProgramUpdate",code:6) }
            if FileManager.default.fileExists(atPath:target.path) { try FileManager.default.moveItem(at:target,to:failed) }
            do { try FileManager.default.copyItem(at:backup,to:target) }
            catch {
                if FileManager.default.fileExists(atPath:failed.path) { try? FileManager.default.moveItem(at:failed,to:target) }
                throw error
            }
            try? FileManager.default.removeItem(at:failed)
            try FileManager.default.removeItem(at:marker)
            let launch=Process()
            launch.executableURL=URL(fileURLWithPath:"/usr/bin/open")
            launch.arguments=["-n","-a",target.path]
            launch.standardOutput=FileHandle.nullDevice;launch.standardError=FileHandle.nullDevice
            try launch.run();launch.waitUntilExit()
            if launch.terminationStatus != 0 { throw NSError(domain:"ProgramUpdate",code:9) }
            return 0
        } catch { return 1 } // Keep previous.app for a recoverable program copy.
    }
}
