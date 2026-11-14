import Foundation

/// One scan waiting to be sent. Made once when the model is built and kept
/// until the laptop answers 200, so every retry carries the same `clientID`.
public struct PendingScan: Codable, Equatable, Sendable {
    /// One per scan. The laptop stores nothing twice for the same id (5.3).
    public var clientID: String
    public var pairing: Pairing
    /// When the capture finished.
    public var capturedAt: Date
    /// The file name to show people on the laptop.
    public var fileName: String

    public init(pairing: Pairing, capturedAt: Date, fileName: String = "scan.usdz",
                clientID: String = UUID().uuidString) {
        self.clientID = clientID
        self.pairing = pairing
        self.capturedAt = capturedAt
        self.fileName = fileName
    }
}

public enum UploadError: Error, Equatable, Sendable {
    case emptyFile
    case tooLarge(bytes: Int64, limit: Int64)

    public var message: String {
        switch self {
        case .emptyFile:
            return "The scan file is empty."
        case .tooLarge(let bytes, let limit):
            return "The scan is \(megabytes(bytes)) and the laptop takes at most \(megabytes(limit))."
        }
    }
}

public func megabytes(_ bytes: Int64) -> String {
    String(format: "%.1f MB", Double(bytes) / 1_048_576)
}

/// Builds the requests in the plan's section 5. Nothing here touches the network.
public enum LaptopRequests {
    /// The upload limit in 5.3, used when the laptop has not said otherwise.
    public static let defaultMaxScanBytes: Int64 = 268_435_456

    public static let usdzContentType = "model/vnd.usdz+zip"

    /// `GET /api/hello` (5.1).
    public static func hello(_ pairing: Pairing) -> URLRequest {
        var request = URLRequest(url: url(pairing.base, path: "/api/hello", query: []))
        request.httpMethod = "GET"
        request.timeoutInterval = 5
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        return request
    }

    /// `POST /api/machines/<machine>/scans` (5.3). The body is the file's raw bytes,
    /// sent by the caller with an upload-from-file call so it is never chunked.
    public static func upload(_ scan: PendingScan, fileBytes: Int64, app: String,
                              maxBytes: Int64 = defaultMaxScanBytes) throws -> URLRequest {
        guard fileBytes >= 1 else { throw UploadError.emptyFile }
        guard fileBytes <= maxBytes else { throw UploadError.tooLarge(bytes: fileBytes, limit: maxBytes) }

        let query = [
            ("client_id", scan.clientID),
            ("at", timestamp(scan.capturedAt)),
            ("name", scan.fileName),
            ("app", app)
        ]
        let path = "/api/machines/\(scan.pairing.machineID)/scans"
        var request = URLRequest(url: url(scan.pairing.base, path: path, query: query))
        request.httpMethod = "POST"
        request.setValue(usdzContentType, forHTTPHeaderField: "Content-Type")
        request.setValue(String(fileBytes), forHTTPHeaderField: "Content-Length")
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        return request
    }

    /// ISO 8601, UTC, whole seconds: `2026-11-14T18:14:40Z`.
    public static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(identifier: "UTC")
        return formatter.string(from: date)
    }

    private static func url(_ base: URL, path: String, query: [(String, String)]) -> URL {
        var text = base.absoluteString
        while text.hasSuffix("/") { text.removeLast() }
        text += path
        if !query.isEmpty {
            text += "?" + query.map { "\($0.0)=\(encode($0.1))" }.joined(separator: "&")
        }
        // The path holds only a checked machine id and the query is fully escaped,
        // so this cannot fail for a pairing that came from `Pairing.parse`.
        return URL(string: text)!
    }

    /// Escapes everything except letters, digits and `-._~`, so `+`, `&`, `:` and
    /// spaces in a value cannot be misread by the receiver.
    private static func encode(_ value: String) -> String {
        var allowed = CharacterSet()
        allowed.insert(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }
}
