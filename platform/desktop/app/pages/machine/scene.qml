// The Machine page's 3D view: one GLB model, turned by dragging and zoomed by scrolling, with a pin
// drawn over each part. Loaded by view3d.py. Kept thin: which pins exist, their colours and what a
// click means are decided in Python.
//
// Coordinates. The model is scaled and moved to fit the window, so a click's point in the scene is not
// the point in the file. Every position that leaves this file (picked) or enters it (pins) is in the
// model's own coordinates, the GLB file's (docs/scan-and-log-plan.md, 5.4): modelRoot converts.
import QtQuick
import QtQuick3D
import QtQuick3D.Helpers
import QtQuick3D.AssetUtils

Item {
    id: root

    property url source
    // [{id, x, y, z, colour, ring, selected, label}], positions in the model's own coordinates
    property var pins: []
    property color background: "#101214"
    property color labelColour: "#e8e8e8"
    property color labelBackground: "#c0101214"
    property real pinSize: 14

    // "empty" (no file asked for), "loading", "ready" or "error"
    readonly property string status: loader.status === RuntimeLoader.Success ? "ready"
                                   : loader.status === RuntimeLoader.Error ? "error"
                                   : root.source.toString() === "" ? "empty" : "loading"
    readonly property string error: loader.errorString
    // The model's box in its own coordinates, {min: [x, y, z], max: [x, y, z]}: the scan's model.bounds.
    // Given by view3d.py, because the loader's own bounds stayed at zero for a loaded GLB (Qt 6.12,
    // Windows, 14 Nov). Without it the loader's bounds are tried, and the model is not fitted if they are empty.
    property var box: null
    onBoxChanged: fit()
    property int frames: 0          // frames drawn since the model loaded: a pick needs at least one
    property bool pickable: false   // every mesh has been marked, so a click can hit the model

    signal picked(real x, real y, real z)   // a click landed on the model, at this point of it
    signal missed()                         // a click landed beside the model
    signal pinClicked(string pinId)

    function markPickable(node) {
        if (node instanceof Model)
            node.pickable = true
        var kids = node.children
        for (var i = 0; i < kids.length; i++)
            markPickable(kids[i])
    }

    // Scale and move the model so its longest side is 100 units and its middle is at the origin,
    // then put the camera back where it shows all of it.
    function fit() {
        var lo = loader.bounds.minimum, hi = loader.bounds.maximum
        if (root.box && root.box.min && root.box.max) {
            lo = Qt.vector3d(root.box.min[0], root.box.min[1], root.box.min[2])
            hi = Qt.vector3d(root.box.max[0], root.box.max[1], root.box.max[2])
        }
        var size = Math.max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z)
        if (!(size > 0))
            return
        var s = 100 / size
        modelRoot.scale = Qt.vector3d(s, s, s)
        modelRoot.position = Qt.vector3d(-(lo.x + hi.x) / 2 * s, -(lo.y + hi.y) / 2 * s, -(lo.z + hi.z) / 2 * s)
        resetCamera()
    }

    function resetCamera() {
        orbitOrigin.eulerRotation = Qt.vector3d(-22, 32, 0)
        camera.position = Qt.vector3d(0, 0, 230)
        camera.eulerRotation = Qt.vector3d(0, 0, 0)
    }

    // Where a point of the model is on the screen: x and y in this item, z in front of the camera if > 0.
    function onScreen(x, y, z) {
        return view.mapFrom3DScene(modelRoot.mapPositionToScene(Qt.vector3d(x, y, z)))
    }

    // A click at (x, y) in this item: a pin if one is under it, else the model, else a miss.
    function click(x, y) {
        var best = -1, bestDistance = root.pinSize
        for (var i = 0; i < root.pins.length; i++) {
            var p = root.pins[i]
            var at = onScreen(p.x, p.y, p.z)
            if (at.z <= 0)
                continue
            var d = Math.hypot(at.x - x, at.y - y)
            if (d <= bestDistance) {
                best = i
                bestDistance = d
            }
        }
        if (best >= 0) {
            root.pinClicked(root.pins[best].id)
            return
        }
        var hit = view.pick(x, y)
        if (hit.objectHit) {
            var m = modelRoot.mapPositionFromScene(hit.scenePosition)
            root.picked(m.x, m.y, m.z)
        } else {
            root.missed()
        }
    }

    View3D {
        id: view
        anchors.fill: parent
        camera: camera

        environment: SceneEnvironment {
            clearColor: root.background
            backgroundMode: SceneEnvironment.Color
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.High
        }

        Node {
            id: orbitOrigin
            PerspectiveCamera {
                id: camera
                z: 230
                clipNear: 1
                clipFar: 5000
            }
        }

        DirectionalLight {
            eulerRotation: Qt.vector3d(-35, -40, 0)
            brightness: 1.1
            ambientColor: Qt.rgba(0.45, 0.45, 0.45, 1)
        }
        DirectionalLight {
            eulerRotation: Qt.vector3d(20, 140, 0)
            brightness: 0.5
        }

        Node {
            id: modelRoot
            RuntimeLoader {
                id: loader
                source: root.source
                onStatusChanged: {
                    root.frames = 0
                    root.pickable = false
                    if (status === RuntimeLoader.Success) {
                        root.markPickable(loader)
                        root.pickable = true
                        root.fit()
                    }
                }
                onBoundsChanged: root.fit()
            }
        }
    }

    OrbitCameraController {
        anchors.fill: parent
        origin: orbitOrigin
        camera: camera
    }

    TapHandler {
        acceptedButtons: Qt.LeftButton
        onTapped: (point) => root.click(point.position.x, point.position.y)
    }

    // Counts frames: view3d.py's FrameCounter calls this after each one is drawn.
    function frameDrawn() {
        if (root.status === "ready" && root.frames < 1000000)
            root.frames += 1
    }

    Repeater {
        model: root.pins
        Item {
            id: pin
            required property var modelData
            // Named so the binding runs again when the camera or the model moves.
            readonly property vector3d at: {
                camera.scenePosition; camera.sceneRotation; modelRoot.scale; modelRoot.position
                view.width; view.height
                return root.onScreen(modelData.x, modelData.y, modelData.z)
            }
            x: at.x
            y: at.y
            visible: at.z > 0

            Rectangle {
                readonly property real d: modelData.selected ? root.pinSize + 6 : root.pinSize
                width: d
                height: d
                radius: d / 2
                x: -d / 2
                y: -d / 2
                color: modelData.colour
                border.color: modelData.ring
                border.width: modelData.selected ? 3 : 1.5
            }
            Rectangle {
                visible: modelData.selected && modelData.label !== ""
                x: root.pinSize
                y: -height / 2
                width: label.implicitWidth + 12
                height: label.implicitHeight + 6
                radius: 4
                color: root.labelBackground
                Text {
                    id: label
                    anchors.centerIn: parent
                    text: modelData.label
                    color: root.labelColour
                    font.pixelSize: 12
                }
            }
        }
    }
}
