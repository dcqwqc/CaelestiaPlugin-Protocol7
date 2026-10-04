pragma Singleton
import QtQuick
QtObject {
    property bool backendConnected: false
    property bool enabled: true
    property string state: "idle"
    property bool whiteboardVisible: false
    property var items: []
    property int sequence: 0
    property int messagesReceived: 0
    function applyMessage(line: string): void {
        const prefix = "COMPANIONSTATE ";
        if (!line || !line.startsWith(prefix)) return;
        try {
            const message = JSON.parse(line.slice(prefix.length));
            backendConnected = true;
            enabled = message.enabled !== false;
            state = String(message.state ?? "idle");
            whiteboardVisible = message.whiteboardVisible === true;
            items = Array.isArray(message.items) ? message.items : [];
            sequence = Number(message.sequence ?? sequence);
            messagesReceived += 1;
        } catch (error) { console.warn("Companion state parse failed:", error); }
    }
    function reset(): void {
        backendConnected = false;
        state = "idle";
        whiteboardVisible = false;
        items = [];
        sequence = 0;
    }
}
