pragma Singleton

import QtQuick

QtObject {
    id: root

    property bool backendConnected: false
    property bool visible: false
    property bool processing: false
    property real level: 0
    property string liveText: ""
    property int messagesReceived: 0

    function applyMessage(line: string): void {
        if (!line || !line.startsWith("P7STATE "))
            return;

        try {
            const message = JSON.parse(line.slice(8));
            backendConnected = true;
            visible = message.visible === true;
            processing = message.processing === true;
            level = Math.max(0, Math.min(1, Number(message.level ?? 0)));
            liveText = String(message.liveText ?? "");
        } catch (error) {
            console.warn("Protocol7 state parse failed:", error);
        }
    }

    function reset(): void {
        backendConnected = false;
        visible = false;
        processing = false;
        level = 0;
        liveText = "";
    }
}
