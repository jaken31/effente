/*
effente scan: what the app knows outside the capture itself. Which laptop it is
paired with, the scan waiting to be sent, and how the send is going.

Apple's AppDataModel still runs the capture and the build. This model takes over
when the USDZ file exists.
*/

import Foundation
import Observation
import ScanKit
import os

private let logger = Logger(subsystem: EffenteScanApp.subsystem, category: "ScanFlowModel")

/// A built scan and where its files are. Saved, so a scan that was not sent
/// survives the app being closed.
struct StoredScan: Codable, Equatable {
    var scan: PendingScan
    /// The capture folder's name inside the app's Documents folder.
    var folderName: String
    /// The model file's path inside the capture folder.
    var modelPath: String
    var photos: Int
    var buildSeconds: Double?
    /// Set when the laptop answered 200.
    var sentScanID: String?
    var duplicate = false
    var filesDeleted = false

    var folder: URL { URL.documentsDirectory.appendingPathComponent(folderName, isDirectory: true) }
    var modelFile: URL { folder.appendingPathComponent(modelPath) }
    var modelBytes: Int64? {
        let attributes = try? FileManager.default.attributesOfItem(atPath: modelFile.path)
        return (attributes?[.size] as? NSNumber)?.int64Value
    }
}

@MainActor
@Observable
final class ScanFlowModel {
    static let instance = ScanFlowModel()

    enum LaptopStatus: Equatable {
        case unknown
        case checking
        case found(Hello)
        case notFound(String)
    }

    enum UploadState: Equatable {
        case idle
        case sending(Double)
        case failed(String)
    }

    private(set) var pairing: Pairing?
    private(set) var laptop: LaptopStatus = .unknown
    private(set) var stored: StoredScan?
    private(set) var upload: UploadState = .idle
    /// Why the last QR code or pasted line was not accepted.
    var pairingProblem: String?

    private var captureFinishedAt: Date?
    private var checkNumber = 0
    private var sendWhenShown = false
    private let client: LaptopClient
    private let defaults = UserDefaults.standard
    private static let pairingKey = "effente.pairing"
    private static let scanKey = "effente.scan"

    private init() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        client = LaptopClient(app: "effente-scan \(version)")
        if let data = defaults.data(forKey: Self.pairingKey) {
            pairing = try? JSONDecoder().decode(Pairing.self, from: data)
        }
        if let data = defaults.data(forKey: Self.scanKey) {
            stored = try? JSONDecoder().decode(StoredScan.self, from: data)
        }
    }

    // MARK: Pairing

    /// Takes the line from the QR code or the paste box. Returns true if it was accepted.
    @discardableResult
    func pair(line: String) -> Bool {
        do {
            let new = try Pairing.parse(line)
            pairing = new
            pairingProblem = nil
            save(new, key: Self.pairingKey)
            // A scan that is still waiting goes to the laptop just paired with.
            if var waiting = stored, waiting.sentScanID == nil {
                waiting.scan.pairing = new
                setStored(waiting)
                if case .failed = upload { upload = .idle }
            }
            checkLaptop()
            return true
        } catch let error as PairingError {
            pairingProblem = error.message
        } catch {
            pairingProblem = "That code could not be read."
        }
        return false
    }

    func forgetPairing() {
        pairing = nil
        laptop = .unknown
        checkNumber += 1
        defaults.removeObject(forKey: Self.pairingKey)
    }

    /// Calls `GET /api/hello`. Tries a few times, because the first request can
    /// fail while iOS is still asking the person about the local network.
    func checkLaptop() {
        guard let pairing else { return }
        checkNumber += 1
        let number = checkNumber
        laptop = .checking
        Task {
            var reason = ""
            for attempt in 0..<3 {
                if attempt > 0 { try? await Task.sleep(for: .seconds(1.5)) }
                guard number == checkNumber else { return }
                do {
                    let hello = try await client.hello(pairing)
                    guard number == checkNumber else { return }
                    laptop = .found(hello)
                    return
                } catch let error as LaptopError {
                    reason = error.message
                    if case .unreachable = error { continue }
                    break
                } catch {
                    reason = error.localizedDescription
                    break
                }
            }
            guard number == checkNumber else { return }
            logger.log("Laptop not found: \(reason)")
            laptop = .notFound(reason)
        }
    }

    // MARK: From capture to a stored scan

    /// The capture ended and the build is starting.
    func captureFinished() {
        captureFinishedAt = Date()
    }

    /// The USDZ file exists. From here the scan is kept until the laptop has it.
    func modelBuilt(file: URL, captureFolder: URL, imagesFolder: URL) {
        guard let pairing else {
            logger.error("A model was built with no pairing.")
            return
        }
        let finished = captureFinishedAt ?? Date()
        let images = (try? FileManager.default.contentsOfDirectory(atPath: imagesFolder.path)) ?? []
        let photos = images.filter { $0.uppercased().hasSuffix(".HEIC") }.count
        let folderPath = captureFolder.standardizedFileURL.path
        var modelPath = file.standardizedFileURL.path
        if modelPath.hasPrefix(folderPath) { modelPath.removeFirst(folderPath.count) }
        while modelPath.hasPrefix("/") { modelPath.removeFirst() }

        let new = StoredScan(scan: PendingScan(pairing: pairing, capturedAt: finished),
                             folderName: captureFolder.lastPathComponent,
                             modelPath: modelPath,
                             photos: photos,
                             buildSeconds: captureFinishedAt.map { Date().timeIntervalSince($0) })
        captureFinishedAt = nil
        upload = .idle
        sendWhenShown = true
        setStored(new)
    }

    // MARK: Sending

    /// The Send screen calls this when it appears. A scan that was just built is sent at once.
    func sendIfJustBuilt() {
        guard sendWhenShown else { return }
        sendWhenShown = false
        send()
    }

    /// Sends the stored scan. Every call for the same scan carries the same `client_id`.
    func send() {
        guard let waiting = stored, waiting.sentScanID == nil else { return }
        if case .sending = upload { return }
        upload = .sending(0)
        var limit = LaptopRequests.defaultMaxScanBytes
        if case .found(let hello) = laptop { limit = hello.maxScanBytes }

        Task {
            do {
                let reply = try await client.send(waiting.scan, file: waiting.modelFile, maxBytes: limit) { fraction in
                    Task { @MainActor [weak self] in
                        if case .sending = self?.upload { self?.upload = .sending(fraction) }
                    }
                }
                guard var done = stored, done.scan.clientID == waiting.scan.clientID else { return }
                done.sentScanID = reply.scanID
                done.duplicate = reply.duplicate
                upload = .idle
                setStored(done)
            } catch {
                guard stored?.scan.clientID == waiting.scan.clientID else { return }
                upload = .failed(Self.reason(error))
                logger.log("Send failed: \(String(describing: error))")
            }
        }
    }

    private static func reason(_ error: Error) -> String {
        if let error = error as? LaptopError { return error.message }
        if let error = error as? UploadError { return error.message }
        if (error as NSError).domain == NSCocoaErrorDomain {
            return "The scan file could not be read. (\(error.localizedDescription))"
        }
        return error.localizedDescription
    }

    // MARK: After

    /// Removes the photos and the model of the stored scan from this phone.
    func deleteFiles() {
        guard var current = stored else { return }
        try? FileManager.default.removeItem(at: current.folder)
        current.filesDeleted = true
        setStored(current)
    }

    /// Back to the first screen. The files stay unless they were deleted.
    func scanAnother() {
        upload = .idle
        setStored(nil)
        if pairing != nil { checkLaptop() }
    }

    /// Gives up on a scan that was not sent, and removes its files.
    func discard() {
        if let current = stored { try? FileManager.default.removeItem(at: current.folder) }
        scanAnother()
    }

    private func setStored(_ new: StoredScan?) {
        stored = new
        if let new {
            save(new, key: Self.scanKey)
        } else {
            defaults.removeObject(forKey: Self.scanKey)
        }
    }

    private func save<Value: Encodable>(_ value: Value, key: String) {
        if let data = try? JSONEncoder().encode(value) {
            defaults.set(data, forKey: key)
        }
    }
}
