/*
effente scan: the look of our own screens. The colours, corner sizes and the
mark are the desktop app's (platform/desktop/app/theme/tokens.py), so the phone
and the laptop read as one product. Red is for shapes, never for text.
*/

import SwiftUI

enum Theme {
    static let background = Color(red: 0x0a / 255, green: 0x0a / 255, blue: 0x0a / 255)
    static let panel = Color(red: 0x14 / 255, green: 0x14 / 255, blue: 0x14 / 255)
    static let raised = Color(red: 0x1a / 255, green: 0x1a / 255, blue: 0x1a / 255)
    static let dim = Color(red: 0x2a / 255, green: 0x2a / 255, blue: 0x2a / 255)
    static let border = Color.white.opacity(0.07)
    static let borderStrong = Color.white.opacity(0.18)
    static let text = Color(red: 0xed / 255, green: 0xed / 255, blue: 0xed / 255)
    static let soft = Color(red: 0xa1 / 255, green: 0xa1 / 255, blue: 0xa1 / 255)
    static let muted = Color(red: 0x87 / 255, green: 0x87 / 255, blue: 0x87 / 255)
    static let accent = Color(red: 0xff / 255, green: 0x4d / 255, blue: 0x2e / 255)
    static let onAccent = Color(red: 0x12 / 255, green: 0x04 / 255, blue: 0x00 / 255)
    static let radius: CGFloat = 16
}

/// The effente mark: one beat of a vibration trace.
struct EffenteMark: Shape {
    func path(in rect: CGRect) -> Path {
        // The desktop logo's path, M3 12h4l3-8 4 16 3-8h4, on its 24 by 24 grid.
        let points: [(CGFloat, CGFloat)] = [(3, 12), (7, 12), (10, 4), (14, 20), (17, 12), (21, 12)]
        var path = Path()
        for (index, point) in points.enumerated() {
            let place = CGPoint(x: rect.minX + point.0 / 24 * rect.width,
                                y: rect.minY + point.1 / 24 * rect.height)
            if index == 0 { path.move(to: place) } else { path.addLine(to: place) }
        }
        return path
    }
}

/// The mark and the name, at the top of every screen of ours.
struct BrandBar: View {
    var body: some View {
        HStack(spacing: 10) {
            EffenteMark()
                .stroke(Theme.accent, style: StrokeStyle(lineWidth: 2.4, lineCap: .round, lineJoin: .round))
                .frame(width: 26, height: 26)
            Text("effente")
                .font(.system(size: 20, weight: .bold))
                .foregroundStyle(Theme.text)
            Text("scan")
                .font(.system(size: 20, weight: .regular))
                .foregroundStyle(Theme.muted)
            Spacer()
        }
    }
}

/// Where the person is in the five steps.
struct StepBar: View {
    static let names = ["Pair", "Scan", "Build", "Send", "Done"]
    /// 1 to 5.
    let step: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                ForEach(1...Self.names.count, id: \.self) { number in
                    Capsule()
                        .fill(number == step ? Theme.accent : (number < step ? Theme.soft : Theme.dim))
                        .frame(height: 4)
                }
            }
            CapsLabel("Step \(step) of \(Self.names.count): \(Self.names[step - 1])")
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Step \(step) of \(Self.names.count), \(Self.names[step - 1])")
    }
}

/// A small uppercase label, as on the desktop app's tables and panels.
struct CapsLabel: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.9)
            .foregroundStyle(Theme.muted)
    }
}

/// One of our screens: the brand, the steps, a title, the content, and the
/// main buttons held at the bottom where a thumb reaches them.
struct Screen<Content: View, Actions: View>: View {
    let step: Int
    let title: String
    @ViewBuilder var content: () -> Content
    @ViewBuilder var actions: () -> Actions

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                BrandBar()
                StepBar(step: step)
                Text(title)
                    .font(.system(size: 32, weight: .bold))
                    .foregroundStyle(Theme.text)
                    .padding(.top, 6)
                content()
            }
            .padding(.horizontal, 20)
            .padding(.top, 12)
            .padding(.bottom, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Theme.background.ignoresSafeArea())
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: 10) { actions() }
                .padding(.horizontal, 20)
                .padding(.top, 12)
                .padding(.bottom, 8)
                .frame(maxWidth: .infinity)
                .background(Theme.background.opacity(0.94))
        }
        .foregroundStyle(Theme.text)
        .tint(Theme.accent)
    }
}

extension View {
    /// A panel, as on the desktop app.
    func panel(padding: CGFloat = 18) -> some View {
        self
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .strokeBorder(Theme.border, lineWidth: 1))
    }
}

/// The one main action on a screen: a filled pill.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(Theme.onAccent.opacity(isEnabled ? 1 : 0.6))
            .frame(maxWidth: .infinity, minHeight: 54)
            .background(Theme.accent.opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : 0.4), in: Capsule())
            .contentShape(Capsule())
    }
}

/// Other actions: an outlined pill.
struct SecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .medium))
            .foregroundStyle(Theme.text.opacity(isEnabled ? 1 : 0.4))
            .frame(maxWidth: .infinity, minHeight: 50)
            .background(configuration.isPressed ? Theme.raised : Color.clear, in: Capsule())
            .overlay(Capsule().strokeBorder(Theme.borderStrong.opacity(isEnabled ? 1 : 0.4), lineWidth: 1))
            .contentShape(Capsule())
    }
}

/// Small text actions at the foot of a screen.
struct QuietButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .medium))
            .foregroundStyle(Theme.soft.opacity(isEnabled ? (configuration.isPressed ? 0.6 : 1) : 0.4))
            .frame(minHeight: 44)
            .contentShape(Rectangle())
    }
}

/// A status line: a dot, which may be red, and words, which never are.
struct StatusLine: View {
    enum Kind { case good, bad, busy }
    let kind: Kind
    let text: String

    var body: some View {
        HStack(spacing: 10) {
            switch kind {
            case .busy:
                ProgressView().controlSize(.small).tint(Theme.soft)
            case .good:
                Circle().fill(Theme.text).frame(width: 9, height: 9)
                    .shadow(color: Theme.text.opacity(0.6), radius: 5)
            case .bad:
                Circle().fill(Theme.accent).frame(width: 9, height: 9)
                    .shadow(color: Theme.accent.opacity(0.7), radius: 5)
            }
            Text(text)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.text)
        }
    }
}

/// A thin progress bar in the accent colour.
struct AccentProgressBar: View {
    /// 0 to 1.
    let fraction: Double

    var body: some View {
        GeometryReader { space in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.dim)
                Capsule().fill(Theme.accent)
                    .frame(width: max(8, space.size.width * min(max(fraction, 0), 1)))
                    .animation(.easeOut(duration: 0.25), value: fraction)
            }
        }
        .frame(height: 8)
    }
}

/// Small grey text under something, to explain it.
struct Hint: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.system(size: 13))
            .foregroundStyle(Theme.muted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
