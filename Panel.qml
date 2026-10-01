pragma ComponentBehavior: Bound

import QtQuick
import Caelestia.Config
import qs.services
import dcqwqc.protocol7.services as P7

// Tiny dictation HUD content. Caelestia owns the surface, background, shadow,
// edge join and slide motion; Protocol7 only supplies state and waveform pixels.
Item {
    id: root

    property real phase: 0

    readonly property bool panelVisible: P7.ProtocolState.visible
    readonly property bool panelInputEnabled: false
    readonly property bool panelOverFullscreen: true
    readonly property bool panelLiftShadow: true
    readonly property real panelDeformAmount: 0.03
    readonly property int panelMotionDuration: 180

    implicitWidth: 112
    implicitHeight: 32

    Timer {
        interval: 28
        repeat: true
        running: P7.ProtocolState.visible
        onTriggered: root.phase += P7.ProtocolState.processing ? 0.32 : 0.19
    }

    // Fixed bottom baseline: bars can only grow UP from this row. Keeping the
    // baseline explicit prevents hot reload/layout changes from visually
    // inverting the waveform again.
    Row {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 6
        height: 20
        spacing: 6

        Repeater {
            model: 9

            Item {
                required property int index

                readonly property real distance: Math.abs(index - 4) / 4
                readonly property real voiceWeight: Math.max(0.46, 1 - distance * 0.48)
                readonly property real processingWave: 0.22 + 0.72 * ((Math.sin(root.phase + index * 0.64) + 1) / 2)
                readonly property real liveTexture: 0.78 + 0.22 * ((Math.sin(root.phase + index * 0.91) + 1) / 2)
                readonly property real liveLevel: Math.pow(Math.max(0, P7.ProtocolState.level), 0.72)
                readonly property real amount: P7.ProtocolState.processing
                    ? processingWave
                    : Math.max(0.055, Math.min(1, liveLevel * voiceWeight * liveTexture))

                width: 4
                height: 20

                Rectangle {
                    width: parent.width
                    height: 3 + parent.amount * 17
                    x: 0
                    // Explicit y makes the bottom edge invariant: height changes
                    // move only the top edge, never the baseline.
                    y: parent.height - height
                    radius: 2
                    color: Colours.palette.m3primary
                    opacity: 0.9

                    Behavior on height {
                        NumberAnimation {
                            duration: 26
                            easing.type: Easing.OutQuad
                        }
                    }
                }
            }
        }
    }
}
