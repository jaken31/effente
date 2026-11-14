/*
effente scan, the home screen: the scans kept on this phone, the laptop this
phone is paired with if any, and the buttons to scan, pair and send.

Scanning needs no laptop. Pair when there is something to send, pick the scans,
and send those.
*/

import QuickLook
import RealityKit
import ScanKit
import SwiftUI

struct HomeView: View {
    @Environment(AppDataModel.self) var appModel
    @Environment(ScanFlowModel.self) var flow

    @State private var showScanner = false
    @State private var showPaste = false
    @State private var preview: URL?
    @State private var toDelete: StoredScan?
    @State private var toRename: StoredScan?
    @State private var newName = ""

    private var canScan: Bool { ObjectCaptureSession.isSupported }

    var body: some View {
        Screen(step: nil, title: "Scans") {
            LaptopPanel()

            if showPaste {
                PasteBox(onPaired: { showPaste = false })
            }
            if let problem = flow.pairingProblem, !showScanner {
                StatusLine(kind: .bad, text: "Code not accepted")
                Hint(problem)
            }

            if flow.scans.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Image(systemName: "cube.transparent")
                        .font(.system(size: 40, weight: .light))
                        .foregroundStyle(Theme.accent)
                    Text("No scans yet")
                        .font(.system(size: 18, weight: .semibold))
                    Hint("Scan a machine now. It is kept on this phone. Pair with the laptop later and send the scans you choose.")
                }
                .panel()
            } else {
                CapsLabel(flow.selectedScans.isEmpty ? "On this phone. Tap to pick what to send."
                                                     : "\(flow.selectedScans.count) picked to send")
                    .padding(.top, 4)
                ForEach(flow.scans) { scan in
                    ScanRow(scan: scan,
                            onView: { preview = scan.modelFile },
                            onRename: {
                                newName = scan.name
                                toRename = scan
                            },
                            onDelete: { toDelete = scan })
                }
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
        .quickLookPreview($preview)
        .sheet(isPresented: $showScanner) {
            ScannerSheet(onPaired: { showScanner = false })
        }
        .confirmationDialog("Delete this scan? Its photos and model are removed from this phone.",
                            isPresented: Binding(get: { toDelete != nil }, set: { if !$0 { toDelete = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let scan = toDelete { flow.delete(scan) }
                toDelete = nil
            }
        }
        .alert("Name this scan", isPresented: Binding(get: { toRename != nil }, set: { if !$0 { toRename = nil } })) {
            TextField("Name", text: $newName)
            Button("Save") {
                if let scan = toRename { flow.rename(scan, to: newName) }
                toRename = nil
            }
            Button("Cancel", role: .cancel) { toRename = nil }
        }
        .onAppear {
            if flow.pairing != nil, flow.laptop == .unknown { flow.checkLaptop() }
        }
    }

    @ViewBuilder
    private var actions: some View {
        let picked = flow.selectedScans.count
        if picked > 0 {
            if flow.isSending {
                Button(action: {}, label: { Text("Sending") })
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(true)
            } else if let pairing = flow.pairing {
                Button(action: { flow.sendSelected() }, label: {
                    Label("Send \(picked) to \(pairing.displayName)", systemImage: "arrow.up.circle")
                })
                .buttonStyle(PrimaryButtonStyle())
            } else {
                Button(action: openScanner) {
                    Label("Pair to send \(picked)", systemImage: "qrcode.viewfinder")
                }
                .buttonStyle(PrimaryButtonStyle())
            }
            Button(action: { appModel.beginCapture() }, label: {
                Label("New scan", systemImage: "viewfinder")
            })
            .buttonStyle(SecondaryButtonStyle())
            .disabled(!canScan || flow.isSending)
        } else {
            Button(action: { appModel.beginCapture() }, label: {
                Label("New scan", systemImage: "viewfinder")
            })
            .buttonStyle(PrimaryButtonStyle())
            .disabled(!canScan || flow.isSending)
        }
        HStack(spacing: 24) {
            Button(flow.pairing == nil ? "Pair with laptop" : "Read another code", action: openScanner)
            Button(showPaste ? "Hide paste box" : "Paste a line") { showPaste.toggle() }
        }
        .buttonStyle(QuietButtonStyle())
        .disabled(flow.isSending)
    }

    private func openScanner() {
        flow.pairingProblem = nil
        showScanner = true
    }
}

/// The laptop this phone is paired with and the machine scans will go to, or that there is none yet.
private struct LaptopPanel: View {
    @Environment(ScanFlowModel.self) var flow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let pairing = flow.pairing {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        CapsLabel("Sends to")
                        Text(pairing.displayName)
                            .font(.system(size: 22, weight: .semibold))
                            .foregroundStyle(Theme.text)
                    }
                    Spacer()
                    Button(action: { flow.forgetPairing() }, label: {
                        Text("Unpair")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(Theme.soft)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 32)
                            .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
                    })
                    .disabled(flow.isSending)
                }
                switch flow.laptop {
                case .unknown, .checking:
                    StatusLine(kind: .busy, text: "Looking for the laptop")
                case .found:
                    StatusLine(kind: .good, text: "Laptop found")
                case .notFound(let reason):
                    StatusLine(kind: .bad, text: "Laptop not found")
                    Hint(reason)
                    Button("Look again") { flow.checkLaptop() }
                        .buttonStyle(QuietButtonStyle())
                }
                Text(pairing.base.absoluteString)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.muted)
            } else {
                CapsLabel("Laptop")
                Text("Not paired")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Theme.text)
                Hint("You can scan without it. To send, open New scan on the laptop's Machine page and read its code here.")
            }
        }
        .panel()
    }
}

/// One scan in the list. Tap it to pick it for sending.
private struct ScanRow: View {
    @Environment(ScanFlowModel.self) var flow
    let scan: StoredScan
    let onView: () -> Void
    let onRename: () -> Void
    let onDelete: () -> Void

    var body: some View {
        let bytes = scan.modelBytes
        let picked = flow.isSelected(scan)
        HStack(alignment: .top, spacing: 14) {
            ZStack {
                Circle().strokeBorder(picked ? Theme.accent : Theme.borderStrong, lineWidth: 2)
                if picked {
                    Circle().fill(Theme.accent)
                    Image(systemName: "checkmark")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Theme.onAccent)
                }
            }
            .frame(width: 26, height: 26)
            .opacity(bytes == nil ? 0.3 : 1)
            .padding(.top, 2)

            VStack(alignment: .leading, spacing: 8) {
                Text(scan.name)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.text)
                Text(details(bytes))
                    .font(.system(size: 13))
                    .monospacedDigit()
                    .foregroundStyle(Theme.muted)
                status(bytes)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Menu {
                if bytes != nil {
                    Button(action: onView) { Label("View model", systemImage: "cube") }
                    ShareLink(item: scan.modelFile) { Label("Share file", systemImage: "square.and.arrow.up") }
                }
                Button(action: onRename) { Label("Rename", systemImage: "pencil") }
                Button(role: .destructive, action: onDelete) { Label("Delete", systemImage: "trash") }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.soft)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .disabled(flow.isSending)
            .accessibilityLabel("More for \(scan.name)")
        }
        .padding(.leading, 16)
        .padding(.trailing, 4)
        .padding(.vertical, 14)
        .background(picked ? Theme.raised : Theme.panel,
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .strokeBorder(picked ? Theme.accent.opacity(0.55) : Theme.border, lineWidth: 1))
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .onTapGesture { flow.toggle(scan) }
        .accessibilityAddTraits(picked ? [.isButton, .isSelected] : .isButton)
    }

    private func details(_ bytes: Int64?) -> String {
        var parts = ["\(scan.photos) photos"]
        if let bytes { parts.append(megabytes(bytes)) }
        if let seconds = scan.buildSeconds {
            let whole = Int(seconds.rounded())
            parts.append("built in " + (whole < 60 ? "\(whole) s" : "\(whole / 60) m \(whole % 60) s"))
        }
        return parts.joined(separator: "  ·  ")
    }

    @ViewBuilder
    private func status(_ bytes: Int64?) -> some View {
        if bytes == nil {
            SmallStatus(dot: Theme.accent, text: "The file is gone. Delete this scan.")
        } else {
            switch flow.uploads[scan.id] {
            case .waiting:
                SmallStatus(dot: Theme.soft, text: "Waiting to send")
            case .sending(let fraction):
                VStack(alignment: .leading, spacing: 6) {
                    SmallStatus(dot: Theme.accent, text: "Sending, \(Int((fraction * 100).rounded())) %")
                    AccentProgressBar(fraction: fraction)
                }
                .padding(.trailing, 12)
            case .failed(let reason):
                VStack(alignment: .leading, spacing: 4) {
                    SmallStatus(dot: Theme.accent, text: "Not sent. Still on this phone.")
                    Hint(reason)
                }
            case nil:
                if let sent = scan.sent {
                    SmallStatus(dot: Theme.text,
                                text: "Sent to \(sent.machineName), " + sent.at.formatted(.dateTime.day().month(.abbreviated).hour().minute()))
                } else {
                    SmallStatus(dot: Theme.dim, text: "Not sent yet")
                }
            }
        }
    }
}

private struct SmallStatus: View {
    let dot: Color
    let text: String

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(dot).frame(width: 7, height: 7)
            Text(text)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.soft)
                .fixedSize(horizontal: false, vertical: true)
        }
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
            CapsLabel("Paste the line under the code")
            TextField("", text: $pasted, prompt: Text(verbatim: "effente://scan?v=1&base=...").foregroundStyle(Theme.muted),
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
