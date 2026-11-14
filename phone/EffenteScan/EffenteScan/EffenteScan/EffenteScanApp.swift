/*
See the LICENSE.txt file for this sample’s licensing information.

Abstract:
The single entry point of the app.
*/

import SwiftUI

@main
struct EffenteScanApp: App {
    static let subsystem: String = "com.effente.scan"

    // effente: start the on-phone log before anything else.
    init() {
        Diagnostics.start()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(AppDataModel.instance)
                .environment(ScanFlowModel.instance)
        }
    }
}
