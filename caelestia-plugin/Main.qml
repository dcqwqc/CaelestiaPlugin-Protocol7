pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io

// Protocol7 Caelestia plugin root.
// This is a `custom` entry point that lives at the shell root. It:
//   1. Receives settings injected by the plugin loader as `settings`
//   2. Writes them into ~/.config/protocol-7/config.json so the Python daemon picks them up
//   3. Ensures the Python daemon is running (starts it if not)
//   4. Stops/restarts the daemon when settings change or the plugin is disabled
Scope {
    id: root

    // Injected by the Caelestia plugin loader — rebuilt on every plugin reload.
    property var settings: null

    // ── Config bridge ─────────────────────────────────────────────────────
    // Write Caelestia plugin settings → protocol-7 config.json whenever they change.
    // The Python daemon watches the file for changes and picks them up live.
    readonly property string configPath: `${Quickshell.env("HOME")}/.config/protocol-7/config.json`
    readonly property string daemonScript: `${Quickshell.env("HOME")}/protocol-7/main.py`
    readonly property string venvPython: `${Quickshell.env("HOME")}/protocol-7/venv/bin/python`

    function applySettings(): void {
        if (!settings) return;
        writeConfig.running = true;
    }

    // Build a jq command that merges new settings into the existing JSON file,
    // creating it if absent.
    readonly property var jqArgs: {
        if (!settings) return [];
        const s = settings;
        const patch = JSON.stringify({
            use_groq: s.useGroq,
            groq_api_key: s.groqApiKey,
            groq_model: s.groqModel,
            model_size: s.modelSize,
            enable_llm_rewrite: s.enableLlmRewrite,
            auto_detect_language: s.autoDetectLanguage,
            input_device: s.inputDevice === -1 ? null : s.inputDevice,
            // Tell main.py not to start the tray — Caelestia is the shell
            show_tray: false
        });
        return ["bash", "-c",
            `cfg="${root.configPath}"; ` +
            `tmp=$(mktemp); ` +
            `if [ -f "$cfg" ]; then jq '. * ${patch}' "$cfg" > "$tmp" && mv "$tmp" "$cfg"; ` +
            `else echo '${patch}' | jq '.' > "$cfg"; fi`
        ];
    }

    Process {
        id: writeConfig
        command: root.jqArgs
        running: false
        onExited: code => {
            if (code === 0) daemonManager.ensureRunning();
        }
    }

    // ── Daemon lifecycle ──────────────────────────────────────────────────
    QtObject {
        id: daemonManager

        function ensureRunning(): void {
            checkProc.running = true;
        }

        function restart(): void {
            stopProc.running = true;
        }
    }

    // Check if daemon is already running
    Process {
        id: checkProc
        command: ["pgrep", "-f", "protocol-7.*main.py"]
        running: false
        stdout: StdioCollector {
            onStreamFinished: {
                // If output is empty, nothing running — start it
                if (text.trim() === "") startProc.running = true;
            }
        }
    }

    // Start the daemon
    Process {
        id: startProc
        command: [root.venvPython, root.daemonScript]
        running: false
        // Don't wait for it — it runs forever in the background
    }

    // Stop the daemon (e.g. plugin disabled)
    Process {
        id: stopProc
        command: ["pkill", "-f", "protocol-7.*main.py"]
        running: false
        onExited: _ => {
            // After a brief pause let the process die, then restart
            restartTimer.start();
        }
    }

    Timer {
        id: restartTimer
        interval: 800
        repeat: false
        onTriggered: {
            applySettings();
        }
    }

    // ── React to settings changes ─────────────────────────────────────────
    onSettingsChanged: applySettings()

    Connections {
        target: settings
        enabled: settings !== null
        function onUseGroqChanged(): void       { root.applySettings(); }
        function onGroqApiKeyChanged(): void    { root.applySettings(); }
        function onGroqModelChanged(): void     { root.applySettings(); }
        function onModelSizeChanged(): void     { root.applySettings(); }
        function onEnableLlmRewriteChanged(): void { root.applySettings(); }
        function onAutoDetectLanguageChanged(): void { root.applySettings(); }
        function onInputDeviceChanged(): void   { root.applySettings(); }
    }

    Component.onCompleted: applySettings()

    // When the plugin is destroyed (user disables it), stop the daemon.
    Component.onDestruction: {
        stopProc.running = true;
    }
}
