import Foundation
import Security
import CryptoKit
import Darwin

struct SavedCanvasCredential: Codable, Equatable {
    let version: Int
    let username: String
    let password: String
    let verifiedUserId: String
}
struct CredentialConsent: Codable {
    var version = 1
    var enabled = false
    var consentVersion = 1
    var activeRef: String?
    var pendingRefs: [String] = []
    var purgePending = false
}
enum CredentialFailure: Error, LocalizedError {
    case unavailable
    var errorDescription: String? { "本机密码保护或清理暂不可用；没有改用明文。请检查钥匙串或系统权限后重试。" }
}
final class CredentialVault {
    static let consentText = "允许本应用加密保存你的学校账号和密码，用于下次填入学校登录页面。建议仅在个人电脑开启。"
    let root: URL
    let testKeychain: SecKeychain?
    let lock = NSRecursiveLock()
    var directory: URL { root.appendingPathComponent("credentials", isDirectory: true) }
    var settings: URL { directory.appendingPathComponent("consent.json") }
    init(root: URL, testKeychain: SecKeychain? = nil) { self.root = root; self.testKeychain = testKeychain }
    func paths() throws {
        for url in [root, directory, settings] {
            if (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true { throw CredentialFailure.unavailable }
        }
    }
    func state() throws -> CredentialConsent {
        try paths()
        if !FileManager.default.fileExists(atPath: settings.path) { return CredentialConsent() }
        guard let data = try? Data(contentsOf: settings), data.count < 16384,
              let value = try? JSONDecoder().decode(CredentialConsent.self, from: data),
              value.version == 1, value.pendingRefs.count < 32,
              ([value.activeRef].compactMap { $0 } + value.pendingRefs).allSatisfy({ UUID(uuidString: $0) != nil }) else { throw CredentialFailure.unavailable }
        return value
    }
    func writeState(_ value: CredentialConsent) throws {
        try paths()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let temporary = directory.appendingPathComponent(".consent-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: temporary) }
        try JSONEncoder().encode(value).write(to: temporary, options: .withoutOverwriting)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        let handle = try FileHandle(forWritingTo: temporary)
        defer { try? handle.close() }
        try handle.synchronize()
        try paths()
        guard Darwin.rename(temporary.path, settings.path) == 0 else { throw CredentialFailure.unavailable }
    }
    func query(_ ref: String) -> [String: Any] {
        let account = SHA256.hash(data: Data(root.standardizedFileURL.path.utf8)).map { String(format: "%02x", $0) }.joined() + "." + ref
        var value: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "local.polyu.studyconsole.canvas-credentials.v1", kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: false]
        if let keychain = testKeychain { value[kSecMatchSearchList as String] = [keychain] }
        return value
    }
    func bytes(_ ref: String) throws -> Data? {
        var value = query(ref); value[kSecReturnData as String] = true; value[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let code = SecItemCopyMatching(value as CFDictionary, &result)
        if code == errSecItemNotFound { return nil }
        guard code == errSecSuccess, let data = result as? Data else { throw CredentialFailure.unavailable }
        return data
    }
    func delete(_ ref: String) throws {
        let code = SecItemDelete(query(ref) as CFDictionary)
        guard code == errSecSuccess || code == errSecItemNotFound else { throw CredentialFailure.unavailable }
        guard try bytes(ref) == nil else { throw CredentialFailure.unavailable }
    }
    // Staging refs make failed writes recoverable without overwriting the active password.
    func cleanup() throws {
        var value = try state()
        let refs = value.pendingRefs + (value.purgePending ? [value.activeRef].compactMap { $0 } : [])
        for ref in Set(refs) { try delete(ref) }
        if !refs.isEmpty || value.purgePending {
            value.pendingRefs = []
            if value.purgePending { value.activeRef = nil; value.enabled = false; value.purgePending = false }
            try writeState(value)
        }
    }
    func enable() throws {
        lock.lock(); defer { lock.unlock() }
        try cleanup(); var value = try state(); value.enabled = true; value.consentVersion = 1; try writeState(value)
    }
    func forget() throws {
        lock.lock(); defer { lock.unlock() }
        var value = try state(); value.enabled = false; value.purgePending = true; try writeState(value); try cleanup()
    }
    func record() throws -> SavedCanvasCredential? {
        lock.lock(); defer { lock.unlock() }
        try cleanup(); let value = try state()
        guard value.enabled, value.consentVersion == 1, let ref = value.activeRef else { return nil }
        guard let data = try bytes(ref), let row = try? JSONDecoder().decode(SavedCanvasCredential.self, from: data),
              row.version == 1, !row.username.isEmpty, row.username.count <= 512, !row.password.isEmpty,
              row.password.count <= 4096, !row.verifiedUserId.isEmpty,
              row.verifiedUserId.allSatisfy({ $0.isASCII && $0.isNumber }) else { throw CredentialFailure.unavailable }
        return row
    }
    func saveVerified(username: String, password: String, userId: String) throws {
        lock.lock(); defer { lock.unlock() }
        try cleanup(); var value = try state()
        guard value.enabled, value.consentVersion == 1, !username.isEmpty, username.count <= 512,
              !password.isEmpty, password.count <= 4096, !userId.isEmpty,
              userId.allSatisfy({ $0.isASCII && $0.isNumber }) else { throw CredentialFailure.unavailable }
        let row = SavedCanvasCredential(version: 1, username: username, password: password, verifiedUserId: userId)
        let data = try JSONEncoder().encode(row)
        let ref = UUID().uuidString
        let previousRef = value.activeRef
        value.pendingRefs.append(ref); try writeState(value)
        do {
            var add = query(ref); add.removeValue(forKey: kSecMatchSearchList as String)
            if let keychain = testKeychain { add[kSecUseKeychain as String] = keychain }
            add[kSecValueData as String] = data
            let code = SecItemAdd(add as CFDictionary, nil)
            if code != errSecSuccess, testKeychain != nil { FileHandle.standardError.write(Data("SYNTHETIC_KEYCHAIN_ADD_CODE=\(code)\n".utf8)) }
            guard code == errSecSuccess, try bytes(ref) == data else { throw CredentialFailure.unavailable }
            value.activeRef = ref; value.pendingRefs = [previousRef].compactMap { $0 }
            try writeState(value)
        } catch { try? cleanup(); throw CredentialFailure.unavailable }
        try cleanup()
    }
    func status(owner: String? = nil) -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        do {
            let row = try record(); let enabled = try state().enabled
            let mismatch = row != nil && owner != nil && row?.verifiedUserId != owner
            let label = row == nil ? "" : String(row!.username.prefix(1)) + "***" + (row!.username.count > 1 ? String(row!.username.suffix(1)) : "")
            return ["enabled": enabled, "state": row != nil && !mismatch ? "saved" : enabled ? "pending" : "off",
                    "maskedUsername": label, "differentAccount": mismatch]
        } catch { return ["enabled": false, "state": "unavailable", "maskedUsername": ""] }
    }
    static func logoutPolicy(_ root: URL) -> Bool {
        let file = root.appendingPathComponent("logout-pending")
        guard let data = try? Data(contentsOf: file),
              let row = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              row["version"] as? Int == 2 else { return false }
        return row["keepRememberedPassword"] as? Bool == true
    }
}
final class LoginCredentialAttempt {
    let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    var documentId: String?
    var candidate: (username: String, password: String)?
    var filled = false
    var active = true
    static func schoolURL(_ url: URL?) -> Bool {
        guard let url = url else { return false }
        return url.scheme == "https" && url.host == "adfs.polyu.edu.hk" && (url.port == nil || url.port == 443) && url.user == nil && url.password == nil && URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath == "/adfs/ls/"
    }
    func close() { active = false; candidate = nil; documentId = nil }
    func receive(source: URL?, mainFrame: Bool, body: [String: Any], enabled: Bool) -> Bool {
        guard active, enabled, mainFrame, Self.schoolURL(source), body["type"] as? String == "polyuRemember",
              body["nonce"] as? String == nonce, let document = body["documentId"] as? String,
              document.count == 32, document.allSatisfy({ "0123456789abcdef".contains($0) }) else { return false }
        let keys = Set(body.keys)
        if body["kind"] as? String == "ready", keys == Set(["type", "kind", "nonce", "documentId"]) {
            documentId = document; candidate = nil; return true
        }
        guard body["kind"] as? String == "candidate", document == documentId,
              keys == Set(["type", "kind", "nonce", "documentId", "username", "password"]),
              let username = body["username"] as? String, let password = body["password"] as? String,
              !username.isEmpty, username.count <= 512, !password.isEmpty, password.count <= 4096 else { return false }
        candidate = (username, password); return true
    }
}
