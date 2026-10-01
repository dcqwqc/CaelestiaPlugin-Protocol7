pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Caelestia.Config
import qs.components
import qs.components.controls
import qs.modules.nexus.common
import qs.services

ColumnLayout {
    id: root

    property var settings: null
    spacing: Tokens.spacing.extraSmall / 2
    Layout.fillWidth: true

    readonly property string home: Quickshell.env("HOME")

    property var modelChoices: [
        { label: "tiny.en — fastest, English", value: "tiny.en" },
        { label: "tiny — fastest, multilingual", value: "tiny" },
        { label: "base.en — fast, English", value: "base.en" },
        { label: "base — fast, multilingual", value: "base" },
        { label: "small.en — balanced, English", value: "small.en" },
        { label: "small — balanced, multilingual", value: "small" },
        { label: "medium — accurate", value: "medium" },
        { label: "large-v3 — most accurate", value: "large-v3" },
        { label: "large-v3-turbo — fast + accurate", value: "large-v3-turbo" }
    ]

    property var languageChoices: [
        {label: "Auto Detect", value: "auto"}, {label: "English", value: "en"},
        {label: "German", value: "de"}, {label: "Japanese", value: "ja"},
        {label: "Spanish", value: "es"}, {label: "French", value: "fr"},
        {label: "Italian", value: "it"}, {label: "Portuguese", value: "pt"},
        {label: "Russian", value: "ru"}, {label: "Korean", value: "ko"},
        {label: "Chinese", value: "zh"}, {label: "Arabic", value: "ar"},
        {label: "Hindi", value: "hi"}, {label: "Dutch", value: "nl"},
        {label: "Turkish", value: "tr"}, {label: "Polish", value: "pl"},
        {label: "Swedish", value: "sv"}, {label: "Danish", value: "da"},
        {label: "Finnish", value: "fi"}, {label: "Norwegian", value: "no"},
        {label: "Greek", value: "el"}, {label: "Thai", value: "th"},
        {label: "Vietnamese", value: "vi"}, {label: "Indonesian", value: "id"},
        {label: "Hebrew", value: "he"}, {label: "Bengali", value: "bn"},
        {label: "Romanian", value: "ro"}, {label: "Czech", value: "cs"},
        {label: "Ukrainian", value: "uk"}, {label: "Hungarian", value: "hu"},
        {label: "Malay", value: "ms"}
    ]

    property var translateChoices: [
        {label: "None — keep original", value: ""},
        {label: "English", value: "English"}, {label: "German", value: "German"},
        {label: "Japanese", value: "Japanese"}, {label: "Spanish", value: "Spanish"},
        {label: "French", value: "French"}, {label: "Italian", value: "Italian"},
        {label: "Portuguese", value: "Portuguese"}, {label: "Russian", value: "Russian"},
        {label: "Korean", value: "Korean"}, {label: "Chinese", value: "Chinese"},
        {label: "Arabic", value: "Arabic"}, {label: "Hindi", value: "Hindi"},
        {label: "Dutch", value: "Dutch"}, {label: "Turkish", value: "Turkish"},
        {label: "Polish", value: "Polish"}, {label: "Swedish", value: "Swedish"},
        {label: "Danish", value: "Danish"}, {label: "Finnish", value: "Finnish"},
        {label: "Norwegian", value: "Norwegian"}, {label: "Greek", value: "Greek"},
        {label: "Thai", value: "Thai"}, {label: "Vietnamese", value: "Vietnamese"},
        {label: "Indonesian", value: "Indonesian"}, {label: "Hebrew", value: "Hebrew"},
        {label: "Bengali", value: "Bengali"}, {label: "Romanian", value: "Romanian"},
        {label: "Czech", value: "Czech"}, {label: "Ukrainian", value: "Ukrainian"},
        {label: "Hungarian", value: "Hungarian"}, {label: "Malay", value: "Malay"}
    ]

    // Protocol7-specific field row: vertical on purpose. Generic Nexus rows
    // are optimized for wider pages; this plugin is embedded inside a card.
    component P7TextFieldRow: ConnectedRect {
        id: fieldRow

        property string label
        property string subtext
        property string value
        property bool password: false
        property string placeholderText
        signal edited(string value)

        Layout.fillWidth: true
        implicitHeight: fieldLayout.implicitHeight + Tokens.padding.medium * 2

        ColumnLayout {
            id: fieldLayout
            anchors.fill: parent
            anchors.margins: Tokens.padding.medium
            anchors.leftMargin: Tokens.padding.largeIncreased
            anchors.rightMargin: Tokens.padding.largeIncreased
            spacing: Tokens.spacing.small

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                StyledText {
                    Layout.fillWidth: true
                    text: fieldRow.label
                    font: Tokens.font.body.small
                    wrapMode: Text.Wrap
                }

                StyledText {
                    Layout.fillWidth: true
                    visible: fieldRow.subtext.length > 0
                    text: fieldRow.subtext
                    color: Colours.palette.m3outline
                    font: Tokens.font.label.small
                    wrapMode: Text.Wrap
                }
            }

            StyledRect {
                Layout.fillWidth: true
                implicitHeight: fieldInput.implicitHeight + Tokens.padding.small * 2
                radius: Tokens.rounding.large
                color: Colours.tPalette.m3surfaceContainerHighest

                StyledTextField {
                    id: fieldInput
                    anchors.fill: parent
                    anchors.leftMargin: Tokens.padding.medium
                    anchors.rightMargin: Tokens.padding.medium
                    text: fieldRow.value
                    placeholderText: fieldRow.placeholderText
                    echoMode: fieldRow.password ? TextInput.Password : TextInput.Normal
                    horizontalAlignment: TextInput.AlignLeft
                    onEditingFinished: fieldRow.edited(text)
                }
            }
        }
    }

    // Protocol7 uses long model/language labels and is embedded in a narrow
    // plugin card. The stock SelectRow is horizontal, so its SplitButton can
    // collide with the label. Keep this plugin-local and stack the selector
    // under the label instead.
    component ChoiceRow: ConnectedRect {
        id: choice

        property alias label: choiceLabel.text
        property string subtext
        property var choices: []
        property string currentValue
        signal valueSelected(string value)

        Layout.fillWidth: true
        implicitHeight: choiceLayout.implicitHeight + Tokens.padding.medium * 2
        clip: false
        z: selector.expanded ? 20 : 0

        readonly property var menuEntries: choices.map(c => optionItem.createObject(choice, {
            text: c.label,
            storedValue: c.value
        }))

        ColumnLayout {
            id: choiceLayout
            anchors.fill: parent
            anchors.margins: Tokens.padding.medium
            anchors.leftMargin: Tokens.padding.largeIncreased
            anchors.rightMargin: Tokens.padding.largeIncreased
            spacing: Tokens.spacing.small

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                StyledText {
                    id: choiceLabel
                    Layout.fillWidth: true
                    font: Tokens.font.body.small
                    wrapMode: Text.Wrap
                }

                StyledText {
                    Layout.fillWidth: true
                    visible: choice.subtext.length > 0
                    text: choice.subtext
                    color: Colours.palette.m3outline
                    font: Tokens.font.label.small
                    wrapMode: Text.Wrap
                }
            }

            SplitButton {
                id: selector
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                type: SplitButton.Tonal
                menuItems: choice.menuEntries
                active: {
                    const idx = choice.choices.findIndex(c => c.value === choice.currentValue);
                    return idx >= 0 ? choice.menuEntries[idx] : (choice.menuEntries[0] ?? null);
                }
                stateLayer.onClicked: selector.expanded = !selector.expanded
                menu.onItemSelected: item => choice.valueSelected(item.storedValue)
            }
        }

        Component {
            id: optionItem
            MenuItem { property string storedValue }
        }
    }

    SectionHeader { text: "Groq Cloud API (Whisper)" }

    ToggleRow {
        first: true
        text: "Use Groq API for Whisper"
        subtext: "Recommended for fast transcription."
        checked: root.settings?.useGroq ?? false
        onToggled: if (root.settings) root.settings.useGroq = checked
    }

    P7TextFieldRow {
        label: "Groq API key"
        subtext: "Stored locally in Caelestia's plugin settings."
        value: root.settings?.groqApiKey ?? ""
        password: true
        placeholderText: "gsk_..."
        onEdited: value => { if (root.settings) root.settings.groqApiKey = value; }
    }

    ChoiceRow {
        last: true
        label: "Groq model"
        choices: [
            {label: "whisper-large-v3-turbo", value: "whisper-large-v3-turbo"},
            {label: "whisper-large-v3", value: "whisper-large-v3"}
        ]
        currentValue: root.settings?.groqModel ?? "whisper-large-v3-turbo"
        onValueSelected: value => { if (root.settings) root.settings.groqModel = value; }
    }

    SectionHeader { text: "Local Whisper" }

    ChoiceRow {
        first: true
        last: true
        label: "Local model"
        subtext: "Used whenever Groq is disabled."
        choices: root.modelChoices
        currentValue: root.settings?.modelSize ?? "tiny.en"
        onValueSelected: value => {
            if (!root.settings) return;
            root.settings.modelSize = value;
            root.settings.whisperIsCustom = false;
        }
    }

    P7TextFieldRow {
        first: true
        last: true
        label: "Custom Hugging Face Whisper model"
        subtext: "Optional repo/model id. Editing this switches local Whisper to custom mode."
        value: root.settings?.customModel ?? ""
        placeholderText: "org/model-name"
        onEdited: value => {
            if (!root.settings) return;
            root.settings.customModel = value;
            if (value.trim().length > 0) {
                root.settings.modelSize = value.trim();
                root.settings.whisperIsCustom = true;
            }
        }
    }

    SectionHeader { text: "Hotkey Configuration" }

    ConnectedRect {
        first: true
        last: true
        Layout.fillWidth: true
        implicitHeight: hotkeyLayout.implicitHeight + Tokens.padding.medium * 2

        ColumnLayout {
            id: hotkeyLayout
            anchors.fill: parent
            anchors.margins: Tokens.padding.medium
            anchors.leftMargin: Tokens.padding.largeIncreased
            anchors.rightMargin: Tokens.padding.largeIncreased
            spacing: Tokens.spacing.medium

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                StyledText {
                    Layout.fillWidth: true
                    text: "Dictation hotkey"
                    font: Tokens.font.body.small
                }

                StyledText {
                    Layout.fillWidth: true
                    text: "Current: " + (root.settings?.hotkeyName ?? "Double Control_L")
                    color: Colours.palette.m3outline
                    font: Tokens.font.label.small
                    elide: Text.ElideRight
                }
            }

            IconTextButton {
                Layout.alignment: Qt.AlignLeft
                icon: hotkeyCapture.running ? "hearing" : "keyboard"
                text: hotkeyCapture.running ? "Listening…" : "Record hotkey"
                type: IconTextButton.Tonal
                disabled: hotkeyCapture.running
                onClicked: hotkeyCapture.running = true
            }
        }
    }

    Process {
        id: hotkeyCapture
        command: [root.home + "/protocol-7/venv/bin/python", root.home + "/protocol-7/hotkey_capture.py"]
        running: false

        stdout: StdioCollector {
            onStreamFinished: {
                if (!root.settings || text.trim().length === 0)
                    return;
                try {
                    const result = JSON.parse(text.trim());
                    if (result.keycode !== undefined)
                        root.settings.hotkeyKeycode = result.keycode;
                    if (result.name)
                        root.settings.hotkeyName = result.name;
                } catch (e) {
                    console.warn("Protocol7: failed to parse hotkey capture", e);
                }
            }
        }
    }

    SectionHeader { text: "Microphone" }

    ConnectedRect {
        first: true
        last: true
        Layout.fillWidth: true
        implicitHeight: microphoneLayout.implicitHeight + Tokens.padding.medium * 2

        ColumnLayout {
            id: microphoneLayout
            anchors.fill: parent
            anchors.margins: Tokens.padding.medium
            anchors.leftMargin: Tokens.padding.largeIncreased
            anchors.rightMargin: Tokens.padding.largeIncreased
            spacing: Tokens.spacing.small

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                StyledText {
                    Layout.fillWidth: true
                    text: "Input device index"
                    font: Tokens.font.body.small
                }

                StyledText {
                    Layout.fillWidth: true
                    text: "-1 = system default. Use a sounddevice input index for another microphone."
                    color: Colours.palette.m3outline
                    font: Tokens.font.label.small
                    wrapMode: Text.Wrap
                }
            }

            CustomSpinBox {
                Layout.alignment: Qt.AlignLeft
                min: -1
                max: 64
                step: 1
                value: root.settings?.inputDevice ?? -1
                onValueModified: v => { if (root.settings) root.settings.inputDevice = v; }
            }
        }
    }

    SectionHeader { text: "Language & Translation" }

    ChoiceRow {
        first: true
        label: "Spoken language"
        choices: root.languageChoices
        currentValue: root.settings?.autoDetectLanguage ? "auto" : (root.settings?.language ?? "en")
        onValueSelected: value => {
            if (!root.settings) return;
            root.settings.autoDetectLanguage = value === "auto";
            if (value !== "auto")
                root.settings.language = value;
        }
    }

    ChoiceRow {
        last: true
        label: "AI translate to"
        choices: root.translateChoices
        currentValue: root.settings?.translateTarget ?? ""
        onValueSelected: value => {
            if (!root.settings) return;
            root.settings.translateTarget = value;
            root.settings.translate = value === "English";
        }
    }

    SectionHeader { text: "AI Grammar Engine" }

    ToggleRow {
        first: true
        text: "Enable AI smart clean-up & correction"
        checked: root.settings?.enableLlmRewrite ?? false
        onToggled: if (root.settings) root.settings.enableLlmRewrite = checked
    }

    ChoiceRow {
        label: "Engine type"
        choices: [
            {label: "Built-in (Llama.cpp)", value: "Built-in (Llama.cpp)"},
            {label: "Ollama Server", value: "Ollama Server"}
        ]
        currentValue: root.settings?.llmBackend ?? "Built-in (Llama.cpp)"
        onValueSelected: value => { if (root.settings) root.settings.llmBackend = value; }
    }

    P7TextFieldRow {
        visible: (root.settings?.llmBackend ?? "Built-in (Llama.cpp)") === "Built-in (Llama.cpp)"
        label: "HF Repo ID"
        value: root.settings?.llamaRepo ?? ""
        onEdited: value => { if (root.settings) root.settings.llamaRepo = value; }
    }

    P7TextFieldRow {
        visible: (root.settings?.llmBackend ?? "Built-in (Llama.cpp)") === "Built-in (Llama.cpp)"
        last: true
        label: "GGUF filename"
        value: root.settings?.llamaFilename ?? ""
        onEdited: value => { if (root.settings) root.settings.llamaFilename = value; }
    }

    P7TextFieldRow {
        visible: root.settings?.llmBackend === "Ollama Server"
        label: "Ollama endpoint"
        value: root.settings?.ollamaEndpoint ?? "http://127.0.0.1:11434"
        onEdited: value => { if (root.settings) root.settings.ollamaEndpoint = value; }
    }

    P7TextFieldRow {
        visible: root.settings?.llmBackend === "Ollama Server"
        last: true
        label: "Ollama model"
        value: root.settings?.ollamaModel ?? "llama3.2"
        onEdited: value => { if (root.settings) root.settings.ollamaModel = value; }
    }

    SectionHeader { text: "AI System Prompt Editor" }

    ConnectedRect {
        first: true
        last: true
        Layout.fillWidth: true
        implicitHeight: promptLayout.implicitHeight + Tokens.padding.medium * 2

        ColumnLayout {
            id: promptLayout
            anchors.fill: parent
            anchors.margins: Tokens.padding.medium
            anchors.leftMargin: Tokens.padding.largeIncreased
            anchors.rightMargin: Tokens.padding.largeIncreased
            spacing: Tokens.spacing.small

            StyledText {
                Layout.fillWidth: true
                text: "Master cleanup prompt"
                font: Tokens.font.body.small
            }

            StyledText {
                Layout.fillWidth: true
                text: "Edit the complete system prompt used by the cleanup model."
                color: Colours.palette.m3outline
                font: Tokens.font.label.small
            }

            StyledRect {
                Layout.fillWidth: true
                implicitHeight: 190
                radius: Tokens.rounding.large
                color: Colours.tPalette.m3surfaceContainerHighest

                TextArea {
                    id: promptEditor
                    anchors.fill: parent
                    anchors.margins: Tokens.padding.medium
                    text: root.settings?.llmSystemPrompt ?? ""
                    wrapMode: TextEdit.Wrap
                    color: Colours.palette.m3onSurface
                    selectionColor: Colours.palette.m3primaryContainer
                    selectedTextColor: Colours.palette.m3onPrimaryContainer
                    font: Tokens.font.body.small
                    background: null
                    onActiveFocusChanged: {
                        if (!activeFocus && root.settings && root.settings.llmSystemPrompt !== text)
                            root.settings.llmSystemPrompt = text;
                    }
                }
            }

            TextButton {
                Layout.alignment: Qt.AlignRight
                text: "Save prompt"
                type: TextButton.Tonal
                onClicked: if (root.settings) root.settings.llmSystemPrompt = promptEditor.text
            }
        }
    }

    SectionHeader { text: "Appearance & Integration" }

    P7TextFieldRow {
        first: true
        label: "Accent colour"
        subtext: "Hex colour used when Caelestia theme colours are disabled."
        value: root.settings?.accentColor ?? "#B57EDC"
        onEdited: value => { if (root.settings) root.settings.accentColor = value; }
    }

    ToggleRow {
        text: "Dynamically match Caelestia Shell colours"
        checked: root.settings?.useCaelestiaColors ?? true
        onToggled: if (root.settings) root.settings.useCaelestiaColors = checked
    }

    ToggleRow {
        text: "Autostart on login"
        subtext: "Kept for compatibility; the enabled Celestia plugin also starts Protocol 7."
        checked: root.settings?.autostart ?? true
        onToggled: if (root.settings) root.settings.autostart = checked
    }

    ToggleRow {
        last: true
        text: "Show legacy Protocol 7 tray icon"
        subtext: "Optional; normally unnecessary while Protocol 7 runs as a Caelestia plugin."
        checked: root.settings?.showTray ?? false
        onToggled: if (root.settings) root.settings.showTray = checked
    }
}
