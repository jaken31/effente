/*
effente scan: what the app knows outside the capture itself. The scans kept on
this phone, which laptop it is paired with, and how each send is going.

Scanning needs no laptop. A scan is built and kept here. Pairing comes later,
and the person picks which scans go to the laptop.

Apple's AppDataModel still runs the capture and the build. This model takes over
when the USDZ file exists.
*/

import Foundation
import Observation
import ScanKit
import os

private let logger = Logger(subsystem: EffenteScanApp.subsystem, category: "ScanFlowModel")

/// A built scan kept on this phone, and where its files are.
struct StoredScan: Codable, Equatable, Identifiable {
    /// What the laptop said when it took this scan.
    struct Sent: Codable, Equatable {
        var scanID: String
        var machineID: String
        var machineName: String
        var at: Date
        var duplicate: Bool
    }

    var id: String
    /// What the person calls it. Shown only on this phone.
    var name: String
    /// When the capture finished.
    var capturedAt: Date
    /// The capture folder's name inside the app's Documents folder.
    var folderName: String
    /// The model file's path inside the capture folder.
    var modelPath: String
    var photos: Int
    var buildSeconds: Double?
    /// Sent with every upload of this scan to one machine, so a retry stores nothing twice.
    var clientID: String
    /// The machine that `clientID` was first used for. Another machine gets a fresh id.
    var clientMachine: String?
    /// The last time a laptop took it, if ever.
    var sent: Sent?

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
        /// Picked, and behind another scan in the queue.
        case waiting
        case sending(Double)
        case failed(String)
    }

    /// The scans on this phone, newest first.
    private(set) var scans: [StoredScan] = []
    /// The scans picked to send.
    private(set) var selection: Set<String> = []
    /// How the send of each scan is going. No entry means nothing is happening.
    private(set) var uploads: [String: UploadState] = [:]
    private(set) var isSending = false

    private(set) var pairing: Pairing?
    private(set) var laptop: LaptopStatus = .unknown
    /// Why the last QR code or pasted line was not accepted.
    var pairingProblem: String?

    private var captureFinishedAt: Date?
    private var checkNumber = 0
    private let client: LaptopClient
    private let defaults = UserDefaults.standard
    private static let pairingKey = "effente.pairing"
    private static let scansKey = "effente.scans"
    /// Where Apple's sample writes the model inside a capture folder.
    private static let modelPath = "Models/model-mobile.usdz"

    private init() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        client = LaptopClient(app: "effente-scan \(version)")
        if let data = defaults.data(forKey: Self.pairingKey) {
            pairing = try? JSONDecoder().decode(Pairing.self, from: data)
        }
        if let data = defaults.data(forKey: Self.scansKey),
           let saved = try? JSONDecoder().decode([StoredScan].self, from: data) {
            scans = saved
        }
        adoptUnlistedScans()
    }

    /// Lists any finished scan in the app's storage that the list does not know,
    /// for example one built by an earlier version of the app.
    private func adoptUnlistedScans() {
        let known = Set(scans.map(\.folderName))
        let names = (try? FileManager.default.contentsOfDirectory(atPath: URL.documentsDirectory.path)) ?? []
        var found: [StoredScan] = []
        for name in names where !known.contains(name) {
            let folder = URL.documentsDirectory.appendingPathComponent(name, isDirectory: true)
            let model = folder.appendingPathComponent(Self.modelPath)
            guard FileManager.default.fileExists(atPath: model.path) else { continue }
            let attributes = try? FileManager.default.attributesOfItem(atPath: model.path)
            let made = attributes?[.creationDate] as? Date ?? Date()
            found.append(StoredScan(id: UUID().uuidString, name: Self.defaultName(made), capturedAt: made,
                                    folderName: name, modelPath: Self.modelPath,
                                    photos: Self.countPhotos(folder.appendingPathComponent(CaptureFolderManager.imagesFolderName)),
                                    buildSeconds: nil, clientID: UUID().uuidString))
        }
        guard !found.isEmpty else { return }
        scans = (scans + found).sorted { $0.capturedAt > $1.capturedAt }
        saveScans()
    }

    private static func defaultName(_ date: Date) -> String {
        "Scan, " + date.formatted(.dateTime.day().month(.abbreviated).hour().minute())
    }

    private static func countPhotos(_ imagesFolder: URL) -> Int {
        let files = (try? FileManager.default.contentsOfDirectory(atPath: imagesFolder.path)) ?? []
        return files.filter { $0.uppercased().hasSuffix(".HEIC") }.count
    }

    // MARK: Pairing

    /// Takes the line from the QR code or the paste box. Returns true if it was accepted.
    @discardableResult
    func pair(line: String) -> Bool {
        do {
            let new = try Pairing.parse(line)
            pairing = new
            pairingProblem = nil
            if let data = try? JSONEncoder().encode(new) { defaults.set(data, forKey: Self.pairingKey) }
            // Earlier failures were about another laptop or address.
            if !isSending { uploads = [:] }
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
        guard !isSending else { return }
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

    // MARK: From capture to a kept scan

    /// The capture ended and the build is starting.
    func captureFinished() {
        captureFinishedAt = Date()
    }

    /// The USDZ file exists. The scan joins the list, picked, ready to send whenever there is a laptop.
    func modelBuilt(file: URL, captureFolder: URL, imagesFolder: URL) {
        let finished = captureFinishedAt ?? Date()
        let folderPath = captureFolder.standardizedFileURL.path
        var modelPath = file.standardizedFileURL.path
        if modelPath.hasPrefix(folderPath) { modelPath.removeFirst(folderPath.count) }
        while modelPath.hasPrefix("/") { modelPath.removeFirst() }

        let new = StoredScan(id: UUID().uuidString, name: Self.defaultName(finished), capturedAt: finished,
                             folderName: captureFolder.lastPathComponent, modelPath: modelPath,
                             photos: Self.countPhotos(imagesFolder),
                             buildSeconds: captureFinishedAt.map { Date().timeIntervalSince($0) },
                             clientID: UUID().uuidString)
        captureFinishedAt = nil
        scans.insert(new, at: 0)
        selection.insert(new.id)
        saveScans()
    }

    // MARK: Picking

    func isSelected(_ scan: StoredScan) -> Bool { selection.contains(scan.id) }

    func toggle(_ scan: StoredScan) {
        guard !isSending else { return }
        if selection.contains(scan.id) {
            selection.remove(scan.id)
        } else if scan.modelBytes != nil {
            selection.insert(scan.id)
        }
    }

    /// The picked scans that still have their file, in the order shown.
    var selectedScans: [StoredScan] {
        scans.filter { selection.contains($0.id) && $0.modelBytes != nil }
    }

    // MARK: Sending

    /// Sends the picked scans to the paired laptop, one after another.
    func sendSelected() {
        guard let pairing, !isSending else { return }
        let ids = selectedScans.map(\.id)
        guard !ids.isEmpty else { return }
        isSending = true
        for id in ids { uploads[id] = .waiting }
        var limit = LaptopRequests.defaultMaxScanBytes
        if case .found(let hello) = laptop { limit = hello.maxScanBytes }

        Task {
            for id in ids { await sendOne(id, to: pairing, maxBytes: limit) }
            isSending = false
            // If it went through, the laptop is there, whatever the last hello said.
            if scans.contains(where: { ids.contains($0.id) && $0.sent != nil }), case .notFound = laptop {
                checkLaptop()
            }
        }
    }

    private func sendOne(_ id: String, to pairing: Pairing, maxBytes: Int64) async {
        guard var scan = scans.first(where: { $0.id == id }) else { return }
        // One client_id per scan and machine: a retry is safe, and the same scan
        // can still go to a second machine as a new upload.
        if let machine = scan.clientMachine, machine != pairing.machineID {
            scan.clientID = UUID().uuidString
        }
        scan.clientMachine = pairing.machineID
        replace(scan)
        uploads[id] = .sending(0)

        let pending = PendingScan(pairing: pairing, capturedAt: scan.capturedAt, clientID: scan.clientID)
        do {
            let reply = try await client.send(pending, file: scan.modelFile, maxBytes: maxBytes) { fraction in
                Task { @MainActor [weak self] in
                    if case .sending = self?.uploads[id] { self?.uploads[id] = .sending(fraction) }
                }
            }
            scan.sent = StoredScan.Sent(scanID: reply.scanID, machineID: pairing.machineID,
                                        machineName: pairing.displayName, at: Date(), duplicate: reply.duplicate)
            replace(scan)
            uploads[id] = nil
            selection.remove(id)
        } catch {
            uploads[id] = .failed(Self.reason(error))
            logger.log("Send failed: \(String(describing: error))")
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

    // MARK: Keeping the list

    func rename(_ scan: StoredScan, to name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, var current = scans.first(where: { $0.id == scan.id }) else { return }
        current.name = trimmed
        replace(current)
    }

    /// Removes a scan's photos and model from this phone, and the scan from the list.
    func delete(_ scan: StoredScan) {
        guard !isSending else { return }
        try? FileManager.default.removeItem(at: scan.folder)
        scans.removeAll { $0.id == scan.id }
        selection.remove(scan.id)
        uploads[scan.id] = nil
        saveScans()
    }

    private func replace(_ scan: StoredScan) {
        guard let index = scans.firstIndex(where: { $0.id == scan.id }) else { return }
        scans[index] = scan
        saveScans()
    }

    private func saveScans() {
        if let data = try? JSONEncoder().encode(scans) {
            defaults.set(data, forKey: Self.scansKey)
        }
    }
}
