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


## Hey Tabby companion (V0.2)

Protocol7 wraps the real ChatGPT Voice experience without showing a browser or
Zen tab. The visible UI is only the small Caelestia companion and its whiteboard.

Flow:

```text
"Hey Tabby"
    -> speech-gated wake detector using Protocol7’s configured STT backend/model
    -> companion face appears
    -> hidden persistent WebKitGTK ChatGPT runtime
    -> real ChatGPT Voice starts
    -> face stays visible while Voice is active
    -> face disappears again when Voice ends
```

The hidden runtime uses its own persistent ChatGPT session under
`~/.local/share/protocol-7/chatgpt-voice`. It has no address bar, tabs, browser
chrome, or visible window during normal use. On first use only, if it is not
authenticated, it may show a small standalone **Tabby — ChatGPT sign in** window.
After sign-in the runtime returns to hidden operation and reuses the stored session.

The runtime is controlled only through a mode-0600 Unix socket at
`$XDG_RUNTIME_DIR/protocol7-chatgpt-voice.sock`. Protocol7 can activate Voice,
query whether the Voice session is still active, end the session, and show the
one-time login surface. It does not open or focus Zen.

### Companion visibility

The shell panel is fully hidden in `idle`/`asleep`. Saying the wake phrase moves
it to `wake`, then `listening` once ChatGPT Voice activates. Ending Voice returns
it to `idle`, which removes the face and all reserved panel space from the shell.

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

### WebView debug mode

Protocol7 plugin settings include **Show ChatGPT WebView (Debug)**. Turning it
on starts/reuses the same persistent Tabby ChatGPT runtime and keeps its real
WebKit view visible so login redirects, Voice controls, permission prompts and
page state can be inspected directly. Turning it off hides the view again
without logging out or replacing the persistent ChatGPT session.

The runtime is singleton-locked in `$XDG_RUNTIME_DIR` so stale copies cannot
race for the Voice IPC socket or produce conflicting authentication/debug state.

### Smart companion lifecycle

Tabby dismisses setup and idle states automatically. Closing the one-time
sign-in surface closes the companion too. A successful first-time sign-in ends
setup (it does not unexpectedly start Voice); the next `Hey Tabby` starts the
normal hidden Voice flow. Stalled non-active states auto-hide after the configured grace period (5 seconds
by default), while real Voice startup and an active Voice session suppress the
idle timeout.
