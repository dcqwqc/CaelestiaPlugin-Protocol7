import Caelestia.Plugins

SettingsObject {
    // Groq cloud Whisper
    property bool useGroq: false
    SettingMeta on useGroq {
        label: "Use Groq Cloud API"
        description: "Use Groq Whisper instead of a local model."
        icon: "cloud"
        inputType: SettingMeta.Switch
    }

    property string groqApiKey: ""
    SettingMeta on groqApiKey {
        label: "Groq API key"
        description: "Your local gsk_... key."
        icon: "key"
        inputType: SettingMeta.TextField
    }

    property string groqModel: "whisper-large-v3-turbo"
    SettingMeta on groqModel {
        label: "Groq Whisper model"
        description: "Turbo is faster; large-v3 is slightly more accurate."
        icon: "model_training"
        inputType: SettingMeta.SplitButton
        options: ["whisper-large-v3-turbo", "whisper-large-v3"]
    }

    // Live preview while speaking. Opt-in and display-only: the final
    // always re-transcribes the complete recording with Groq once. Reuses
    // the Groq key and model above (needs "Use Groq Cloud API"); there is no
    // separate credential or paid provider.
    property bool streamingSttEnabled: false
    SettingMeta on streamingSttEnabled {
        label: "Live preview (experimental)"
        description: "Show Groq Whisper preview text every few seconds while speaking. Uses the Groq key above within a conservative free-tier budget. Off by default."
        icon: "graphic_eq"
        inputType: SettingMeta.Switch
    }

    property string streamingSttProvider: "groq-chunks"
    SettingMeta on streamingSttProvider {
        label: "Live preview provider"
        description: "groq-chunks: overlapping ~6 s Groq Whisper windows. The only supported provider."
        icon: "settings_input_antenna"
        inputType: SettingMeta.SplitButton
        options: ["groq-chunks"]
    }

    // Local Whisper
    property bool whisperIsCustom: false
    property string customModel: ""
    property string modelSize: "tiny.en"
    SettingMeta on modelSize {
        label: "Local Whisper model"
        description: "Used when Groq is disabled."
        icon: "memory"
        inputType: SettingMeta.SplitButton
        options: ["tiny.en", "tiny", "base.en", "base", "small.en", "small", "medium", "large-v3", "large-v3-turbo"]
    }

    // Hotkey
    property var hotkeyKeycode: [29, 29]
    property string hotkeyName: "Double Control_L"

    // Microphone
    property int inputDevice: -1
    SettingMeta on inputDevice {
        label: "Microphone device index"
        description: "-1 uses the system default."
        icon: "mic"
        inputType: SettingMeta.SpinBox
        min: -1
        max: 64
        step: 1
    }

    // Language / translation
    property bool autoDetectLanguage: true
    property string language: "en"
    property string translateTarget: ""
    property bool translate: false

    // AI cleanup
    property bool enableLlmRewrite: false
    SettingMeta on enableLlmRewrite {
        label: "AI smart cleanup"
        description: "Clean filler words, stutters and punctuation before pasting."
        icon: "auto_fix_high"
        inputType: SettingMeta.Switch
    }

    property string llmBackend: "Built-in (Llama.cpp)"
    SettingMeta on llmBackend {
        label: "AI cleanup backend"
        icon: "dns"
        inputType: SettingMeta.SplitButton
        options: ["Built-in (Llama.cpp)", "Ollama Server"]
    }

    property string llamaRepo: "bartowski/Llama-3.2-3B-Instruct-GGUF"
    property string llamaFilename: "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    property string ollamaEndpoint: "http://127.0.0.1:11434"
    property string ollamaModel: "llama3.2"

    property string llmSystemPrompt: "You are an expert Speech-to-Text editor. Remove filler words and stutters, apply self-corrections, fix obvious transcription errors, add punctuation and capitalization, preserve the speaker's intended meaning and tone, and output only the cleaned text."


    // Loom wake recognition only. The standalone Loom plugin owns all
    // presentation, ChatGPT/Zen state, input and lifecycle settings.
    property bool companionWakeEnabled: true
    SettingMeta on companionWakeEnabled {
        label: "Loom wake word"
        description: "Listen for Loom's wake phrase using Protocol7's selected STT backend/model."
        icon: "record_voice_over"
        inputType: SettingMeta.Switch
    }

    property string companionWakePhrase: "Hey Loom"
    SettingMeta on companionWakePhrase {
        label: "Loom wake phrase"
        description: "Phrase Protocol7 recognizes before handing control to the standalone Loom plugin."
        icon: "graphic_eq"
        inputType: SettingMeta.TextField
    }

    property string companionClosePhrase: "Bye Loom"
    SettingMeta on companionClosePhrase {
        label: "Loom close phrase"
        description: "Phrase Protocol7 recognizes to end Voice and close Loom. Leave empty to disable. Example: Bye Loom"
        icon: "voice_over_off"
        inputType: SettingMeta.TextField
    }

    property int companionWakeThreshold: 84
    SettingMeta on companionWakeThreshold {
        label: "Wake confidence"
        description: "Higher values reduce accidental Loom activations."
        icon: "tune"
        inputType: SettingMeta.SpinBox
        min: 65
        max: 98
        step: 1
    }

    property int companionWakeCooldownSeconds: 4
    SettingMeta on companionWakeCooldownSeconds {
        label: "Wake cooldown"
        description: "Minimum seconds before Protocol7 may trigger Loom again."
        icon: "timer"
        inputType: SettingMeta.SpinBox
        min: 1
        max: 30
        step: 1
    }

    // Appearance / integration
    property string accentColor: "#B57EDC"
    property bool useCaelestiaColors: true
    property string themeMode: "system"
    property bool autostart: true
    property bool showTray: false
}
