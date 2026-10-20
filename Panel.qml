pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import Caelestia.Config
import qs.services
import qs.utils
import dcqwqc.protocol7.services as P7

// Detached processing is waveform-only. Recording-mode hover restores a
// compact transcription history anchored above the native bottom waveform.
// No processing labels, tooltips, or extra overlay window.
Item {
    id: root

    property real phase: 0
    property bool panelHostHovered: false
    property bool historyPinned: false
    property var historyEntries: []
    property string selectedHistoryTimestamp: ""

    readonly property var ghostJob: P7.ProtocolState.jobs.length > 0 ? P7.ProtocolState.jobs[0] : null
    readonly property bool panelDetached: !P7.ProtocolState.visible && ghostJob !== null
    readonly property bool waitingToSend: panelDetached && ghostJob?.sendQueued === true
    readonly property real panelAnchorX: Number(ghostJob?.target?.x ?? NaN)
    readonly property real panelAnchorY: Number(ghostJob?.target?.y ?? NaN)
    readonly property string panelMonitorName: String(ghostJob?.target?.monitor ?? "")
    readonly property bool panelVisible: P7.ProtocolState.visible || ghostJob !== null

    // Native input masking is essential for hover: allow pointer events over
    // the recording pill/history, but keep detached *processing* click-through.
    readonly property bool panelInputEnabled: !panelDetached || String(ghostJob?.status ?? "") === "ready"
    readonly property bool panelTouchTargetEnabled: !panelDetached
    readonly property bool panelTouchPinned: !panelDetached && historyPinned
    readonly property bool expanded: !panelDetached && panelVisible && (panelHostHovered || historyPinned)
    readonly property bool panelOverFullscreen: Osk.inset <= 0
    readonly property bool panelLiftShadow: true
    readonly property real panelDeformAmount: 0.03
    readonly property int panelMotionDuration: 230

    implicitWidth: panelDetached ? 136 : (expanded ? 376 : 112)
    implicitHeight: panelDetached ? 42 : (expanded ? 318 : 32)

    function dismissTouchPanel(): void {
        historyPinned = false;
    }

    onPanelVisibleChanged: {
        if (!panelVisible)
            historyPinned = false;
    }

    Behavior on implicitWidth { NumberAnimation { duration: 165; easing.type: Easing.OutCubic } }
    Behavior on implicitHeight { NumberAnimation { duration: 165; easing.type: Easing.OutCubic } }

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

    Process {
        id: pasteHistory
        command: ["python3", Paths.toLocalFile(Qt.resolvedUrl("history_actions.py")),
                  "paste", root.selectedHistoryTimestamp]
        running: false
    }

    FileView {
        id: historyFile
        path: Quickshell.env("HOME") + "/.config/protocol-7/history.json"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: {
            try {
                const loaded = JSON.parse(text());
                if (Array.isArray(loaded))
                    root.historyEntries = loaded.filter(e => e && typeof e.text === "string" &&
                        Number.isFinite(Number(e.timestamp))).slice(0, 100);
            } catch (e) {
                // Preserve the last valid snapshot during atomic writes.
            }
        }
    }

    Timer {
        interval: 28
        repeat: true
        running: root.panelVisible
        // Queued send has a more energetic but still organic rhythm.
        // Avoid the old high-frequency spikes that looked mechanical.
        onTriggered: root.phase += root.waitingToSend ? 0.38 : (root.panelDetached ? 0.32 : 0.19)
    }

    // The history is deliberately absent when detached. No status bubble
    // or tooltip can appear during background transcription.
    Item {
        id: historyPane
        visible: root.expanded
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: wave.top
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.topMargin: 12
        anchors.bottomMargin: 8

        Column {
            anchors.fill: parent
            spacing: 10

            Text {
                text: "Dictation history"
                font.pixelSize: 14
                font.weight: Font.DemiBold
                color: Colours.palette.m3onSurface
            }

            ListView {
                id: historyList
                width: parent.width
                height: parent.height - 28
                model: root.historyEntries
                spacing: 6
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                delegate: Rectangle {
                    id: historyEntry
                    required property var modelData
                    width: historyList.width
                    height: 57
                    radius: 11
                    color: historyHover.hovered
                        ? Colours.palette.m3surfaceContainerHighest
                        : Colours.palette.m3surfaceContainer

                    HoverHandler {
                        id: historyHover
                        cursorShape: Qt.PointingHandCursor
                    }

                    TapHandler {
                        onTapped: {
                            if (pasteHistory.running)
                                return;
                            root.selectedHistoryTimestamp = String(historyEntry.modelData.timestamp);
                            pasteHistory.running = true;
                        }
                    }

                    Text {
                        anchors.fill: parent
                        anchors.margins: 11
                        text: String(historyEntry.modelData.text)
                        color: Colours.palette.m3onSurface
                        font.pixelSize: 12
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        wrapMode: Text.Wrap
                    }
                }
            }
        }

        Text {
            anchors.centerIn: parent
            visible: root.historyEntries.length === 0
            text: "No dictations saved yet"
            color: Colours.palette.m3onSurfaceVariant
            font.pixelSize: 12
        }
    }

    Item {
        id: wave
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 6
        width: root.panelDetached ? 100 : 84
        height: root.panelDetached ? 30 : 20

        TapHandler {
            enabled: root.panelInputEnabled
            onTapped: {
                if (!root.panelDetached) {
                    root.historyPinned = !root.historyPinned;
                } else if (!insertProcess.running && root.ghostJob) {
                    insertProcess.jobId = String(root.ghostJob.id);
                    insertProcess.running = true;
                }
            }
        }

        Row {
            anchors.centerIn: parent
            spacing: root.waitingToSend ? 3 : (root.panelDetached ? 4 : 6)

            Repeater {
                model: root.waitingToSend ? 15 : (root.panelDetached ? 13 : 9)

                Item {
                    required property int index
                    readonly property real centerIndex: root.waitingToSend ? 7 : (root.panelDetached ? 6 : 4)
                    readonly property real voiceWeight: Math.max(0.45, 1 - Math.abs(index - centerIndex) / centerIndex * 0.48)
                    readonly property real processingWave: 0.22 + 0.72 * (Math.sin(root.phase + index * 0.64) + 1) / 2
                    readonly property real liveTexture: 0.78 + 0.22 * (Math.sin(root.phase + index * 0.91) + 1) / 2
                    readonly property real liveLevel: Math.pow(Math.max(0, P7.ProtocolState.level), 0.72)
                    readonly property real waveDrift: Math.sin(root.phase * 0.34 + index * 0.23) * 0.16
                    readonly property real queuedWave: 0.12 + 0.83 *
                        Math.pow((1 + Math.sin(root.phase + index * 0.88 + waveDrift)) / 2, 1.45) *
                        (0.86 + 0.14 * Math.sin(root.phase * 0.51 + index * 0.32))
                    readonly property real amount: root.waitingToSend ? queuedWave : root.panelDetached
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
                            NumberAnimation { duration: root.waitingToSend ? 65 : 26; easing.type: Easing.OutQuad }
                        }
                    }
                }
            }
        }
    }
}
