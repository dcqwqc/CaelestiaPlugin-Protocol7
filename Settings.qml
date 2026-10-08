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


    // Lume wake recognition only. The standalone Lume plugin owns all
    // presentation, ChatGPT/Zen state, input and lifecycle settings.
    property bool companionWakeEnabled: true
    SettingMeta on companionWakeEnabled {
        label: "Lume wake word"
        description: "Listen for Lume's wake phrase using Protocol7's selected STT backend/model."
        icon: "record_voice_over"
        inputType: SettingMeta.Switch
    }

    property string companionWakePhrase: "Hey Lume"
    SettingMeta on companionWakePhrase {
        label: "Lume wake phrase"
        description: "Phrase Protocol7 recognizes before handing control to the standalone Lume plugin."
        icon: "graphic_eq"
        inputType: SettingMeta.TextField
    }

    property string companionClosePhrase: "Bye Lume"
    SettingMeta on companionClosePhrase {
        label: "Lume close phrase"
        description: "Phrase Protocol7 recognizes to end Voice and close Lume. Leave empty to disable. Example: Bye Lume"
        icon: "voice_over_off"
        inputType: SettingMeta.TextField
    }

    property int companionWakeThreshold: 84
    SettingMeta on companionWakeThreshold {
        label: "Wake confidence"
        description: "Higher values reduce accidental Lume activations."
        icon: "tune"
        inputType: SettingMeta.SpinBox
        min: 65
        max: 98
        step: 1
    }

    property int companionWakeCooldownSeconds: 4
    SettingMeta on companionWakeCooldownSeconds {
        label: "Wake cooldown"
        description: "Minimum seconds before Protocol7 may trigger Lume again."
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
