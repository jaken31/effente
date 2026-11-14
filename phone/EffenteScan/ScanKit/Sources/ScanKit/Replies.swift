import Foundation

/// The laptop's answer to `GET /api/hello` (5.1).
public struct Hello: Equatable, Sendable {
    public var contract: String
    public var maxScanBytes: Int64

    /// Reads the reply. Throws a `LaptopError` with a plain reason if this is not an effente receiver.
    public static func parse(status: Int, body: Data) throws -> Hello {
        guard let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            throw LaptopError.notEffente(status: status)
        }
        guard status == 200, object["ok"] as? Bool == true, object["service"] as? String == "effente" else {
            if let reason = object["error"] as? String, !reason.isEmpty {
                throw LaptopError.refused(reason)
            }
            throw LaptopError.notEffente(status: status)
        }
        let limit = (object["max_scan_bytes"] as? NSNumber)?.int64Value
        return Hello(contract: object["contract"] as? String ?? "",
                     maxScanBytes: limit ?? LaptopRequests.defaultMaxScanBytes)
    }
}

/// The laptop's answer to an upload (5.3).
public struct UploadReply: Equatable, Sendable {
    /// The id the laptop gave the scan. Opaque: shown, never read.
    public var scanID: String
    /// True when the laptop already had this `client_id`, so this send stored nothing new.
    public var duplicate: Bool
    /// `waiting`, `converting`, `ready` or `failed`, if the laptop said.
    public var modelStatus: String?

    public static func parse(status: Int, body: Data) throws -> UploadReply {
        let object = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        guard status == 200, let object, object["ok"] as? Bool == true else {
            if let reason = object?["error"] as? String, !reason.isEmpty {
                throw LaptopError.refused(reason)
            }
            throw LaptopError.notEffente(status: status)
        }
        let scan = object["scan"] as? [String: Any]
        let model = scan?["model"] as? [String: Any]
        return UploadReply(scanID: scan?["scan_id"] as? String ?? "",
                           duplicate: object["duplicate"] as? Bool ?? false,
                           modelStatus: model?["status"] as? String)
    }
}

public enum LaptopError: Error, Equatable, Sendable {
    /// The laptop answered and said no. The text is its own `error`.
    case refused(String)
    /// Something answered, but not an effente receiver that understood the request.
    case notEffente(status: Int)
    /// Nothing answered.
    case unreachable(String)

    public var message: String {
        switch self {
        case .refused(let reason):
            return "The laptop refused it: \(reason)"
        case .notEffente(let status):
            return "Something answered at that address, but not effente (HTTP \(status)). Is the effente receiver running, and up to date?"
        case .unreachable(let detail):
            return "Cannot reach the laptop. Check that the phone and the laptop are on the same WiFi, that effente is open on the laptop, and that this app is allowed to use the local network. (\(detail))"
        }
    }
}
