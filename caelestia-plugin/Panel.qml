pragma ComponentBehavior: Bound

import QtQuick
import Caelestia.Config
import qs.services
import dcqwqc.protocol7.services as P7

// Native Protocol7 waveform content. The containing surface, border merge,
// shadow, placement and slide animation are all owned by Caelestia.
Item {
    id: root

    property var settings: null
    property real phase: 0

    readonly property bool panelVisible: P7.ProtocolState.visible
    readonly property bool panelInputEnabled: false
    readonly property real panelDeformAmount: 0.03
    readonly property int panelMotionDuration: 180

    implicitWidth: 112
    implicitHeight: 32

    Timer {
        interval: 45
        repeat: true
        running: P7.ProtocolState.visible && P7.ProtocolState.processing
        onTriggered: root.phase += 0.36
    }

    Row {
        anchors.centerIn: parent
        spacing: 6

        Repeater {
            model: 9

            Rectangle {
                required property int index

                readonly property real distance: Math.abs(index - 4) / 4
                readonly property real voiceWeight: Math.max(0.34, 1 - distance * 0.66)
                readonly property real wave: 0.22 + 0.62 * ((Math.sin(root.phase + index * 0.56) + 1) / 2)
                readonly property real amount: P7.ProtocolState.processing
                    ? wave
                    : Math.max(0.12, P7.ProtocolState.level * voiceWeight)

                width: 4
                height: 4 + amount * 16
                radius: 2
                color: root.settings?.useCaelestiaColors !== false
                    ? Colours.palette.m3primary
                    : (root.settings?.accentColor ?? "#B57EDC")
                opacity: 0.88

                Behavior on height {
                    NumberAnimation {
                        duration: 65
                        easing.type: Easing.OutQuad
                    }
                }
            }
        }
    }
}
