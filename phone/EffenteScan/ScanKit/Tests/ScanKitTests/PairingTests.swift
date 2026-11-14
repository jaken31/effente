import XCTest
@testable import ScanKit

final class PairingTests: XCTestCase {
    let good = "effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07&name=Conveyor%207"

    func testGoodLine() throws {
        let pairing = try Pairing.parse(good)
        XCTAssertEqual(pairing.base.absoluteString, "http://192.168.43.10:8000")
        XCTAssertEqual(pairing.machineID, "conveyor-07")
        XCTAssertEqual(pairing.name, "Conveyor 7")
        XCTAssertEqual(pairing.displayName, "Conveyor 7")
    }

    func testPastedLineWithSpacesAround() throws {
        XCTAssertEqual(try Pairing.parse("  \(good)\n"), try Pairing.parse(good))
    }

    func testNameIsOptional() throws {
        let pairing = try Pairing.parse("effente://scan?v=1&base=http%3A%2F%2F10.0.0.2%3A8000&machine=pump-1")
        XCTAssertNil(pairing.name)
        XCTAssertEqual(pairing.displayName, "pump-1")
    }

    func testTrailingSlashOnBaseIsDropped() throws {
        let pairing = try Pairing.parse("effente://scan?v=1&base=http%3A%2F%2F10.0.0.2%3A8000%2F&machine=pump-1")
        XCTAssertEqual(pairing.base.absoluteString, "http://10.0.0.2:8000")
    }

    func testWrongVersion() {
        let line = good.replacingOccurrences(of: "v=1", with: "v=2")
        XCTAssertThrowsError(try Pairing.parse(line)) { XCTAssertEqual($0 as? PairingError, .unsupportedVersion) }
        XCTAssertTrue(PairingError.unsupportedVersion.message.contains("Update the app"))
    }

    func testMissingVersion() {
        let line = good.replacingOccurrences(of: "v=1&", with: "")
        XCTAssertThrowsError(try Pairing.parse(line)) { XCTAssertEqual($0 as? PairingError, .unsupportedVersion) }
    }

    func testMissingBase() {
        let line = "effente://scan?v=1&machine=conveyor-07"
        XCTAssertThrowsError(try Pairing.parse(line)) { XCTAssertEqual($0 as? PairingError, .missingBase) }
    }

    func testBaseThatIsNotAWebAddress() {
        for base in ["ftp%3A%2F%2F10.0.0.2", "10.0.0.2%3A8000", "http%3A%2F%2F"] {
            let line = "effente://scan?v=1&base=\(base)&machine=conveyor-07"
            XCTAssertThrowsError(try Pairing.parse(line), base) { XCTAssertEqual($0 as? PairingError, .badBase) }
        }
    }

    func testBadMachine() {
        let tooLong = String(repeating: "a", count: 41)
        for machine in ["", "Conveyor-07", "conveyor_07", "conveyor%2007", "a%2Fb", tooLong] {
            let line = "effente://scan?v=1&base=http%3A%2F%2F10.0.0.2%3A8000&machine=\(machine)"
            XCTAssertThrowsError(try Pairing.parse(line), machine) { XCTAssertEqual($0 as? PairingError, .badMachine) }
        }
        let line = "effente://scan?v=1&base=http%3A%2F%2F10.0.0.2%3A8000"
        XCTAssertThrowsError(try Pairing.parse(line)) { XCTAssertEqual($0 as? PairingError, .badMachine) }
    }

    func testFortyCharacterMachineIsAllowed() throws {
        let machine = String(repeating: "a", count: 40)
        let line = "effente://scan?v=1&base=http%3A%2F%2F10.0.0.2%3A8000&machine=\(machine)"
        XCTAssertEqual(try Pairing.parse(line).machineID, machine)
    }

    func testOtherText() {
        for line in ["", "hello", "https://example.com", "effente://other?v=1", "http://192.168.43.10:8000"] {
            XCTAssertThrowsError(try Pairing.parse(line), line) { XCTAssertEqual($0 as? PairingError, .notAPairingLine) }
        }
    }

    func testSurvivesBeingSaved() throws {
        let pairing = try Pairing.parse(good)
        let saved = try JSONEncoder().encode(pairing)
        XCTAssertEqual(try JSONDecoder().decode(Pairing.self, from: saved), pairing)
    }
}
