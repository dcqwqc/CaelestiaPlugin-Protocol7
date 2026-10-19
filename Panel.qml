pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import Caelestia.Config
import qs.services
import qs.utils
import dcqwqc.protocol7.services as P7

// Protocol 7 presents ONLY a waveform. No hover expansion, status text,
// tooltip, retry badge, or second window. The transcription history remains
// persisted by the backend, without an unsolicited onscreen popover.
Item {
    id: root

    property real phase: 0

    readonly property var ghostJob: P7.ProtocolState.jobs.length > 0 ? P7.ProtocolState.jobs[0] : null
    readonly property bool panelDetached: !P7.ProtocolState.visible && ghostJob !== null
    readonly property real panelAnchorX: Number(ghostJob?.target?.x ?? NaN)
    readonly property real panelAnchorY: Number(ghostJob?.target?.y ?? NaN)
    readonly property string panelMonitorName: String(ghostJob?.target?.monitor ?? "")
    readonly property bool panelVisible: P7.ProtocolState.visible || ghostJob !== null

    // During recording/transcription the overlay is entirely click-through.
    // A result waiting for manual insertion accepts a click only on its wave.
    readonly property bool panelInputEnabled: panelDetached && String(ghostJob?.status ?? "") === "ready"
    readonly property bool panelTouchTargetEnabled: false
    readonly property bool panelTouchPinned: false
    readonly property bool panelOverFullscreen: Osk.inset <= 0
    readonly property bool panelLiftShadow: true
    readonly property real panelDeformAmount: 0.03
    readonly property int panelMotionDuration: 230

    implicitWidth: panelDetached ? 136 : 112
    implicitHeight: panelDetached ? 42 : 32

    Process {
        id: insertProcess
        property string jobId: ""
        command: ["python3", Paths.toLocalFile(Qt.resolvedUrl("control_ipc.py")),
                  "insert", jobId]
        running: false
        onExited: code => {
            if (code !== 0)
                console.warn("Protocol7 insertion was not completed");
        }
    }

    Timer {
        interval: 28
        repeat: true
        running: root.panelVisible
        onTriggered: root.phase += root.panelDetached ? 0.30 : 0.19
    }

    Item {
        id: wave
        anchors.centerIn: parent
        width: root.panelDetached ? 100 : 84
        height: root.panelDetached ? 30 : 20

        TapHandler {
            enabled: root.panelInputEnabled
            onTapped: {
                if (insertProcess.running || !root.ghostJob)
                    return;
                insertProcess.jobId = String(root.ghostJob.id);
                insertProcess.running = true;
            }
        }

        Row {
            anchors.centerIn: parent
            spacing: root.panelDetached ? 4 : 6

            Repeater {
                model: root.panelDetached ? 13 : 9

                Item {
                    required property int index
                    readonly property real centerIndex: root.panelDetached ? 6 : 4
                    readonly property real voiceWeight: Math.max(0.45, 1 - Math.abs(index - centerIndex) / centerIndex * 0.48)
                    readonly property real processingWave: 0.22 + 0.72 * (Math.sin(root.phase + index * 0.64) + 1) / 2
                    readonly property real liveTexture: 0.78 + 0.22 * (Math.sin(root.phase + index * 0.91) + 1) / 2
                    readonly property real liveLevel: Math.pow(Math.max(0, P7.ProtocolState.level), 0.72)
                    readonly property real amount: root.panelDetached
                        ? processingWave
                        : Math.max(0.055, Math.min(1, liveLevel * voiceWeight * liveTexture))

                    width: 4
                    height: wave.height

                    Rectangle {
                        x: 0
                        y: parent.height - height
                        width: parent.width
                        height: 4 + parent.amount * (parent.height - 4)
                        radius: 2
                        color: Colours.palette.m3primary
                        opacity: 0.94

                        Behavior on height {
                            NumberAnimation { duration: 26; easing.type: Easing.OutQuad }
                        }
                    }
                }
            }
        }
    }
}
