import Caelestia.Plugins

// Protocol7 plugin settings — rendered automatically by Caelestia's Plugins page.
// All values are persisted to ~/.config/caelestia/plugins.json under
// `settings."dcqwqc/protocol7"` and injected into Main.qml as `settings`.
SettingsObject {
    // ── Groq Cloud API ──────────────────────────────────────────────────────
    property bool useGroq: false
    SettingMeta on useGroq {
        label: "Use Groq Cloud API"
        description: "Send audio to Groq's API instead of running Whisper locally. Much faster — creating a free API key at console.groq.com is highly recommended!"
        icon: "cloud"
        inputType: SettingMeta.Switch
    }

    property string groqApiKey: ""
    SettingMeta on groqApiKey {
        label: "Groq API Key"
        description: "Your gsk_... key from console.groq.com. Stored only in your local Caelestia config."
        icon: "key"
        inputType: SettingMeta.TextField
    }

    property string groqModel: "whisper-large-v3-turbo"
    SettingMeta on groqModel {
        label: "Groq Whisper model"
        description: "whisper-large-v3-turbo is faster; whisper-large-v3 is slightly more accurate."
        icon: "model_training"
        inputType: SettingMeta.SplitButton
        options: ["whisper-large-v3-turbo", "whisper-large-v3"]
    }

    // ── Local Whisper ───────────────────────────────────────────────────────
    property string modelSize: "tiny.en"
    SettingMeta on modelSize {
        label: "Local Whisper model"
        description: "Only used when Groq is off. Larger models are more accurate but slower to load."
        icon: "memory"
        inputType: SettingMeta.SplitButton
        options: ["tiny.en", "base.en", "small.en", "medium.en", "large-v3", "large-v3-turbo"]
    }

    // ── LLM Rewrite ─────────────────────────────────────────────────────────
    property bool enableLlmRewrite: false
    SettingMeta on enableLlmRewrite {
        label: "AI cleanup (LLM rewrite)"
        description: "Run the transcript through a local LLM to remove filler words and fix punctuation before pasting."
        icon: "auto_fix_high"
        inputType: SettingMeta.Switch
    }

    // ── Language ────────────────────────────────────────────────────────────
    property bool autoDetectLanguage: true
    SettingMeta on autoDetectLanguage {
        label: "Auto-detect language"
        description: "Let Whisper figure out the spoken language automatically."
        icon: "language"
        inputType: SettingMeta.Switch
    }

    // ── Microphone ──────────────────────────────────────────────────────────
    property int inputDevice: -1
    SettingMeta on inputDevice {
        label: "Microphone device index"
        description: "Index of the sounddevice input. -1 uses the system default."
        icon: "mic"
        inputType: SettingMeta.SpinBox
        min: -1
        max: 32
        step: 1
    }
}
