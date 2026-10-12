// Secrets travel over private stdin/stdout, never process arguments or logs.
import Foundation
import Security

func fail(_ status: OSStatus) -> Never {
    FileHandle.standardError.write(Data("KEYCHAIN_ERROR=\(status)\n".utf8))
    exit(1)
}
let args = CommandLine.arguments
if args.count != 3 && args.count != 5 { fail(errSecParam) }
let operation = args[1], account = args[2]
if !["get", "set", "delete"].contains(operation) || account.count != 64 ||
    account.range(of: "^[a-f0-9]{64}$", options: .regularExpression) == nil { fail(errSecParam) }
var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: "local.polyu.studyconsole.canvas-session.v1",
    kSecAttrAccount as String: account]
var testKeychain: SecKeychain?
if args.count == 5 {
    if args[3] != "--test-keychain" || !args[4].hasPrefix("/") { fail(errSecParam) }
    SecKeychainSetUserInteractionAllowed(false)
    let status = SecKeychainOpen(args[4], &testKeychain)
    if status != errSecSuccess { fail(status) }
    if let keychain = testKeychain { query[kSecMatchSearchList as String] = [keychain] }
}
if operation == "get" {
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { exit(3) }
    if status != errSecSuccess { fail(status) }
    guard let data = result as? Data else { fail(errSecDecode) }
    FileHandle.standardOutput.write(data)
} else if operation == "set" {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    if data.isEmpty || data.count > 262144 { fail(errSecParam) }
    let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
        query.removeValue(forKey: kSecMatchSearchList as String)
        if let keychain = testKeychain { query[kSecUseKeychain as String] = keychain }
        query[kSecValueData as String] = data
        let added = SecItemAdd(query as CFDictionary, nil)
        if added != errSecSuccess { fail(added) }
    } else if status != errSecSuccess { fail(status) }
} else {
    let status = SecItemDelete(query as CFDictionary)
    if status != errSecSuccess && status != errSecItemNotFound { fail(status) }
}
