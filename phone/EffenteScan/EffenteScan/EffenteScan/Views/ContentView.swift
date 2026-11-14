/*
See the LICENSE.txt file for this sample’s licensing information.

Abstract:
The top-level app view.
*/

import SwiftUI
import os

private let logger = Logger(subsystem: EffenteScanApp.subsystem, category: "ContentView")

/// The root of the SwiftUI View graph.
struct ContentView: View {
    @Environment(AppDataModel.self) var appModel
    @Environment(ScanFlowModel.self) var flow

    // effente: the five screens. Pair, then Apple's capture and build, then send and done.
    // A built scan that the laptop does not have yet always comes first, so it is
    // still there after a failed send or after the app was closed.
    var body: some View {
        Group {
            if let scan = flow.stored {
                if scan.sentScanID != nil {
                    DoneView(scan: scan)
                } else {
                    SendView(scan: scan)
                }
            } else if appModel.state == .notSet {
                PairView()
            } else {
                PrimaryView()
            }
        }
            .onChange(of: appModel.state) { _, newState in
                if newState == .reconstructing { flow.captureFinished() }
            }
            .onOpenURL { url in
                flow.pair(line: url.absoluteString)
            }
            .onAppear(perform: {
                UIApplication.shared.isIdleTimerDisabled = true
            })
            .onDisappear(perform: {
                UIApplication.shared.isIdleTimerDisabled = false
            })
    }
}
