import Foundation

/// The network, as far as this package needs it. The tests put a stub here.
public protocol ScanTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, Int)
    /// Sends `file` as the request's body. `progress` gets a fraction from 0 to 1.
    func upload(_ request: URLRequest, file: URL,
                progress: @escaping @Sendable (Double) -> Void) async throws -> (Data, Int)
}

/// Talks to the laptop named in a pairing, and to nothing else.
public struct LaptopClient: Sendable {
    public var transport: ScanTransport
    /// Sent as `app`, for example `effente-scan 0.1`.
    public var app: String

    public init(transport: ScanTransport = URLSessionTransport(), app: String) {
        self.transport = transport
        self.app = app
    }

    public func hello(_ pairing: Pairing) async throws -> Hello {
        let (body, status) = try await run { try await transport.send(LaptopRequests.hello(pairing)) }
        return try Hello.parse(status: status, body: body)
    }

    /// Sends the scan file. Safe to call again for the same `PendingScan` after a failure.
    public func send(_ scan: PendingScan, file: URL,
                     maxBytes: Int64 = LaptopRequests.defaultMaxScanBytes,
                     progress: @escaping @Sendable (Double) -> Void = { _ in }) async throws -> UploadReply {
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        let bytes = (attributes[.size] as? NSNumber)?.int64Value ?? 0
        let request = try LaptopRequests.upload(scan, fileBytes: bytes, app: app, maxBytes: maxBytes)
        let (body, status) = try await run { try await transport.upload(request, file: file, progress: progress) }
        return try UploadReply.parse(status: status, body: body)
    }

    private func run(_ call: () async throws -> (Data, Int)) async throws -> (Data, Int) {
        do {
            return try await call()
        } catch let error as URLError {
            throw LaptopError.unreachable(error.localizedDescription)
        }
    }
}

/// The real network. One request at a time is all the app needs.
public final class URLSessionTransport: ScanTransport {
    private let session: URLSession

    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 30 * 60
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        session = URLSession(configuration: configuration)
    }

    public func send(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await session.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    public func upload(_ request: URLRequest, file: URL,
                       progress: @escaping @Sendable (Double) -> Void) async throws -> (Data, Int) {
        let (data, response) = try await session.upload(for: request, fromFile: file,
                                                        delegate: ProgressDelegate(progress))
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }
}

private final class ProgressDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    private let progress: @Sendable (Double) -> Void

    init(_ progress: @escaping @Sendable (Double) -> Void) {
        self.progress = progress
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }
        progress(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
    }
}
