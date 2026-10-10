import Foundation
import Darwin

struct TestIsolation {
    let root: URL
    let nonce: String
    let phase: String?
    var data: URL { root.appendingPathComponent("app-data", isDirectory: true) }
    var control: URL { root.appendingPathComponent("control", isDirectory: true) }

    static func problem(_ text: String) -> NSError {
        NSError(domain: "PolyUTestIsolation", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
    }

    static func item(_ url: URL, directory: Bool, optional: Bool = false) throws {
        var info = stat()
        let result = url.path.withCString { lstat($0, &info) }
        if result != 0 {
            if optional && errno == ENOENT { return }
            throw problem("隔离路径不存在或不可读取。")
        }
        let kind = info.st_mode & mode_t(S_IFMT)
        guard kind == mode_t(directory ? S_IFDIR : S_IFREG), info.st_uid == getuid(),
              info.st_mode & mode_t(0o077) == 0 else {
            throw problem("隔离路径必须属于当前用户、仅本人可访问，且不能是链接。")
        }
    }

    static func parse(_ arguments: [String]) throws -> TestIsolation? {
        let reserved = ["--isolated-test", "--isolation-nonce", "--gui-smoke"]
        guard arguments.contains(where: { reserved.contains($0) }) else { return nil }
        guard arguments.count == 5 || arguments.count == 7,
              arguments[1] == "--isolated-test", arguments[3] == "--isolation-nonce",
              UUID(uuidString: arguments[4]) != nil else { throw problem("隔离测试参数不完整。") }
        let raw = arguments[2]
        guard raw.hasPrefix("/"), !raw.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }) else {
            throw problem("测试目录必须是没有相对跳转的临时绝对路径。")
        }
        let input = URL(fileURLWithPath: raw, isDirectory: true).standardizedFileURL
        let temporary = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true).standardizedFileURL
        let aliases = [temporary, temporary.resolvingSymlinksInPath(), URL(fileURLWithPath: "/tmp", isDirectory: true),
                       URL(fileURLWithPath: "/private/tmp", isDirectory: true)]
        guard let parent = aliases.first(where: { input.deletingLastPathComponent().path == $0.path }),
              input.lastPathComponent.hasPrefix("polyu-native-smoke-"), input.lastPathComponent.count > 19 else {
            throw problem("隔离测试只接受临时目录下独立的 polyu-native-smoke-* 目录。")
        }
        let root = parent.resolvingSymlinksInPath().appendingPathComponent(input.lastPathComponent, isDirectory: true)
        try item(root, directory: true)
        var phase: String?
        if arguments.count == 7 {
            guard arguments[5] == "--gui-smoke", ["first", "reopen", "logout"].contains(arguments[6]) else {
                throw problem("未知 GUI 测试阶段。")
            }
            phase = arguments[6]
        }
        let context = TestIsolation(root: root, nonce: arguments[4], phase: phase)
        try context.validate()
        return context
    }

    func validate() throws {
        try Self.item(root, directory: true)
        let marker = root.appendingPathComponent(".polyu-isolated-test.json")
        try Self.item(marker, directory: false)
        let attributes = try FileManager.default.attributesOfItem(atPath: marker.path)
        guard ((attributes[.size] as? NSNumber)?.intValue ?? 99999) <= 4096,
              let object = try JSONSerialization.jsonObject(with: Data(contentsOf: marker)) as? [String: Any],
              object["nonce"] as? String == nonce else { throw Self.problem("隔离目录的显式测试标记不匹配。") }
        let allowed = Set([".polyu-isolated-test.json", "app-data", "control"])
        guard Set(try FileManager.default.contentsOfDirectory(atPath: root.path)).isSubset(of: allowed) else {
            throw Self.problem("隔离目录包含不属于本测试的内容。")
        }
        try Self.item(data, directory: true, optional: true)
        try Self.item(control, directory: true, optional: true)
    }

    func prepareControl() throws {
        try validate()
        try FileManager.default.createDirectory(at: control, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
    }
}
