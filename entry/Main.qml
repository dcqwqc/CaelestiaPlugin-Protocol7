import QtQuick
import Quickshell
import Quickshell.Io
import dcqwqc.protocol7.services as P7
import qs.utils

// Protocol7 backend controller. Visual presentation is a separate shell-panel
// entry point rendered by Caelestia itself.
Scope {
    id: root

    property var settings: null

    readonly property string configPath: `${Quickshell.env("HOME")}/.config/protocol-7/config.json`
    readonly property string daemonScript: Paths.toLocalFile(Qt.resolvedUrl("../main.py"))
    readonly property string configBridgeScript: Paths.toLocalFile(Qt.resolvedUrl("../plugin_config_bridge.py"))
    readonly property string controlScript: Paths.toLocalFile(Qt.resolvedUrl("../control_ipc.py"))
    readonly property string venvPython: `${Paths.data}/plugin-runtime/protocol7/venv/bin/python`
    readonly property string bootstrapScript: Paths.toLocalFile(Qt.resolvedUrl("../scripts/plugin-bootstrap"))
    property bool backendReady: false

    property bool writeQueued: false
    property bool restartRequested: false
    property bool initialBackendAttach: true

    readonly property string patchJson: {
        if (!settings)
            return "{}";

        return JSON.stringify({
            use_groq: settings.useGroq,
            groq_api_key: settings.groqApiKey,
            groq_model: settings.groqModel,
            model_size: settings.modelSize,
            whisper_is_custom: settings.whisperIsCustom,
            custom_model: settings.customModel,
            hotkey_keycode: settings.hotkeyKeycode,
            hotkey_name: settings.hotkeyName,
            auto_detect_language: settings.autoDetectLanguage,
            language: settings.language,
            translate_target: settings.translateTarget,
            translate: settings.translate,
            enable_llm_rewrite: settings.enableLlmRewrite,
            llm_backend: settings.llmBackend,
            llama_repo: settings.llamaRepo,
            llama_filename: settings.llamaFilename,
            ollama_endpoint: settings.ollamaEndpoint,
            ollama_model: settings.ollamaModel,
            llm_system_prompt: settings.llmSystemPrompt,
            input_device: settings.inputDevice === -1 ? null : settings.inputDevice,
            accent_color: settings.accentColor,
            use_caelestia_colors: settings.useCaelestiaColors,
            theme_mode: settings.themeMode,
            autostart: settings.autostart,
            show_tray: settings.showTray,
            companion_enabled: settings.companionEnabled,
            companion_wake_enabled: settings.companionWakeEnabled,
            companion_wake_phrase: settings.companionWakePhrase,
            companion_wake_threshold: settings.companionWakeThreshold / 100.0,
            companion_wake_cooldown_seconds: settings.companionWakeCooldownSeconds,
            companion_wake_model: settings.companionWakeModel,
            companion_url: settings.companionUrl,
            companion_browser_bridge_enabled: settings.companionBrowserBridgeEnabled,
            native_caelestia_ui: true
        });
    }

    function applySettings(restartDaemon: bool): void {
        if (!settings || !backendReady)
            return;

        restartRequested = restartRequested || restartDaemon;
        if (writeConfig.running) {
            writeQueued = true;
            return;
        }
        writeConfig.running = true;
    }

    QtObject {
        id: daemonManager

        function ensureRunning(): void {
            if (!startProc.running)
                startProc.running = true;
        }

        function restart(): void {
            stopProc.running = true;
        }
    }

    Process {
        id: bootstrap
        command: [root.bootstrapScript]
        running: false
        onExited: code => {
            if (code === 0) {
                root.backendReady = true;
                root.applySettings(false);
            } else {
                console.warn("Protocol7 bootstrap failed with code", code);
            }
        }
    }

    Process {
        id: writeConfig

        // Avoid shell quoting entirely: user-editable prompts and API keys are
        // passed as argv to a tiny atomic JSON merge helper.
        command: [
            "python3",
            root.configBridgeScript,
            root.configPath,
            root.patchJson
        ]

        stderr: SplitParser {
            onRead: data => {
                if (data.trim() !== "")
                    console.warn("Protocol7 config bridge:", data.trim());
            }
        }

        onExited: (code, status) => {
            if (code !== 0)
                return;

            if (root.writeQueued) {
                root.writeQueued = false;
                Qt.callLater(() => writeConfig.running = true);
                return;
            }

            // On every shell/plugin load, kill any orphaned Protocol7 backend
            // first. The new backend must be a child of this Process object so
            // its P7STATE stdout is actually connected to ProtocolState.
            if (root.initialBackendAttach) {
                root.initialBackendAttach = false;
                root.restartRequested = false;
                daemonManager.restart();
            } else if (root.restartRequested) {
                root.restartRequested = false;
                daemonManager.restart();
            } else {
                daemonManager.ensureRunning();
            }
        }
    }

    Process {
        id: startProc

        command: [root.venvPython, root.daemonScript]

        stdout: SplitParser {
            splitMarker: "
"
            onRead: data => {
                P7.ProtocolState.applyMessage(data);
                P7.CompanionState.applyMessage(data);
            }
        }

        stderr: SplitParser {
            splitMarker: "
"
            onRead: data => {
                if (data.trim() !== "")
                    console.warn("Protocol7 backend:", data.trim());
            }
        }

        onExited: {
            P7.ProtocolState.reset();
            P7.CompanionState.reset();
        }
    }

    Process {
        id: stopProc

        command: ["pkill", "-f", "protocol-7.*main.py"]
        onExited: restartTimer.start()
    }

    Timer {
        id: restartTimer

        interval: 350
        repeat: false
        onTriggered: daemonManager.ensureRunning()
    }

    onSettingsChanged: { if (backendReady) applySettings(false); }

    Connections {
        target: settings
        enabled: settings !== null

        function onUseGroqChanged(): void { root.applySettings(true); }
        function onGroqApiKeyChanged(): void { root.applySettings(true); }
        function onGroqModelChanged(): void { root.applySettings(true); }
        function onModelSizeChanged(): void { root.applySettings(true); }
        function onWhisperIsCustomChanged(): void { root.applySettings(true); }
        function onCustomModelChanged(): void { root.applySettings(true); }
        function onHotkeyKeycodeChanged(): void { root.applySettings(true); }
        function onHotkeyNameChanged(): void { root.applySettings(true); }
        function onAutoDetectLanguageChanged(): void { root.applySettings(true); }
        function onLanguageChanged(): void { root.applySettings(true); }
        function onTranslateTargetChanged(): void { root.applySettings(true); }
        function onTranslateChanged(): void { root.applySettings(true); }
        function onEnableLlmRewriteChanged(): void { root.applySettings(true); }
        function onLlmBackendChanged(): void { root.applySettings(true); }
        function onLlamaRepoChanged(): void { root.applySettings(true); }
        function onLlamaFilenameChanged(): void { root.applySettings(true); }
        function onOllamaEndpointChanged(): void { root.applySettings(true); }
        function onOllamaModelChanged(): void { root.applySettings(true); }
        function onLlmSystemPromptChanged(): void { root.applySettings(true); }
        function onInputDeviceChanged(): void { root.applySettings(true); }
        function onAccentColorChanged(): void { root.applySettings(false); }
        function onUseCaelestiaColorsChanged(): void { root.applySettings(false); }
        function onThemeModeChanged(): void { root.applySettings(false); }
        function onAutostartChanged(): void { root.applySettings(false); }
        function onShowTrayChanged(): void { root.applySettings(true); }
        function onCompanionEnabledChanged(): void { root.applySettings(true); }
        function onCompanionWakeEnabledChanged(): void { root.applySettings(true); }
        function onCompanionWakePhraseChanged(): void { root.applySettings(true); }
        function onCompanionWakeThresholdChanged(): void { root.applySettings(true); }
        function onCompanionWakeCooldownSecondsChanged(): void { root.applySettings(true); }
        function onCompanionWakeModelChanged(): void { root.applySettings(true); }
        function onCompanionUrlChanged(): void { root.applySettings(true); }
        function onCompanionBrowserBridgeEnabledChanged(): void { root.applySettings(true); }
    }

    IpcHandler {
        target: "protocol7"

        function trigger(): string {
            daemonManager.ensureRunning();
            triggerRetry.remaining = 12;
            triggerRetry.start();
            return "queued";
        }

        function startDictation(): string {
            daemonManager.ensureRunning();
            controlStart.running = true;
            return "queued";
        }

        function stopDictation(): string {
            daemonManager.ensureRunning();
            controlStop.running = true;
            return "queued";
        }

        function debug(): string {
            return [
                `backendRunning=${startProc.running}`,
                `stateConnected=${P7.ProtocolState.backendConnected}`,
                `visible=${P7.ProtocolState.visible}`,
                `processing=${P7.ProtocolState.processing}`,
                `level=${P7.ProtocolState.level}`,
                `stateMessages=${P7.ProtocolState.messagesReceived}`
            ].join("
");
        }

    }

    Process {
        id: controlToggle
        command: [root.venvPython, root.controlScript, "toggle"]
    }

    Process {
        id: controlStart
        command: [root.venvPython, root.controlScript, "start"]
    }

    Process {
        id: controlStop
        command: [root.venvPython, root.controlScript, "stop"]
    }

    Timer {
        id: triggerRetry
        property int remaining: 0
        interval: 80
        repeat: true
        onTriggered: {
            if (controlToggle.running)
                return;
            if (remaining <= 0) {
                stop();
                return;
            }
            remaining -= 1;
            controlToggle.running = true;
        }
    }

    Connections {
        target: controlToggle
        function onExited(code: int): void {
            if (code === 0)
                triggerRetry.stop();
        }
    }

    Component.onCompleted: bootstrap.running = true

    Component.onDestruction: stopProc.running = true
}
