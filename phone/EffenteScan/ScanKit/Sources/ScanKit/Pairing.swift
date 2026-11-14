import Foundation

/// What the QR code on the laptop holds (docs/scan-and-log-plan.md, 5.2):
///
///     effente://scan?v=1&base=http%3A%2F%2F192.168.43.10%3A8000&machine=conveyor-07&name=Conveyor%207
public struct Pairing: Codable, Equatable, Sendable {
    /// The receiver's address, with no trailing slash. Every request goes here and nowhere else.
    public var base: URL
    /// The machine the scan is for.
    public var machineID: String
    /// What to show the person. Optional in the QR line.
    public var name: String?

    public init(base: URL, machineID: String, name: String? = nil) {
        self.base = base
        self.machineID = machineID
        self.name = name
    }

    /// The machine's name if the laptop gave one, else its id.
    public var displayName: String {
        if let name, !name.isEmpty { return name }
        return machineID
    }

    /// The only version of the QR line this app reads.
    public static let version = "1"

    /// Reads the line from the QR code, or the same line pasted by hand.
    public static func parse(_ line: String) throws -> Pairing {
        let text = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text),
              parts.scheme?.lowercased() == "effente",
              parts.host?.lowercased() == "scan" else {
            throw PairingError.notAPairingLine
        }
        let items = parts.queryItems ?? []
        func value(_ name: String) -> String? {
            items.first(where: { $0.name == name })?.value
        }

        guard value("v") == version else {
            throw PairingError.unsupportedVersion
        }

        guard var baseText = value("base"), !baseText.isEmpty else {
            throw PairingError.missingBase
        }
        while baseText.hasSuffix("/") { baseText.removeLast() }
        guard let base = URL(string: baseText),
              let scheme = base.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = base.host, !host.isEmpty else {
            throw PairingError.badBase
        }

        guard let machine = value("machine"), isMachineID(machine) else {
            throw PairingError.badMachine
        }

        let name = value("name")
        return Pairing(base: base, machineID: machine, name: (name?.isEmpty ?? true) ? nil : name)
    }

    /// Lowercase letters, digits and `-`, 1 to 40 characters.
    public static func isMachineID(_ text: String) -> Bool {
        guard (1...40).contains(text.unicodeScalars.count) else { return false }
        return text.unicodeScalars.allSatisfy { scalar in
            ("a"..."z").contains(scalar) || ("0"..."9").contains(scalar) || scalar == "-"
        }
    }
}

public enum PairingError: Error, Equatable, Sendable {
    case notAPairingLine
    case unsupportedVersion
    case missingBase
    case badBase
    case badMachine

    /// A plain sentence for the person holding the phone.
    public var message: String {
        switch self {
        case .notAPairingLine:
            return "This is not an effente scan code. Use the code on the laptop's New scan screen."
        case .unsupportedVersion:
            return "This code is from a different version of effente. Update the app."
        case .missingBase:
            return "The code does not say where the laptop is."
        case .badBase:
            return "The laptop address in the code is not a web address."
        case .badMachine:
            return "The machine id in the code is not valid."
        }
    }
}
