pragma ComponentBehavior: Bound

import Caelestia.Plugins

// Protocol7 plugin settings — rendered automatically by Caelestia's Plugins page.
// Persisted to ~/.config/caelestia/plugins.json under `settings."dcqwqc/protocol7"`.
// All are bridged into ~/.config/protocol-7/config.json by Main.qml.
SettingsObject {

    // ── Groq Cloud API ──────────────────────────────────────────────────────
    property bool useGroq: false
    SettingMeta on useGroq {
        label: "Use Groq Cloud API"
        description: "Send audio to Groq instead of running Whisper locally. Much faster — creating a free API key at console.groq.com is highly recommended!"
        icon: "cloud"
        inputType: SettingMeta.Switch
    }

    property string groqApiKey: ""
    SettingMeta on groqApiKey {
        label: "Groq API key"
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
        description: "Only used when Groq is off. Larger = more accurate but slower to load."
        icon: "memory"
        inputType: SettingMeta.SplitButton
        options: ["tiny.en", "base.en", "small.en", "medium.en", "large-v3", "large-v3-turbo"]
    }

    // ── Language & Translation ──────────────────────────────────────────────
    property bool autoDetectLanguage: true
    SettingMeta on autoDetectLanguage {
        label: "Auto-detect language"
        description: "Let Whisper detect the spoken language automatically."
        icon: "language"
        inputType: SettingMeta.Switch
    }

    property string language: "en"
    SettingMeta on language {
        label: "Spoken language code"
        description: "Two-letter language code (e.g. en, de, fr, es). Only used when auto-detect is off."
        icon: "translate"
        inputType: SettingMeta.TextField
    }

    property bool translate: false
    SettingMeta on translate {
        label: "Translate to English"
        description: "Transcribe in any language and translate the result to English automatically."
        icon: "g_translate"
        inputType: SettingMeta.Switch
    }

    // ── LLM Rewrite ─────────────────────────────────────────────────────────
    property bool enableLlmRewrite: false
    SettingMeta on enableLlmRewrite {
        label: "AI cleanup (LLM rewrite)"
        description: "Run the transcript through a local LLM to remove filler words and fix punctuation before pasting."
        icon: "auto_fix_high"
        inputType: SettingMeta.Switch
    }

    property string llmBackend: "Built-in (Llama.cpp)"
    SettingMeta on llmBackend {
        label: "LLM backend"
        description: "Built-in uses a downloaded GGUF model via Llama.cpp. Ollama uses a running Ollama server."
        icon: "dns"
        inputType: SettingMeta.SplitButton
        options: ["Built-in (Llama.cpp)", "Ollama Server"]
    }

    property string llamaRepo: "bartowski/Llama-3.2-3B-Instruct-GGUF"
    SettingMeta on llamaRepo {
        label: "Llama.cpp HuggingFace repo"
        description: "HuggingFace repo ID of the GGUF model to download and use."
        icon: "folder_open"
        inputType: SettingMeta.TextField
    }

    property string llamaFilename: "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    SettingMeta on llamaFilename {
        label: "Llama.cpp GGUF filename"
        description: "Exact filename of the GGUF file inside the HuggingFace repo."
        icon: "description"
        inputType: SettingMeta.TextField
    }

    property string ollamaEndpoint: "http://127.0.0.1:11434"
    SettingMeta on ollamaEndpoint {
        label: "Ollama endpoint"
        description: "URL of the Ollama server (default: http://127.0.0.1:11434)."
        icon: "hub"
        inputType: SettingMeta.TextField
    }

    property string ollamaModel: "llama3.2"
    SettingMeta on ollamaModel {
        label: "Ollama model"
        description: "Model name to use with Ollama (e.g. llama3.2, mistral)."
        icon: "smart_toy"
        inputType: SettingMeta.TextField
    }

    property string llmSystemPrompt: "You are an expert Speech-to-Text (STT) editor. Your sole task is to process raw voice transcripts and output the speaker's final, intended message in clean, readable text.\n\n### Core Directives:\n1. Eliminate Disfluencies: Remove all filler words (um, uh, ah, like, you know) and stutters.\n2. Apply Self-Corrections: When the speaker changes their mind (using phrases like \"I mean,\" \"actually,\" \"no wait\"), apply their final intent.\n3. Fix Phonetic Typos: Correct obvious transcription errors based on context.\n4. Add Mechanics: Apply proper capitalization and punctuation.\n5. Preserve Meaning: Do not summarize, paraphrase, or change the user's intended tone.\n6. Absolute Constraint: Output ONLY the final cleaned text."
    SettingMeta on llmSystemPrompt {
        label: "LLM system prompt"
        description: "The instruction given to the LLM that cleans up the raw transcript."
        icon: "psychology"
        inputType: SettingMeta.TextField
    }

    // ── Microphone ──────────────────────────────────────────────────────────
    property int inputDevice: -1
    SettingMeta on inputDevice {
        label: "Microphone device index"
        description: "Index of the sounddevice input device. Use -1 for the system default."
        icon: "mic"
        inputType: SettingMeta.SpinBox
        min: -1
        max: 32
        step: 1
    }

    // ── Appearance ──────────────────────────────────────────────────────────
    property string accentColor: "#B57EDC"
    SettingMeta on accentColor {
        label: "Accent colour"
        description: "Hex colour (#rrggbb) used for the visualiser bars. Ignored when Caelestia colours are enabled."
        icon: "palette"
        inputType: SettingMeta.TextField
    }

    property bool useCaelestiaColors: true
    SettingMeta on useCaelestiaColors {
        label: "Use Caelestia theme colours"
        description: "Automatically pick colours from your Caelestia theme instead of the accent colour above."
        icon: "style"
        inputType: SettingMeta.Switch
    }

    // ── System ──────────────────────────────────────────────────────────────
    property bool autostart: true
    SettingMeta on autostart {
        label: "Autostart on login"
        description: "Launch Protocol7 automatically when you log in."
        icon: "start"
        inputType: SettingMeta.Switch
    }
}
