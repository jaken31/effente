// swift-tools-version:5.9
// The parts of the phone scan app that need no camera: reading the QR line,
// building the requests to the laptop, and reading its replies.
// Kept in a package so `swift test` runs them on a Mac. The app itself only
// builds for a real iPhone.
import PackageDescription

let package = Package(
    name: "ScanKit",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "ScanKit", targets: ["ScanKit"])],
    targets: [
        .target(name: "ScanKit"),
        .testTarget(name: "ScanKitTests", dependencies: ["ScanKit"])
    ]
)
