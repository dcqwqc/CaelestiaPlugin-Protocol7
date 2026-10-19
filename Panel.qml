pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import Caelestia.Config
import qs.services
import qs.utils
import dcqwqc.protocol7.services as P7

// Native bottom dictation chip. Hovering expands the same Caelestia surface
// upwards into a transcription history; it is not a second overlay window.
Item {
    id: root

    property real phase: 0
    property bool panelHostHovered: false
    property bool historyPinned: false
    property var historyEntries: []
    property string pendingTimestamp: ""
    property string pasteStatus: ""
    property string pendingJobId: ""
    property string pendingJobAction: ""

    readonly property var ghostJob: P7.ProtocolState.jobs.length > 0 ? P7.ProtocolState.jobs[0] : null
    readonly property bool panelDetached: !P7.ProtocolState.visible && ghostJob !== null
    readonly property real panelAnchorX: Number(ghostJob?.target?.x ?? NaN)
    readonly property real panelAnchorY: Number(ghostJob?.target?.y ?? NaN)
    readonly property string panelMonitorName: String(ghostJob?.target?.monitor ?? "")
    readonly property bool panelVisible: P7.ProtocolState.visible || ghostJob !== null
    readonly property bool panelInputEnabled: true
    readonly property bool panelTouchTargetEnabled: true
    readonly property bool panelTouchPinned: historyPinned
    readonly property bool expanded: panelVisible && !panelDetached && (panelHostHovered || historyPinned)
    readonly property bool panelOverFullscreen: Osk.inset <= 0
    readonly property bool panelLiftShadow: true
    readonly property real panelDeformAmount: 0.03
    readonly property int panelMotionDuration: 180
    readonly property string pasteScript: Paths.toLocalFile(Qt.resolvedUrl("history_actions.py"))

    implicitWidth: panelDetached ? 176 : (expanded ? 382 : 112)
    implicitHeight: panelDetached ? 36 : (expanded ? 360 : 32)

    function dismissTouchPanel(): void {
        historyPinned = false;
    }

    function activateGhost(): void {
        if (!ghostJob || ghostCommand.running)
            return;
        const status = String(ghostJob.status ?? "");
        if (status !== "ready" && status !== "error" && status !== "processing" && status !== "queued")
            return;
        pendingJobId = String(ghostJob.id);
        pendingJobAction = status === "ready" ? "insert"
            : status === "error" && ghostJob.retryable ? "retry" : "cancel";
        ghostCommand.running = true;
    }

    function pasteEntry(timestamp: var): void {
        if (pasteProcess.running || !Number.isFinite(Number(timestamp)))
            return;
        pendingTimestamp = String(timestamp);
        pasteStatus = "Inserting…";
        pasteProcess.running = true;
    }

    onPanelVisibleChanged: {
        if (!panelVisible)
            historyPinned = false;
    }

    Behavior on implicitWidth {
        NumberAnimation { duration: 170; easing.type: Easing.OutCubic }
    }
    Behavior on implicitHeight {
        NumberAnimation { duration: 170; easing.type: Easing.OutCubic }
    }

    FileView {
        id: historyFile
        path: Quickshell.env("HOME") + "/.config/protocol-7/history.json"
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: {
            try {
                const value = JSON.parse(text());
                if (Array.isArray(value))
                    root.historyEntries = value.filter(x => x && typeof x.text === "string" && Number.isFinite(Number(x.timestamp))).slice(0, 100);
            } catch (e) {
                // Writes from the dictation process may briefly be incomplete.
                // Keep displaying the last valid snapshot instead of flickering.
            }
        }
    }

    Timer {
        interval: 1100
        repeat: true
        running: root.expanded
        onTriggered: historyFile.reload()
    }

    Process {
        id: ghostCommand
        command: ["python3", Paths.toLocalFile(Qt.resolvedUrl("control_ipc.py")),
                  root.pendingJobAction, root.pendingJobId]
        running: false
        onExited: code => {
            if (code !== 0)
                console.warn("Protocol7 Ghost Wave action failed:", root.pendingJobAction);
        }
    }

    Process {
        id: pasteProcess
        command: ["python3", root.pasteScript, "paste", root.pendingTimestamp]
        running: false
        stderr: SplitParser {
            onRead: data => {
                if (data.trim())
                    console.warn("Protocol7 history insert:", data.trim());
            }
        }
        onExited: code => {
            root.pasteStatus = code === 0 ? "Inserted" : "Couldn't insert";
            pasteStatusTimer.restart();
        }
    }

    Timer {
        id: pasteStatusTimer
        interval: 2200
        onTriggered: root.pasteStatus = ""
    }

    Timer {
        interval: 28
        repeat: true
        running: root.panelVisible
        onTriggered: root.phase += P7.ProtocolState.processing ? 0.32 : 0.19
    }

    Item {
        id: historyPane
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: waveRow.top
        anchors.margins: 13
        anchors.bottomMargin: 9
        visible: root.expanded
        opacity: root.expanded ? 1 : 0

        Column {
            anchors.fill: parent
            spacing: 10

            Item {
                width: parent.width
                height: 42

                Text {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    text: "Dictation history"
                    color: Colours.palette.m3onSurface
                }

                Text {
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    font.pixelSize: 11
                    text: root.pasteStatus || "Choose a transcription to insert"
                    color: root.pasteStatus === "Couldn't insert"
                        ? Colours.palette.m3error : Colours.palette.m3onSurfaceVariant
                }

                Text {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    font.pixelSize: 11
                    text: String(Math.min(root.historyEntries.length, 100)) + " saved"
                    color: Colours.palette.m3onSurfaceVariant
                }
            }

            ListView {
                id: historyList
                width: parent.width
                height: parent.height - 52
                clip: true
                spacing: 6
                boundsBehavior: Flickable.StopAtBounds
                model: root.historyEntries
                delegate: Rectangle {
                    id: entryRow
                    required property var modelData
                    width: historyList.width
                    height: 63
                    radius: 11
                    color: rowHover.hovered
                        ? Colours.palette.m3surfaceContainerHighest
                        : Colours.palette.m3surfaceContainer
                    border.width: rowHover.hovered ? 1 : 0
                    border.color: Colours.palette.m3primary

                    HoverHandler {
                        id: rowHover
                        cursorShape: Qt.PointingHandCursor
                    }
                    TapHandler {
                        onTapped: root.pasteEntry(entryRow.modelData.timestamp)
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        anchors.topMargin: 8
                        text: String(entryRow.modelData.text)
                        color: Colours.palette.m3onSurface
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                        elide: Text.ElideRight
                        maximumLineCount: 2
                    }

                    Text {
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.rightMargin: 12
                        anchors.bottomMargin: 5
                        color: Colours.palette.m3onSurfaceVariant
                        font.pixelSize: 10
                        text: Qt.formatDateTime(new Date(Number(entryRow.modelData.timestamp) * 1000), "dd MMM · hh:mm")
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
        }
    }

    // Live waveform stays attached to the bottom edge while the history rises
    // above it. On touchscreens tapping the chip pins/unpins history.
    Item {
        id: waveRow
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 6
        width: root.panelDetached ? 164 : 112
        height: 22

        TapHandler {
            onTapped: {
                if (root.panelDetached)
                    root.activateGhost();
                else
                    root.historyPinned = !root.historyPinned;
            }
        }

        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: root.panelDetached ? 8 : 0
            height: 20
            spacing: root.panelDetached ? 3 : 6

            Repeater {
                model: root.panelDetached ? 6 : 9

                Item {
                    required property int index
                    readonly property real distance: Math.abs(index - (root.panelDetached ? 2.5 : 4)) / (root.panelDetached ? 2.5 : 4)
                    readonly property real voiceWeight: Math.max(0.46, 1 - distance * 0.48)
                    readonly property real processingWave: 0.22 + 0.72 * ((Math.sin(root.phase + index * 0.64) + 1) / 2)
                    readonly property real liveTexture: 0.78 + 0.22 * ((Math.sin(root.phase + index * 0.91) + 1) / 2)
                    readonly property real liveLevel: Math.pow(Math.max(0, P7.ProtocolState.level), 0.72)
                    readonly property real amount: (P7.ProtocolState.processing || root.panelDetached)
                        ? processingWave
                        : Math.max(0.055, Math.min(1, liveLevel * voiceWeight * liveTexture))
                    width: root.panelDetached ? 3 : 4
                    height: 20

                    Rectangle {
                        width: parent.width
                        height: 3 + parent.amount * 17
                        x: 0
                        y: parent.height - height
                        radius: 2
                        color: Colours.palette.m3primary
                        opacity: 0.9

                        Behavior on height {
                            NumberAnimation { duration: 26; easing.type: Easing.OutQuad }
                        }
                    }
                }
            }
        }

        Text {
            visible: root.panelDetached
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 53
            color: Colours.palette.m3onSurface
            font.pixelSize: 11
            font.weight: Font.DemiBold
            text: {
                const status = String(root.ghostJob?.status ?? "");
                if (status === "ready") return "Insert here";
                if (status === "error") return root.ghostJob?.retryable ? "Retry" : "Dismiss";
                if (status === "inserting") return "Inserting…";
                const extra = Math.max(0, P7.ProtocolState.jobs.length - 1);
                return extra ? "Processing · +" + extra : "Processing…";
            }
        }
    }
}
