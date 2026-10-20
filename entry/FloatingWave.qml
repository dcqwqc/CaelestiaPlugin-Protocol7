pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Caelestia.Config
import qs.services
import qs.utils
import dcqwqc.protocol7.services as P7

// This window is intentionally independent of Caelestia's edge-shell BlobGroup.
// A small Wayland layer surface appears AT ITS TARGET. It never travels from
// the bottom edge and never inherits the dock's SDF clipping or retraction.
Scope {
    id: root

    property real phase: 0
    property string floatingJobId: ""
    readonly property var ghostJobs: P7.ProtocolState.jobs

    onGhostJobsChanged: {
        const rows = ghostJobs ?? [];
        if (!rows.length) {
            floatingJobId = "";
            return;
        }
        const active = rows.find(j => ["queued", "processing", "inserting"].includes(j.status));
        if (active && String(active.id) !== floatingJobId) {
            floatingJobId = String(active.id);
            return;
        }
        if (!rows.some(j => String(j.id) === floatingJobId))
            floatingJobId = String(rows[0].id);
    }

    readonly property var ghostJob: ghostJobs.find(j => String(j.id) === floatingJobId)
        ?? (ghostJobs.length > 0 ? ghostJobs[0] : null)
    readonly property bool detached: !P7.ProtocolState.visible && ghostJob !== null
    readonly property bool waitingToSend: detached && ghostJob?.sendQueued === true

    Timer {
        interval: 28
        running: root.detached
        repeat: true
        onTriggered: root.phase += root.waitingToSend ? 0.38 : 0.32
    }

    Variants {
        model: Screens.screens

        PanelWindow {
            id: win

            required property ShellScreen modelData
            readonly property bool onMonitor: root.detached
                && String(root.ghostJob?.target?.monitor ?? "") === modelData.name
            readonly property bool canAct: onMonitor && (
                String(root.ghostJob?.status ?? "") === "ready"
                || (String(root.ghostJob?.status ?? "") === "error"
                    && root.ghostJob?.retryable === true))
            property bool mapped: false
            property bool revealed: false
            property real lockedX: 0
            property real lockedY: 0
            readonly property string currentId: String(root.ghostJob?.id ?? "")

            // Freeze the placement for the entire lifetime of one job.
            // When the job disappears its nullable target must NOT reset the
            // window margins to the screen origin during the fade-out.
            onCurrentIdChanged: {
                if (onMonitor && currentId !== "") {
                    lockedX = Number(root.ghostJob?.target?.x ?? 0);
                    lockedY = Number(root.ghostJob?.target?.y ?? 0);
                }
            }

            screen: modelData
            Config.screen: modelData.name
            visible: mapped
            color: "transparent"
            surfaceFormat.opaque: false
            exclusionMode: ExclusionMode.Ignore

            anchors.top: true
            anchors.left: true
            // Monitor-local coordinates from ghost_wave.py are *pill centers*.
            // The surface has 16px of shadow padding on all sides.
            readonly property real localX: lockedX
            readonly property real localY: lockedY
            implicitWidth: 144
            implicitHeight: 74
            margins.left: Math.round(Math.max(0, Math.min(modelData.width - width,
                localX - width / 2)))
            margins.top: Math.round(Math.max(0, Math.min(modelData.height - height,
                localY - height / 2)))

            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "protocol7-ghost-wave"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

            // During processing no input is consumed; target field retains
            // keyboard focus and clicks. Ready/error accepts a click on pill.
            mask: Region {
                x: 16
                y: 16
                width: win.canAct ? 112 : 0
                height: win.canAct ? 42 : 0
            }

            onOnMonitorChanged: {
                if (onMonitor) {
                    fadeTimer.stop();
                    lockedX = Number(root.ghostJob?.target?.x ?? 0);
                    lockedY = Number(root.ghostJob?.target?.y ?? 0);
                    revealed = false;
                    mapped = true;
                    revealTimer.restart();
                } else {
                    revealTimer.stop();
                    revealed = false;
                    fadeTimer.restart();
                }
            }

            Timer {
                id: revealTimer
                interval: 20
                repeat: false
                onTriggered: {
                    if (win.onMonitor)
                        win.revealed = true;
                }
            }

            Timer {
                id: fadeTimer
                interval: 190
                repeat: false
                onTriggered: {
                    if (!win.onMonitor)
                        win.mapped = false;
                }
            }

            Process {
                id: action
                property string jobId: ""
                property string mode: ""
                command: ["python3", Paths.toLocalFile(Qt.resolvedUrl("../control_ipc.py")),
                          mode, jobId]
                running: false
                onExited: code => {
                    if (code !== 0)
                        console.warn("Ghost Wave insertion/retry did not complete");
                }
            }

            Item {
                id: pill
                anchors.centerIn: parent
                width: 112
                height: 42
                opacity: win.revealed ? 1 : 0
                scale: win.revealed ? 1 : 0.91

                Behavior on opacity {
                    NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                }
                Behavior on scale {
                    NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
                }

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: -3
                    radius: 26
                    color: "#1a000000"
                    opacity: 0.35
                }
                Rectangle {
                    anchors.fill: parent
                    radius: 21
                    color: Colours.palette.m3surface
                }

                TapHandler {
                    enabled: win.canAct
                    onTapped: {
                        if (action.running || !root.ghostJob)
                            return;
                        action.jobId = String(root.ghostJob.id);
                        action.mode = String(root.ghostJob.status) === "error" ? "retry" : "insert";
                        action.running = true;
                    }
                }

                Row {
                    anchors.centerIn: parent
                    spacing: root.waitingToSend ? 3 : 4

                    Repeater {
                        model: root.waitingToSend ? 15 : 13

                        Item {
                            required property int index
                            readonly property real waveDrift: Math.sin(root.phase * 0.34 + index * 0.23) * 0.16
                            readonly property real queuedWave: 0.12 + 0.83 *
                                Math.pow((1 + Math.sin(root.phase + index * 0.88 + waveDrift)) / 2, 1.45) *
                                (0.86 + 0.14 * Math.sin(root.phase * 0.51 + index * 0.32))
                            readonly property real processingWave:
                                0.22 + 0.72 * (Math.sin(root.phase + index * 0.64) + 1) / 2
                            readonly property real amount: root.waitingToSend ? queuedWave : processingWave

                            width: 4
                            height: 30

                            Rectangle {
                                anchors.bottom: parent.bottom
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: 4
                                height: 4 + parent.amount * 26
                                radius: 2
                                color: String(root.ghostJob?.status ?? "") === "error"
                                    ? Colours.palette.m3error
                                    : Colours.palette.m3primary

                                Behavior on height {
                                    NumberAnimation {
                                        duration: root.waitingToSend ? 65 : 40
                                        easing.type: Easing.OutQuad
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
