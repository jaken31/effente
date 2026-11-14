/*
effente scan, screen 1: pair with the laptop. Read the QR code or paste the
line, then ask the laptop to say hello.
*/

import RealityKit
import ScanKit
import SwiftUI

struct PairView: View {
    @Environment(AppDataModel.self) var appModel
    @Environment(ScanFlowModel.self) var flow

    @State private var showScanner = false
    @State private var showPaste = false

    private var canScan: Bool { ObjectCaptureSession.isSupported }

    var body: some View {
        Screen(step: 1, title: flow.pairing == nil ? "Pair with the laptop" : "Ready to scan") {
            if let pairing = flow.pairing {
                MachinePanel(pairing: pairing)
            } else {
                VStack(alignment: .leading, spacing: 14) {
                    Image(systemName: "qrcode.viewfinder")
                        .font(.system(size: 44, weight: .light))
                        .foregroundStyle(Theme.accent)
                    Text("On the laptop, open the Machine page and choose New scan. Then read the code it shows.")
                        .font(.system(size: 16))
                        .foregroundStyle(Theme.soft)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .panel()
            }

            if showPaste || flow.pairing == nil {
                PasteBox(onPaired: { showPaste = false })
            }

            if let problem = flow.pairingProblem, !showScanner {
                StatusLine(kind: .bad, text: "Code not accepted")
                Hint(problem)
            }

            if !canScan {
                VStack(alignment: .leading, spacing: 8) {
                    StatusLine(kind: .bad, text: "This phone cannot scan")
                    Hint("Scanning needs an iPhone with a LiDAR scanner: a Pro model, iPhone 12 Pro or later.")
                }
                .panel()
            }
        } actions: {
            actions
        }
        .sheet(isPresented: $showScanner) {
            ScannerSheet(onPaired: { showScanner = false })
        }
        .onAppear {
            if flow.pairing != nil, flow.laptop == .unknown { flow.checkLaptop() }
        }
    }

    @ViewBuilder
    private var actions: some View {
        if flow.pairing == nil {
            Button(action: openScanner) {
                Label("Read the code", systemImage: "qrcode.viewfinder")
            }
            .buttonStyle(PrimaryButtonStyle())
        } else {
            switch flow.laptop {
            case .found:
                Button(action: { appModel.beginCapture() }, label: {
                    Label("Start scan", systemImage: "viewfinder")
                })
                .buttonStyle(PrimaryButtonStyle())
                .disabled(!canScan)
            case .notFound:
                Button(action: { flow.checkLaptop() }, label: {
                    Label("Look for the laptop again", systemImage: "arrow.clockwise")
                })
                .buttonStyle(PrimaryButtonStyle())
                Button("Scan without the laptop") { appModel.beginCapture() }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(!canScan)
            case .unknown, .checking:
                Button(action: {}, label: { Text("Looking for the laptop") })
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(true)
            }
            HStack(spacing: 24) {
                Button("Read another code", action: openScanner)
                Button(showPaste ? "Hide paste box" : "Paste a line") { showPaste.toggle() }
            }
            .buttonStyle(QuietButtonStyle())
        }
    }

    private func openScanner() {
        flow.pairingProblem = nil
        showScanner = true
    }
}

/// The machine this phone is paired for, and whether the laptop answers.
struct MachinePanel: View {
    @Environment(ScanFlowModel.self) var flow
    let pairing: Pairing

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                CapsLabel("Machine")
                Text(pairing.displayName)
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(Theme.text)
                if pairing.displayName != pairing.machineID {
                    Text(pairing.machineID)
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(Theme.muted)
                }
            }

            Rectangle().fill(Theme.border).frame(height: 1)

            VStack(alignment: .leading, spacing: 8) {
                CapsLabel("Laptop")
                switch flow.laptop {
                case .unknown, .checking:
                    StatusLine(kind: .busy, text: "Looking for the laptop")
                case .found:
                    StatusLine(kind: .good, text: "Laptop found")
                case .notFound(let reason):
                    StatusLine(kind: .bad, text: "Laptop not found")
                    Hint(reason)
                    Hint("You can still scan. The scan is kept on this phone, to send later or to share as a file.")
                }
                Text(pairing.base.absoluteString)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.muted)
            }
        }
        .panel()
    }
}

/// The fallback when the camera cannot read the code: paste the line under it.
struct PasteBox: View {
    @Environment(ScanFlowModel.self) var flow
    /// Called after a line was accepted.
    var onPaired: () -> Void = {}

    @State private var pasted = ""

    private var isEmpty: Bool {
        pasted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            CapsLabel("Or paste the line under the code")
            TextField("", text: $pasted, prompt: Text("effente://scan?v=1&base=...").foregroundStyle(Theme.muted),
                      axis: .vertical)
                .lineLimit(1...3)
                .font(.system(size: 14, design: .monospaced))
                .foregroundStyle(Theme.text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .padding(12)
                .background(Theme.raised, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Theme.border, lineWidth: 1))
            HStack(spacing: 10) {
                Button("Paste") {
                    if let text = UIPasteboard.general.string { pasted = text }
                }
                .buttonStyle(SecondaryButtonStyle())
                Button("Use this line") {
                    if flow.pair(line: pasted) {
                        pasted = ""
                        onPaired()
                    }
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(isEmpty)
            }
        }
        .panel()
    }
}

/// The camera, reading the QR code. Closes itself when a code is accepted.
struct ScannerSheet: View {
    @Environment(ScanFlowModel.self) var flow
    @Environment(\.dismiss) private var dismiss
    let onPaired: () -> Void

    var body: some View {
        ZStack {
            QRScannerView { text in
                if flow.pair(line: text) { onPaired() }
            }
            .ignoresSafeArea()

            // A frame to aim with.
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .strokeBorder(Color.white.opacity(0.9), lineWidth: 3)
                .frame(width: 250, height: 250)
                .shadow(color: .black.opacity(0.5), radius: 12)

            VStack {
                HStack {
                    Spacer()
                    Button(action: { dismiss() }, label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 15, weight: .bold))
                            .foregroundStyle(.white)
                            .frame(width: 40, height: 40)
                            .background(.ultraThinMaterial, in: Circle())
                    })
                    .accessibilityLabel("Close")
                }
                Spacer()
                HStack(spacing: 10) {
                    if flow.pairingProblem != nil {
                        Circle().fill(Theme.accent).frame(width: 9, height: 9)
                    }
                    Text(flow.pairingProblem ?? "Point the camera at the code on the laptop")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.leading)
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
                .frame(maxWidth: .infinity)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            }
            .padding(20)
        }
        .preferredColorScheme(.dark)
    }
}
