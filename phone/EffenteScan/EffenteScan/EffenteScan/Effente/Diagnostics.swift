/*
effente scan: a small log kept in the app's own Documents folder, so a crash on
a phone with no debugger attached can still be explained. It records the steps
the app took and, if the app dies on an uncaught exception or an abort, why.
Nothing in it leaves the phone unless a person copies the file off.
*/

import Foundation

enum Diagnostics {
    static let file = URL.documentsDirectory.appendingPathComponent("effente-log.txt")

    /// Adds one line to the log, at once, so it is on disk before whatever comes next.
    static func note(_ text: String) {
        let line = ISO8601DateFormatter().string(from: Date()) + " " + text + "\n"
        guard let data = line.data(using: .utf8) else { return }
        if let handle = try? FileHandle(forWritingTo: file) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
        } else {
            try? data.write(to: file)
        }
    }

    /// Call once when the app starts.
    static func start() {
        // Keep the log small: start again when it passes 200 kB.
        if let size = (try? FileManager.default.attributesOfItem(atPath: file.path))?[.size] as? NSNumber,
           size.intValue > 200_000 {
            try? FileManager.default.removeItem(at: file)
        }
        note("---- app started, version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?")")

        NSSetUncaughtExceptionHandler { exception in
            Diagnostics.note("UNCAUGHT EXCEPTION \(exception.name.rawValue): \(exception.reason ?? "no reason")")
            Diagnostics.note(exception.callStackSymbols.prefix(40).joined(separator: "\n"))
        }
        signal(SIGABRT) { _ in
            Diagnostics.note("SIGABRT on thread \(Thread.isMainThread ? "main" : "other")")
            Diagnostics.note(Thread.callStackSymbols.prefix(40).joined(separator: "\n"))
            signal(SIGABRT, SIG_DFL)
            abort()
        }
    }
}
