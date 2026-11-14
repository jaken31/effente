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

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let pairing = flow.pairing {
                        PairedCard(pairing: pairing)
                        startButtons
                    } else {
                        Text("Open New scan on the effente laptop. Then read its code here.")
                            .foregroundStyle(.secondary)
                    }

                    PairingEntry(title: flow.pairing == nil ? "Read the code on the laptop" : "Pair with another machine")

                    if !ObjectCaptureSession.isSupported {
                        Label("This phone cannot scan. Scanning needs an iPhone with a LiDAR scanner.",
                              systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(20)
            }
            .navigationTitle("effente scan")
        }
        .onAppear {
            if flow.pairing != nil, flow.laptop == .unknown { flow.checkLaptop() }
        }
    }

    @ViewBuilder
    private var startButtons: some View {
        switch flow.laptop {
        case .found:
            Button(action: { appModel.beginCapture() }, label: {
                Text("Start scan").frame(maxWidth: .infinity)
            })
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!ObjectCaptureSession.isSupported)
        case .notFound:
            VStack(alignment: .leading, spacing: 8) {
                Button(action: { flow.checkLaptop() }, label: {
                    Text("Look for the laptop again").frame(maxWidth: .infinity)
                })
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                Button(action: { appModel.beginCapture() }, label: {
                    Text("Scan without the laptop").frame(maxWidth: .infinity)
                })
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(!ObjectCaptureSession.isSupported)

                Text("The scan is kept on this phone. You can send it later or share the file.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        case .unknown, .checking:
            EmptyView()
        }
    }
}

/// The machine this phone is paired for, and whether the laptop answers.
struct PairedCard: View {
    @Environment(ScanFlowModel.self) var flow
    let pairing: Pairing

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(pairing.displayName)
                .font(.title2)
                .bold()
            if pairing.displayName != pairing.machineID {
                Text(pairing.machineID)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Text(pairing.base.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)

            Divider()

            switch flow.laptop {
            case .unknown, .checking:
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Looking for the laptop")
                }
            case .found:
                Label("Laptop found", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            case .notFound(let reason):
                Label("Laptop not found", systemImage: "wifi.exclamationmark")
                    .bold()
                Text(reason)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
    }
}

/// The two ways to give the app the pairing line: the camera, or the paste box.
struct PairingEntry: View {
    @Environment(ScanFlowModel.self) var flow
    let title: String
    /// Called after a line was accepted.
    var onPaired: () -> Void = {}

    @State private var showScanner = false
    @State private var pasted = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(action: {
                flow.pairingProblem = nil
                showScanner = true
            }, label: {
                Label(title, systemImage: "qrcode.viewfinder").frame(maxWidth: .infinity)
            })
            .buttonStyle(.bordered)
            .controlSize(.large)

            Text("Or paste the line under the code:")
                .font(.footnote)
                .foregroundStyle(.secondary)
            HStack {
                TextField("effente://scan?v=1&base=...", text: $pasted)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .submitLabel(.done)
                    .onSubmit(usePasted)
                Button("Use", action: usePasted)
                    .disabled(pasted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            if let problem = flow.pairingProblem, !showScanner {
                Text(problem)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .sheet(isPresented: $showScanner) {
            ScannerSheet(onPaired: {
                showScanner = false
                onPaired()
            })
        }
    }

    private func usePasted() {
        if flow.pair(line: pasted) {
            pasted = ""
            onPaired()
        }
    }
}

private struct ScannerSheet: View {
    @Environment(ScanFlowModel.self) var flow
    @Environment(\.dismiss) private var dismiss
    let onPaired: () -> Void

    var body: some View {
        NavigationStack {
            ZStack(alignment: .bottom) {
                QRScannerView { text in
                    if flow.pair(line: text) { onPaired() }
                }
                .ignoresSafeArea()

                Text(flow.pairingProblem ?? "Point the camera at the code on the laptop.")
                    .multilineTextAlignment(.center)
                    .padding(12)
                    .frame(maxWidth: .infinity)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                    .padding(20)
            }
            .navigationTitle("Read the code")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }
}
