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

    // effente: the home screen with the kept scans, or Apple's capture and build
    // while a scan is being made. Pairing and sending happen on the home screen.
    var body: some View {
        Group {
            if appModel.state == .notSet {
                HomeView()
            } else {
                PrimaryView()
            }
        }
            .onChange(of: appModel.state) { _, newState in
                if newState == .reconstructing { flow.captureFinished() }
            }
            // effente: one dark look for the whole app, as on the laptop.
            .preferredColorScheme(.dark)
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
