pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Caelestia.Config
import qs.components.effects
import qs.services
import qs.utils
import dcqwqc.protocol7.services as P7

// This window is intentionally independent of Caelestia's edge-shell BlobGroup.
// A small Wayland layer surface appears AT ITS TARGET. It never travels from
// the bottom edge and never inherits the dock's SDF clipping or retraction.
Scope {
    id: root

    property real phase: 0
    readonly property int pillWidth: 102
    readonly property int pillHeight: 38
    readonly property int shadowInset: 16
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
        onTriggered: root.phase += root.waitingToSend ? 0.31 : 0.24
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
            // Shadow padding equals the native Elevation level-3 blur
            // allowance. The compositor window remains centered on target.
            readonly property real localX: lockedX
            readonly property real localY: lockedY
            implicitWidth: root.pillWidth + root.shadowInset * 2
            implicitHeight: root.pillHeight + root.shadowInset * 2
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
                x: root.shadowInset
                y: root.shadowInset
                width: win.canAct ? root.pillWidth : 0
                height: win.canAct ? root.pillHeight : 0
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
                width: root.pillWidth
                height: root.pillHeight
                opacity: win.revealed ? 1 : 0
                scale: win.revealed ? 1 : 0.91

                Behavior on opacity {
                    NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                }
                Behavior on scale {
                    NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
                }

                // Match Caelestia tooltips and toast cards instead of
                // approximating their shadow with a dark background rectangle.
                // Only the shape radius breathes gently; window coordinates
                // never move while transcription progresses.
                Elevation {
                    anchors.fill: surface
                    radius: surface.radius
                    level: 3
                    z: -1
                }

                Rectangle {
                    id: surface
                    anchors.fill: parent
                    radius: root.pillHeight / 2 + 0.7 * Math.sin(root.phase * 0.21)
                    color: Colours.palette.m3surface
                    border.width: 1
                    border.color: Qt.alpha(Colours.palette.m3outlineVariant, 0.22)
                    antialiasing: true
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
                    spacing: root.waitingToSend ? 2 : 3

                    Repeater {
                        model: root.waitingToSend ? 15 : 13

                        Item {
                            required property int index
                            readonly property real centerDistance: Math.abs(index - (root.waitingToSend ? 7 : 6))
                                / (root.waitingToSend ? 7 : 6)
                            // Combine two slowly varying phases so adjacent
                            // bars correlate like a real waveform, without
                            // periodic symmetrical spikes or harsh jumps.
                            readonly property real waveDrift:
                                0.19 * Math.sin(root.phase * 0.25 + index * 0.37)
                            readonly property real rollingWave:
                                (1 + Math.sin(root.phase + index * 0.58 + waveDrift)) / 2
                            readonly property real texture:
                                (1 + Math.sin(root.phase * 0.43 - index * 0.89)) / 2
                            readonly property real amount: Math.max(0.12, Math.min(0.97,
                                (root.waitingToSend ? 0.2 : 0.16) +
                                (root.waitingToSend ? 0.68 : 0.61) * rollingWave *
                                (0.76 + 0.24 * texture) * (1 - centerDistance * 0.18)))

                            width: 3
                            height: 26

                            Rectangle {
                                anchors.bottom: parent.bottom
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: 3
                                height: 3 + parent.amount * (parent.height - 3)
                                radius: 1.5
                                color: String(root.ghostJob?.status ?? "") === "error"
                                    ? Colours.palette.m3error
                                    : Colours.palette.m3primary

                                Behavior on height {
                                    NumberAnimation {
                                        duration: root.waitingToSend ? 74 : 65
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
