import XCTest
@testable import ScanKit

/// Stands in for the network. Remembers every request and answers from a list.
final class StubTransport: ScanTransport, @unchecked Sendable {
    var requests: [URLRequest] = []
    var uploadedFiles: [URL] = []
    var answers: [Result<(Data, Int), Error>] = []

    private func next() throws -> (Data, Int) {
        guard !answers.isEmpty else { return (Data("{\"ok\": true}".utf8), 200) }
        return try answers.removeFirst().get()
    }

    func send(_ request: URLRequest) async throws -> (Data, Int) {
        requests.append(request)
        return try next()
    }

    func upload(_ request: URLRequest, file: URL,
                progress: @escaping @Sendable (Double) -> Void) async throws -> (Data, Int) {
        requests.append(request)
        uploadedFiles.append(file)
        progress(1)
        return try next()
    }
}

final class UploadTests: XCTestCase {
    let pairing = Pairing(base: URL(string: "http://192.168.43.10:8000")!, machineID: "conveyor-07", name: "Conveyor 7")
    // 2026-11-14T18:14:40Z
    let capturedAt = Date(timeIntervalSince1970: 1_794_680_080)
    var file: URL!

    override func setUpWithError() throws {
        file = FileManager.default.temporaryDirectory.appendingPathComponent("scankit-\(UUID().uuidString).usdz")
        try Data("PK\u{03}\u{04}not a real scan".utf8).write(to: file)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: file)
    }

    func query(_ request: URLRequest) -> [String: String] {
        let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    func testHelloRequest() {
        let request = LaptopRequests.hello(pairing)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.absoluteString, "http://192.168.43.10:8000/api/hello")
    }

    func testUploadRequest() throws {
        let scan = PendingScan(pairing: pairing, capturedAt: capturedAt,
                               clientID: "6F9619FF-8B86-D011-B42D-00C04FC964FF")
        let request = try LaptopRequests.upload(scan, fileBytes: 18_234_112, app: "effente-scan 0.1")

        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString,
                       "http://192.168.43.10:8000/api/machines/conveyor-07/scans"
                       + "?client_id=6F9619FF-8B86-D011-B42D-00C04FC964FF"
                       + "&at=2026-11-14T18%3A14%3A40Z&name=scan.usdz&app=effente-scan%200.1")
        XCTAssertEqual(request.url?.path, "/api/machines/conveyor-07/scans")
        XCTAssertEqual(query(request), [
            "client_id": "6F9619FF-8B86-D011-B42D-00C04FC964FF",
            "at": "2026-11-14T18:14:40Z",
            "name": "scan.usdz",
            "app": "effente-scan 0.1"
        ])
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "model/vnd.usdz+zip")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Length"), "18234112")
        XCTAssertNil(request.httpBody, "the body is the file, sent by the transport")
    }

    func testQueryValuesAreFullyEscaped() throws {
        var scan = PendingScan(pairing: pairing, capturedAt: capturedAt)
        scan.fileName = "a+b & c=d.usdz"
        let request = try LaptopRequests.upload(scan, fileBytes: 10, app: "x")
        XCTAssertTrue(request.url!.absoluteString.contains("name=a%2Bb%20%26%20c%3Dd.usdz"))
        XCTAssertEqual(query(request)["name"], "a+b & c=d.usdz")
    }

    func testFreshClientIDFitsTheContract() {
        let one = PendingScan(pairing: pairing, capturedAt: capturedAt)
        let two = PendingScan(pairing: pairing, capturedAt: capturedAt)
        XCTAssertNotEqual(one.clientID, two.clientID, "one per scan")
        XCTAssertTrue((8...64).contains(one.clientID.count))
        XCTAssertTrue(one.clientID.unicodeScalars.allSatisfy {
            CharacterSet.alphanumerics.contains($0) && $0.isASCII || $0 == "-"
        })
    }

    func testSizeLimits() {
        let scan = PendingScan(pairing: pairing, capturedAt: capturedAt)
        XCTAssertThrowsError(try LaptopRequests.upload(scan, fileBytes: 0, app: "x")) {
            XCTAssertEqual($0 as? UploadError, .emptyFile)
        }
        XCTAssertNoThrow(try LaptopRequests.upload(scan, fileBytes: 268_435_456, app: "x"))
        XCTAssertThrowsError(try LaptopRequests.upload(scan, fileBytes: 268_435_457, app: "x")) {
            XCTAssertEqual($0 as? UploadError, .tooLarge(bytes: 268_435_457, limit: 268_435_456))
        }
        XCTAssertThrowsError(try LaptopRequests.upload(scan, fileBytes: 11, app: "x", maxBytes: 10))
    }

    func testRetryReusesTheClientID() async throws {
        let stub = StubTransport()
        stub.answers = [
            .failure(URLError(.networkConnectionLost)),
            .success((Data(#"{"ok": true, "duplicate": false, "scan": {"scan_id": "scan-1", "model": {"status": "waiting"}}}"#.utf8), 200))
        ]
        let client = LaptopClient(transport: stub, app: "effente-scan 0.1")
        let scan = PendingScan(pairing: pairing, capturedAt: capturedAt)

        do {
            _ = try await client.send(scan, file: file)
            XCTFail("the first send should fail")
        } catch let error as LaptopError {
            guard case .unreachable = error else { return XCTFail("\(error)") }
        }
        let reply = try await client.send(scan, file: file)

        XCTAssertEqual(reply, UploadReply(scanID: "scan-1", duplicate: false, modelStatus: "waiting"))
        XCTAssertEqual(stub.requests.count, 2)
        XCTAssertEqual(stub.requests[0].url, stub.requests[1].url)
        XCTAssertEqual(query(stub.requests[1])["client_id"], scan.clientID)
        XCTAssertEqual(stub.uploadedFiles, [file, file])
        let size = try XCTUnwrap(stub.requests[0].value(forHTTPHeaderField: "Content-Length"))
        XCTAssertEqual(Int(size), try Data(contentsOf: file).count)
    }

    func testPendingScanKeepsItsClientIDWhenSaved() throws {
        let scan = PendingScan(pairing: pairing, capturedAt: capturedAt)
        let restored = try JSONDecoder().decode(PendingScan.self, from: JSONEncoder().encode(scan))
        XCTAssertEqual(restored.clientID, scan.clientID)
    }

    func testRefusalShowsTheLaptopsOwnWords() async {
        let stub = StubTransport()
        stub.answers = [.success((Data(#"{"ok": false, "error": "not a USDZ or GLB file"}"#.utf8), 400))]
        let client = LaptopClient(transport: stub, app: "x")
        do {
            _ = try await client.send(PendingScan(pairing: pairing, capturedAt: capturedAt), file: file)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? LaptopError, .refused("not a USDZ or GLB file"))
            XCTAssertTrue((error as? LaptopError)?.message.contains("not a USDZ or GLB file") ?? false)
        }
    }

    func testDuplicateReply() throws {
        let body = Data(#"{"ok": true, "duplicate": true, "scan": {"scan_id": "scan-20261114181502-3fa9"}}"#.utf8)
        let reply = try UploadReply.parse(status: 200, body: body)
        XCTAssertEqual(reply, UploadReply(scanID: "scan-20261114181502-3fa9", duplicate: true, modelStatus: nil))
    }

    func testReplyThatIsNotJSON() {
        XCTAssertThrowsError(try UploadReply.parse(status: 502, body: Data("<html>".utf8))) {
            XCTAssertEqual($0 as? LaptopError, .notEffente(status: 502))
        }
    }

    func testHelloReply() throws {
        let body = Data(#"{"ok": true, "service": "effente", "contract": "3.4", "now": "2026-11-14T18:15:02Z", "max_scan_bytes": 268435456}"#.utf8)
        XCTAssertEqual(try Hello.parse(status: 200, body: body), Hello(contract: "3.4", maxScanBytes: 268_435_456))
    }

    func testHelloFromSomethingElse() {
        for (status, text) in [(200, #"{"ok": true}"#), (404, "Not found"), (200, #"{"ok": true, "service": "other"}"#)] {
            XCTAssertThrowsError(try Hello.parse(status: status, body: Data(text.utf8)), text) {
                XCTAssertEqual($0 as? LaptopError, .notEffente(status: status))
            }
        }
    }

    func testHelloFromAnOldReceiver() {
        // Today's receiver answers an unknown route with a 400 and an error text.
        let body = Data(#"{"ok": false, "error": "not built yet"}"#.utf8)
        XCTAssertThrowsError(try Hello.parse(status: 400, body: body)) {
            XCTAssertEqual($0 as? LaptopError, .refused("not built yet"))
        }
    }

    func testHelloWhenNothingAnswers() async {
        let stub = StubTransport()
        stub.answers = [.failure(URLError(.cannotConnectToHost))]
        do {
            _ = try await LaptopClient(transport: stub, app: "x").hello(pairing)
            XCTFail("should throw")
        } catch let error as LaptopError {
            guard case .unreachable = error else { return XCTFail("\(error)") }
        } catch {
            XCTFail("\(error)")
        }
    }
}
