# Protocol-7
The ultimate local AI-powered voice dictation tool, built natively for Windows and Linux Wayland.

<img width="134" height="57" alt="image" src="https://github.com/user-attachments/assets/9ac9c6e1-4fd1-4c3e-a183-014eacf854b5" />
<img width="1913" height="1078" alt="image" src="https://github.com/user-attachments/assets/8be086a8-bcd9-4c1a-88de-8f56f953d7de" />

Protocol-7 is a standalone desktop application that provides blazing-fast, offline voice dictation. It integrates directly into your operating system to allow seamless, global voice-to-text with advanced AI grammar correction.

## Features
- **Cross-Platform Native**: Runs flawlessly on both Windows 10/11 and Linux (Wayland).
- **Global Hotkey**: Double-tap `Ctrl` to trigger dictation globally from anywhere in your OS. 
- **Direct local control**: Caelestia and trusted local integrations can start,
  stop, toggle, or query dictation through the mode-0600
  `$XDG_RUNTIME_DIR/protocol7-control.sock` endpoint. The Caelestia plugin also
  exposes `protocol7.trigger`, `startDictation`, and `stopDictation` over
  Quickshell IPC, so integrations do not need to synthesize the configured
  keyboard hotkey.
- **Auto-Paste**: Instantly pastes the transcribed text directly into your currently focused window (`pyautogui` on Windows, `wtype` on Wayland).
- **Local AI Engine**: Powered by `faster-whisper` with automatic hardware acceleration (CUDA fallback to CPU).
- **AI Grammar & Self-Correction**: Features a real-time LLM backend (Built-in LLaMA.cpp or remote Ollama Server) to correct grammar, fix phonetic typos, and apply vocal self-corrections on the fly.
- **Advanced Microphone Engine**: Automatically detects WASAPI/MME capabilities, deduplicates virtual inputs, and auto-negotiates sample rates for pristine audio capture.
- **Customizable UI**: Beautiful dark-mode settings built with `CustomTkinter` (Windows) and `GTK4` (Linux). Change themes, languages, translation targets, and AI prompts visually.

## Setup for Windows

1. Clone the repository:
   ```powershell
   git clone https://github.com/dcqwqc/CaelestiaPlugin-Protocol7.git
   cd protocol-7
   ```
2. Install requirements:
   ```powershell
   pip install -r requirements.txt
   ```
3. Run the app:
   ```powershell
   python main.py
   ```
   *(Optional) You can right-click `create_shortcut.ps1` and select "Run with PowerShell" to generate a hidden `.vbs` shortcut that launches the app silently in the background.*

## Setup for Linux (Wayland)

1. Ensure you have the required dependencies: `python3`, `wtype`, `gtk4`, and `gtk4-layer-shell`.
2. Clone and run the universal install script (Debian/Ubuntu, Arch, Fedora):
   ```bash
   git clone https://github.com/dcqwqc/CaelestiaPlugin-Protocol7.git
   cd protocol-7
   ./install.sh
   ```
3. *Note: The install script adds your user to the `input` group to allow global hotkey detection via `evdev`. You may need to log out and log back in.*
4. Run the app:
   ```bash
   ./venv/bin/python main.py
   ```

## Usage
Once running, the app lives silently in your system tray. 
- **Double-tap `Ctrl`** to start dictating. A beautiful overlay visualizer will pop up at the bottom of your screen.
- **Double-tap `Ctrl` again** to stop. It will automatically process the audio and type it into your active window.

Right-click the tray icon (or run with `--settings`) to access the configuration menu, view live developer logs, and select your preferred microphone.

## Configuration & Custom Models
All model management is handled seamlessly within the Settings UI:
- **Whisper Models**: Choose from built-in models or input any HuggingFace Repo ID (e.g. `Systran/faster-whisper-large-v3`). 
- **Local LLM Models**: Select "Built-in (Llama.cpp)" and provide any GGUF filename and HuggingFace Repo.
- **Ollama**: Connect to a local or remote Ollama server and pull models dynamically.
- **AI System Prompt**: Fully rewrite and customize the exact prompt instructions the AI follows using the built-in text editor.

## License
MIT License


## Hey Tabby companion (V0.1)

Protocol7 now also contains the first thin wrapper around the real ChatGPT Voice
experience. The existing dictation feature stays separate.

Flow:

```text
"Hey Tabby"
    -> speech-gated wake detector using Protocol7’s configured STT backend/model
    -> CompanionState / top Caelestia face
    -> thin Zen browser bridge
    -> real ChatGPT Voice
    -> Companion OS/MCP can drive the local whiteboard socket
```

The browser bridge is deliberately disposable. It focuses Zen and can open the
configured dedicated ChatGPT conversation URL. Exact tab selection and pressing
ChatGPT's Voice control belongs in the optional
`~/.local/bin/hey-tabby-browser-bridge` extension/native-messaging helper; the
core never scrapes ChatGPT content or clicks fixed screen coordinates.

### Local whiteboard / HUD API

The backend owns a mode-0600 Unix socket at
`$XDG_RUNTIME_DIR/protocol7-companion.sock`. Commands are bounded JSON with a
fixed vocabulary. The CLI is intended to be the first adapter target for the
future Companion OS MCP tools:

```bash
python -m companion.cli state listening
python -m companion.cli text "Three trains found" --title "Hamburg -> Berlin"
python -m companion.cli progress 0.65 --label "Building Sumi"
python -m companion.cli choice "Use the fast route?" Yes No
python -m companion.cli shape arrow 0.15 0.25 0.55 0.35
python -m companion.cli clear
```

Whiteboard content is rendered as native QML structured primitives. V0.1 does
not execute arbitrary HTML or shell commands.

### Companion settings

Nexus -> Plugins -> Protocol7 exposes the companion toggle, wake-word toggle,
wake phrase (default `Hey Tabby`), confidence threshold, cooldown, dedicated ChatGPT URL and browser-bridge toggle. Wake transcription reuses the main Protocol7 STT backend/model.

Wake detection deliberately shares Protocol7’s main transcription engine. If Protocol7 uses Groq, wake clips use the configured Groq Whisper model; if Protocol7 uses local Whisper, wake detection follows that local model.

### MCP tools

python -m companion.mcp_server starts a real stdio MCP server using the
current MCP Python SDK. It exposes:

- companion_set_state
- whiteboard_show, whiteboard_hide, whiteboard_clear
- whiteboard_write
- whiteboard_progress
- whiteboard_choice
- whiteboard_shape

The MCP process cannot run arbitrary shell commands. Every call is validated
again by the local Companion IPC layer before it reaches the QML HUD.

### Zen Voice bridge

..browser_extension/ contains the browser-specific ChatGPT Voice adapter. The
Protocol7 bridge focuses Zen and sends Ctrl+Alt+Shift+V; the extension chooses
the pinned ChatGPT tab and activates Voice using semantic DOM metadata rather
than screen coordinates. Keep the dedicated Companion conversation pinned so
wake activations return to the same chat.

Package it with ./scripts/package-voice-bridge.
