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


## Hey Tabby companion (V0.3)

Protocol7 wraps the real ChatGPT Voice experience while keeping browser UI out
of the normal interaction. The visible UI is the small Caelestia companion and
its whiteboard.

Flow:

```text
"Hey Tabby"
    -> speech-gated wake detector using Protocol7’s configured STT backend/model
    -> companion face appears
    -> hidden dedicated ChatGPT tab inside Zen/Firefox
    -> privileged local bridge sends a trusted Voice-button click
    -> real ChatGPT Voice starts through Firefox WebRTC
    -> face stays visible while Voice is active
    -> face disappears again when Voice ends
```

The dedicated Tabby tab uses the user’s normal persistent Zen profile, including
the existing ChatGPT login session. During normal use the tab is collapsed and
not selected. The local Sine bridge reads no conversation text; it only exposes
Voice readiness/active state and accepts activate/end/show/hide commands through
small JSON command/state files stored inside the Zen profile.

WebKitGTK is deliberately not used for Voice. Mirai’s WebKitGTK 2.52.x
GStreamer WebRTC path crashed during outgoing audio startup. The Zen/Firefox
engine avoids that media backend while preserving the same Tabby UI and wake
flow.

### Companion visibility

The shell panel is fully hidden in `idle`/`asleep`. Saying the wake phrase moves
it to `wake`, then `listening` once ChatGPT Voice is confirmed active. Ending
Voice returns it to `idle`, which removes the face and all reserved panel space.

### Whiteboard / MCP

The companion whiteboard remains native QML and supports text, progress, choices,
line/arrow/rectangle/circle primitives. `python -m companion.mcp_server` exposes
the stdio MCP tools `companion_set_state`, `whiteboard_show`, `whiteboard_hide`,
`whiteboard_clear`, `whiteboard_write`, `whiteboard_progress`, `whiteboard_choice`,
and `whiteboard_shape`. Every MCP call is validated again by the local companion
IPC layer before reaching QML.

### Wake transcription

Wake detection deliberately shares Protocol7’s main transcription engine. With
the current configuration that means Groq + `whisper-large-v3`. Wake requests
do not fall back to the local `tiny.en` model if Groq fails.

### Voice engine debug mode

Protocol7 plugin settings include **Show Tabby Voice Engine (Debug)**. Turning
it on reveals the dedicated Tabby ChatGPT tab in Zen so login, Voice controls,
permissions, and page state can be inspected directly. Turning it off collapses
that tab again without logging out or changing the persistent ChatGPT session.

### Smart companion lifecycle

Tabby dismisses setup and idle states automatically. Closing the visible setup
surface closes the companion too. Stalled non-active states auto-hide after the
configured grace period (5 seconds by default), while real Voice startup and an
active Voice session suppress the idle timeout.
