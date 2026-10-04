pragma ComponentBehavior: Bound

import QtQuick
import Caelestia.Config
import qs.services
import dcqwqc.protocol7.services as P7

Item {
    id: root
    property real phase: 0
    readonly property bool panelVisible: P7.CompanionState.enabled && P7.CompanionState.summoned
    readonly property bool panelInputEnabled: false
    readonly property bool panelOverFullscreen: true
    readonly property bool panelLiftShadow: P7.CompanionState.whiteboardVisible
    readonly property real panelDeformAmount: 0.025
    readonly property int panelMotionDuration: 180

    implicitWidth: P7.CompanionState.whiteboardVisible ? 360 : 74
    implicitHeight: P7.CompanionState.whiteboardVisible ? 224 : 46

    Behavior on implicitWidth { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    Behavior on implicitHeight { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

    Timer {
        interval: 90
        repeat: true
        running: root.panelVisible
        onTriggered: {
            root.phase += 0.16
            face.requestPaint()
        }
    }

    Column {
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 6

        Item {
            width: 64
            height: 38
            anchors.horizontalCenter: parent.horizontalCenter

            Canvas {
                id: face
                anchors.fill: parent
                readonly property color ink: P7.CompanionState.state === "error"
                    ? Colours.palette.m3error
                    : Colours.palette.m3primary

                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    ctx.strokeStyle = ink.toString()
                    ctx.fillStyle = ink.toString()
                    ctx.lineWidth = 3.2
                    const state = P7.CompanionState.state
                    const blink = Math.floor(root.phase) % 33 === 0
                    let look = 0
                    if (state === "thinking" || state === "tool")
                        look = Math.sin(root.phase * 1.5) * 2.4

                    if (state === "asleep" || blink) {
                        ctx.beginPath()
                        ctx.moveTo(17, 18); ctx.lineTo(24, 18)
                        ctx.moveTo(40, 18); ctx.lineTo(47, 18)
                        ctx.stroke()
                    } else {
                        const eyeH = state === "wake" || state === "listening" ? 10 : 8
                        ctx.fillRect(17 + look, 14, 6, eyeH)
                        ctx.fillRect(41 + look, 14, 6, eyeH)
                    }

                    ctx.beginPath()
                    if (state === "speaking") {
                        const mouthH = 3 + Math.abs(Math.sin(root.phase * 2.2)) * 6
                        ctx.ellipse(26, 29 - mouthH / 2, 12, mouthH)
                    } else if (state === "success") {
                        ctx.arc(32, 24, 8, 0.2, Math.PI - 0.2)
                    } else if (state === "error") {
                        ctx.arc(32, 35, 7, Math.PI + 0.25, Math.PI * 2 - 0.25)
                    } else if (state === "wake" || state === "listening") {
                        ctx.arc(32, 28, 3, 0, Math.PI * 2)
                    } else {
                        ctx.moveTo(27, 28)
                        ctx.quadraticCurveTo(32, 31, 37, 28)
                    }
                    ctx.stroke()
                }

                Connections {
                    target: P7.CompanionState
                    function onStateChanged(): void { face.requestPaint() }
                }
            }
        }

        Rectangle {
            id: board
            width: 340
            height: 164
            anchors.horizontalCenter: parent.horizontalCenter
            visible: P7.CompanionState.whiteboardVisible
            radius: 14
            color: Colours.tPalette.m3surfaceContainer
            border.width: 1
            border.color: Colours.palette.m3outlineVariant
            clip: true

            Repeater {
                model: P7.CompanionState.items
                delegate: Item {
                    id: delegateRoot
                    required property var modelData
                    required property int index
                    readonly property var entry: modelData || ({})
                    anchors.fill: parent

                    Text {
                        visible: delegateRoot.entry.type === "text"
                        x: 14
                        y: 10 + delegateRoot.index * 34
                        width: board.width - 28
                        text: delegateRoot.entry.title
                            ? String(delegateRoot.entry.title) + "
" + String(delegateRoot.entry.text || "")
                            : String(delegateRoot.entry.text || "")
                        color: Colours.palette.m3onSurface
                        font.pixelSize: 13
                        font.weight: delegateRoot.entry.title ? Font.DemiBold : Font.Normal
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }

                    Item {
                        visible: delegateRoot.entry.type === "progress"
                        x: 14
                        y: 12 + delegateRoot.index * 34
                        width: board.width - 28
                        height: 28
                        Text {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            text: delegateRoot.entry.label || "Working..."
                            color: Colours.palette.m3onSurface
                            font.pixelSize: 12
                        }
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 6
                            radius: 3
                            color: Colours.palette.m3surfaceContainerHighest
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, Number(delegateRoot.entry.value || 0)))
                                height: parent.height
                                radius: parent.radius
                                color: Colours.palette.m3primary
                                Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                            }
                        }
                    }

                    Text {
                        visible: delegateRoot.entry.type === "choice"
                        x: 14
                        y: 10 + delegateRoot.index * 34
                        width: board.width - 28
                        text: "> " + delegateRoot.entry.label
                            + ((delegateRoot.entry.options && delegateRoot.entry.options.length)
                                ? "   " + delegateRoot.entry.options.join("  /  ") : "")
                        color: Colours.palette.m3onSurface
                        font.pixelSize: 13
                        elide: Text.ElideRight
                    }

                    Canvas {
                        id: shapeCanvas
                        anchors.fill: parent
                        visible: delegateRoot.entry.type === "shape"
                        property var payload: delegateRoot.entry
                        property color ink: Colours.palette.m3primary
                        onPaint: {
                            const ctx = getContext("2d")
                            ctx.reset()
                            ctx.strokeStyle = ink.toString()
                            ctx.fillStyle = ink.toString()
                            ctx.lineWidth = 2.5
                            ctx.lineCap = "round"
                            const x = 12 + Number(payload.x || 0) * (width - 24)
                            const y = 12 + Number(payload.y || 0) * (height - 24)
                            const w = Number(payload.w || 0.25) * (width - 24)
                            const h = Number(payload.h || 0.25) * (height - 24)
                            ctx.beginPath()
                            if (payload.kind === "circle") {
                                ctx.ellipse(x, y, Math.abs(w), Math.abs(h))
                            } else if (payload.kind === "rect") {
                                ctx.rect(x, y, w, h)
                            } else {
                                ctx.moveTo(x, y)
                                ctx.lineTo(x + w, y + h)
                                if (payload.kind === "arrow") {
                                    const angle = Math.atan2(h, w)
                                    ctx.moveTo(x + w, y + h)
                                    ctx.lineTo(x + w - 10 * Math.cos(angle - 0.55), y + h - 10 * Math.sin(angle - 0.55))
                                    ctx.moveTo(x + w, y + h)
                                    ctx.lineTo(x + w - 10 * Math.cos(angle + 0.55), y + h - 10 * Math.sin(angle + 0.55))
                                }
                            }
                            ctx.stroke()
                        }
                        Connections {
                            target: P7.CompanionState
                            function onSequenceChanged(): void { shapeCanvas.requestPaint() }
                        }
                        Component.onCompleted: requestPaint()
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: P7.CompanionState.items.length === 0
                text: "Hey Tabby"
                color: Colours.palette.m3onSurfaceVariant
                opacity: 0.55
                font.pixelSize: 13
            }
        }
    }
}
