import AppKit

let destination = CommandLine.arguments[1]
guard CommandLine.arguments.count == 3, let emblem = NSImage(contentsOfFile: CommandLine.arguments[2]) else {
    fatalError("Missing official PolyU emblem asset")
}
try FileManager.default.createDirectory(atPath: destination, withIntermediateDirectories: true)
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let base = NSBezierPath(roundedRect: NSRect(x: 42, y: 42, width: 940, height: 940), xRadius: 212, yRadius: 212)
NSColor.white.setFill(); base.fill()
// Place the complete official bitmap at its original aspect ratio and colors.
let scale = min(916 / emblem.size.width, 850 / emblem.size.height)
let width = emblem.size.width * scale, height = emblem.size.height * scale
emblem.draw(in: NSRect(x: (1024-width)/2, y: (1024-height)/2, width: width, height: height))
image.unlockFocus()
for (points, scale) in [(16,1),(16,2),(32,1),(32,2),(128,1),(128,2),(256,1),(256,2),(512,1),(512,2)] {
    let pixels = points*scale
    let representation = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: representation)
    image.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
    NSGraphicsContext.restoreGraphicsState()
    let suffix = scale == 2 ? "@2x" : ""
    try representation.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination + "/icon_\(points)x\(points)\(suffix).png"))
}
