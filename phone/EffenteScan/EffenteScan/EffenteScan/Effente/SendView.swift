/*
effente scan, screens 4 and 5: send the model to the laptop, and show that it
arrived. The file is kept until the laptop answers 200, and a retry never needs
a new scan.
*/

import QuickLook
import ScanKit
import SwiftUI

/// Screen 4. Shown while a built scan has not reached the laptop.
struct SendView: View {
    @Environment(ScanFlowModel.self) var flow
    let scan: StoredScan

    @State private var preview: URL?
    @State private var showPairing = false
    @State private var confirmDiscard = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ScanSummary(scan: scan)

                    if scan.modelBytes == nil {
                        Label("The scan file is no longer on this phone.", systemImage: "exclamationmark.triangle")
                    } else {
                        sendSection
                        ShareLink(item: scan.modelFile) {
                            Label("Share the file", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                        Text("If the laptop cannot be reached: AirDrop the file to a Mac or copy it by cable, then use Import scan file on the laptop.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)

                        Button(action: { preview = scan.modelFile }, label: {
                            Label("Look at the model", systemImage: "cube").frame(maxWidth: .infinity)
                        })
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                    }

                    Divider()

                    Button("Pair with the laptop again") { showPairing = true }
                        .disabled(isSending)
                    Button("Discard this scan", role: .destructive) { confirmDiscard = true }
                        .disabled(isSending)
                }
                .padding(20)
            }
            .navigationTitle("Send to laptop")
        }
        .quickLookPreview($preview)
        .sheet(isPresented: $showPairing) {
            NavigationStack {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Use this if the laptop's address changed. The scan is kept and goes to the laptop you pair with.")
                        .foregroundStyle(.secondary)
                    PairingEntry(title: "Read the code on the laptop", onPaired: { showPairing = false })
                    Spacer()
                }
                .padding(20)
                .navigationTitle("Pair again")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { showPairing = false }
                    }
                }
            }
        }
        .confirmationDialog("Discard this scan? Its photos and model are removed from this phone.",
                            isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { flow.discard() }
        }
        .onAppear { flow.sendIfJustBuilt() }
    }

    private var isSending: Bool {
        if case .sending = flow.upload { return true }
        return false
    }

    @ViewBuilder
    private var sendSection: some View {
        switch flow.upload {
        case .sending(let fraction):
            VStack(alignment: .leading, spacing: 8) {
                ProgressView(value: fraction)
                Text("Sending, \(Int((fraction * 100).rounded())) %")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text("Keep the app open until it is done.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        case .failed(let reason):
            VStack(alignment: .leading, spacing: 8) {
                Label("Not sent", systemImage: "exclamationmark.circle.fill").bold()
                Text(reason)
                    .font(.subheadline)
                Text("The scan is still on this phone.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button(action: { flow.send() }, label: {
                    Text("Try again").frame(maxWidth: .infinity)
                })
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        case .idle:
            Button(action: { flow.send() }, label: {
                Text("Send to laptop").frame(maxWidth: .infinity)
            })
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
    }
}

/// Screen 5. Shown once the laptop has the scan.
struct DoneView: View {
    @Environment(ScanFlowModel.self) var flow
    let scan: StoredScan

    @State private var confirmDelete = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Label("Sent to laptop", systemImage: "checkmark.circle.fill")
                        .font(.title2.bold())
                        .foregroundStyle(.green)
                    if scan.duplicate {
                        Text("The laptop already had this scan. Nothing was stored twice.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    ScanSummary(scan: scan)

                    Button(action: { flow.scanAnother() }, label: {
                        Text("Scan another").frame(maxWidth: .infinity)
                    })
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)

                    if scan.filesDeleted {
                        Text("The photos and the model were deleted from this phone.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ShareLink(item: scan.modelFile) {
                            Label("Share the file", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)

                        Button("Delete the photos and the model from this phone", role: .destructive) {
                            confirmDelete = true
                        }
                    }
                }
                .padding(20)
            }
            .navigationTitle("Done")
        }
        .confirmationDialog("Delete this scan's photos and model from this phone? The laptop keeps its copy.",
                            isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) { flow.deleteFiles() }
        }
    }
}

/// The machine, and the numbers of this scan: photos, build time, file size.
private struct ScanSummary: View {
    let scan: StoredScan

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(scan.scan.pairing.displayName)
                .font(.title2)
                .bold()
            Text(scan.scan.pairing.base.absoluteString)
                .font(.footnote.monospaced())
                .foregroundStyle(.secondary)
            Divider()
            row("Photos", "\(scan.photos)")
            if let seconds = scan.buildSeconds {
                row("Build time", Self.duration(seconds))
            }
            if let bytes = scan.modelBytes {
                row("File size", megabytes(bytes))
            }
            if let id = scan.sentScanID, !id.isEmpty {
                row("Scan id", id)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
    }

    private func row(_ name: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(name).foregroundStyle(.secondary)
            Spacer()
            Text(value).multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
    }

    private static func duration(_ seconds: Double) -> String {
        let whole = Int(seconds.rounded())
        return whole < 60 ? "\(whole) s" : "\(whole / 60) min \(whole % 60) s"
    }
}
