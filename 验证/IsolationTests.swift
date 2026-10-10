import Foundation
import Darwin

@main struct IsolationChecks {
    static func main() throws {
        let manager = FileManager.default
        let temporary = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let root = temporary.appendingPathComponent("polyu-native-smoke-unit-" + UUID().uuidString, isDirectory: true)
        let nonce = UUID().uuidString
        let marker = root.appendingPathComponent(".polyu-isolated-test.json")
        try manager.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? manager.removeItem(at: root) }
        func writeMarker(_ value: String) throws {
            try JSONSerialization.data(withJSONObject: ["nonce": value]).write(to: marker)
            try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: marker.path)
        }
        try writeMarker(nonce)
        func args(_ path: String? = nil, token: String? = nil) -> [String] {
            ["test", "--isolated-test", path ?? root.path, "--isolation-nonce", token ?? nonce]
        }
        var checks = 0
        func denied(_ arguments: [String]) throws {
            var rejected = false
            do { _ = try TestIsolation.parse(arguments) } catch { rejected = true }
            guard rejected else { throw TestIsolation.problem("Unsafe isolation request was accepted") }
            checks += 1
        }
        guard try TestIsolation.parse(["test"]) == nil else { throw TestIsolation.problem("Normal launch changed") }
        checks += 1
        try denied(args("relative/path"))
        try denied(args("/private/var/pol yu-never-read"))
        try denied(["test", "--isolated-test", root.path])
        try denied(args(token: UUID().uuidString))
        try denied(args(root.path + "/../" + root.lastPathComponent))
        try manager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root.path)
        try denied(args())
        try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
        let unexpected = root.appendingPathComponent("unrelated.txt")
        try Data("synthetic".utf8).write(to: unexpected)
        try denied(args()); try manager.removeItem(at: unexpected)
        let markerTarget = temporary.appendingPathComponent("polyu-marker-fixture-" + UUID().uuidString)
        try Data("synthetic untouched fixture".utf8).write(to: markerTarget)
        defer { try? manager.removeItem(at: markerTarget) }
        try manager.removeItem(at: marker)
        try manager.createSymbolicLink(at: marker, withDestinationURL: markerTarget)
        try denied(args()); try manager.removeItem(at: marker); try writeMarker(nonce)
        let alias = temporary.appendingPathComponent("polyu-native-smoke-link-" + UUID().uuidString)
        try manager.createSymbolicLink(at: alias, withDestinationURL: root)
        defer { try? manager.removeItem(at: alias) }
        try denied(args(alias.path))
        let data = root.appendingPathComponent("app-data", isDirectory: true)
        try manager.createSymbolicLink(at: data, withDestinationURL: temporary)
        try denied(args()); try manager.removeItem(at: data)
        guard let valid = try TestIsolation.parse(args()), valid.data == data else { throw TestIsolation.problem("Valid fixture rejected") }
        try valid.prepareControl(); try valid.prepareControl(); try valid.validate()
        guard try String(contentsOf: markerTarget, encoding: .utf8) == "synthetic untouched fixture" else { throw TestIsolation.problem("Rejected link target changed") }
        checks += 1
        print("ISOLATION_CHECKS=\(checks)")
    }
}
