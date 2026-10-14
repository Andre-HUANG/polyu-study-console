import Foundation
import Security
@main struct RememberPasswordChecks {
    static func main() throws {
        SecKeychainSetUserInteractionAllowed(false)
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        var keychain: SecKeychain?
        guard SecKeychainOpen(CommandLine.arguments[2], &keychain) == errSecSuccess, let keychain = keychain else { fatalError("Synthetic keychain unavailable") }
        let vault = CredentialVault(root: root, testKeychain: keychain)
        if CommandLine.arguments.count > 3 {
            let mode = CommandLine.arguments[3]
            if mode == "seed" {
                try vault.enable(); try vault.saveVerified(username: "synthetic-process", password: "synthetic-process-only", userId: "123")
            } else if mode == "reopen" {
                guard let row = try vault.record(), row.password == "synthetic-process-only", row.verifiedUserId == "123" else { exit(1) }
            } else if mode == "forget" {
                try vault.forget(); guard try vault.record() == nil else { exit(1) }
            } else { exit(1) }
            print("SYNTHETIC_PROCESS_OK")
            return
        }
        var count = 0
        func check(_ value: @autoclosure () -> Bool) { if !value() { FileHandle.standardError.write(Data("SYNTHETIC_CHECK_\(count + 1)\n".utf8)); exit(1) }; count += 1 }
        check(vault.status()["state"] as? String == "off")
        check(!FileManager.default.fileExists(atPath: vault.directory.path))
        try vault.enable()
        check(vault.status()["state"] as? String == "pending")
        let secret = "synthetic-only-\"'\\换行\n $() </script>"
        FileHandle.standardError.write(Data("SYNTHETIC_PHASE_FIRST_WRITE\n".utf8))
        try vault.saveVerified(username: "synthetic-netid", password: secret, userId: "123")
        let row = try vault.record()
        check(row?.password == secret)
        check(row?.verifiedUserId == "123")
        let restarted = CredentialVault(root: root, testKeychain: keychain)
        check(try! restarted.record() == row)
        check(vault.status(owner: "456")["differentAccount"] as? Bool == true)
        check(vault.status(owner: "456")["state"] as? String == "pending")
        let metadata = try String(contentsOf: vault.settings, encoding: .utf8)
        check(!metadata.contains(secret) && !metadata.contains("synthetic-netid"))
        let previous = try vault.state().activeRef
        check(SecKeychainLock(keychain) == errSecSuccess)
        do { try vault.saveVerified(username: "synthetic-new", password: "synthetic-new", userId: "123"); fatalError("Locked keychain accepted a write") }
        catch { count += 1 }
        check(try! vault.state().activeRef == previous)
        let key = ProcessInfo.processInfo.environment["POLYU_SYNTHETIC_KEYCHAIN_PASSWORD"]!
        let unlocked = key.withCString { SecKeychainUnlock(keychain, UInt32(key.utf8.count), $0, true) }
        check(unlocked == errSecSuccess)
        FileHandle.standardError.write(Data("SYNTHETIC_PHASE_RECOVERY\n".utf8))
        try vault.cleanup()
        check(try! vault.record() == row)
        let attempt = LoginCredentialAttempt()
        let source = URL(string: "https://adfs.polyu.edu.hk/adfs/ls/?fixture=true")!
        check(LoginCredentialAttempt.schoolURL(source))
        let ready: [String: Any] = ["type":"polyuRemember", "kind":"ready", "nonce":attempt.nonce, "documentId":String(repeating:"a",count:32)]
        check(!attempt.receive(source: source, mainFrame: false, body: ready, enabled: true))
        check(!attempt.receive(source: source, mainFrame: true, body: ready, enabled: false))
        check(!attempt.receive(source: URL(string:"https://adfs.polyu.edu.hk.evil.example/adfs/ls/"), mainFrame: true, body:ready, enabled:true))
        check(attempt.receive(source: source, mainFrame: true, body: ready, enabled: true))
        var candidate = ready; candidate["kind"] = "candidate"; candidate["username"] = "synthetic-netid"; candidate["password"] = secret
        check(attempt.receive(source: source, mainFrame: true, body: candidate, enabled: true))
        check(attempt.candidate?.password == secret)
        var next = ready; next["documentId"] = String(repeating:"b",count:32)
        check(attempt.receive(source: source, mainFrame: true, body: next, enabled: true))
        check(attempt.candidate == nil)
        attempt.close(); check(!attempt.receive(source: source, mainFrame: true, body: ready, enabled: true))
        try vault.forget()
        check(vault.status()["state"] as? String == "off")
        check(try! vault.bytes(previous!) == nil)
        try vault.enable(); try vault.saveVerified(username: "synthetic-delete", password: "synthetic-delete-only", userId: "123")
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: vault.directory.path)
        do { try vault.forget(); fatalError("Read-only consent directory accepted deletion") }
        catch { count += 1 }
        check(vault.status()["state"] as? String == "unavailable")
        check(vault.status()["enabled"] as? Bool == false)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: vault.directory.path)
        check(try! vault.record() == nil)
        check(vault.status()["state"] as? String == "off")
        let marker = root.appendingPathComponent("logout-pending")
        try JSONSerialization.data(withJSONObject:["version":2,"keepRememberedPassword":true]).write(to:marker)
        check(CredentialVault.logoutPolicy(root))
        try Data("pending\n".utf8).write(to:marker)
        check(!CredentialVault.logoutPolicy(root))
        print("REMEMBER_CHECKS=\(count)")
    }
}
