import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib
import sounddevice as sd
from config import save_config

LANGUAGES = {
    "Auto Detect": "auto", "English": "en", "Spanish": "es", "French": "fr", "German": "de", "Italian": "it",
    "Portuguese": "pt", "Russian": "ru", "Japanese": "ja", "Korean": "ko", "Chinese": "zh",
    "Arabic": "ar", "Hindi": "hi", "Dutch": "nl", "Turkish": "tr", "Polish": "pl",
    "Swedish": "sv", "Danish": "da", "Finnish": "fi", "Norwegian": "no", "Greek": "el",
    "Thai": "th", "Vietnamese": "vi", "Indonesian": "id", "Hebrew": "he", "Bengali": "bn",
    "Romanian": "ro", "Czech": "cs", "Ukrainian": "uk", "Hungarian": "hu", "Malay": "ms"
}

TRANSLATE_TARGETS = {
    "None (Keep Original)": "",
    "English": "English", "Spanish": "Spanish", "French": "French", "German": "German",
    "Italian": "Italian", "Portuguese": "Portuguese", "Russian": "Russian", "Japanese": "Japanese",
    "Korean": "Korean", "Chinese": "Chinese", "Arabic": "Arabic", "Hindi": "Hindi",
    "Dutch": "Dutch", "Turkish": "Turkish", "Polish": "Polish", "Swedish": "Swedish",
    "Danish": "Danish", "Finnish": "Finnish", "Norwegian": "Norwegian", "Greek": "Greek",
    "Thai": "Thai", "Vietnamese": "Vietnamese", "Indonesian": "Indonesian", "Hebrew": "Hebrew",
    "Bengali": "Bengali", "Romanian": "Romanian", "Czech": "Czech", "Ukrainian": "Ukrainian",
    "Hungarian": "Hungarian", "Malay": "Malay"
}

import os
def _load_downloaded_whisper_models():
    models = {
        "tiny.en (Fastest, English-only)": "tiny.en",
        "tiny (Fastest, Multi-language)": "tiny",
        "base.en (Fast, English-only)": "base.en",
        "base (Fast, Multi-language)": "base",
        "small.en (Balanced, English-only)": "small.en",
        "small (Balanced, Multi-language)": "small",
        "medium (Accurate, Multi-language)": "medium",
        "large-v3 (Most Accurate, Slowest)": "large-v3"
    }
    try:
        hf_hub_path = os.path.expanduser("~/.cache/huggingface/hub")
        if os.path.exists(hf_hub_path):
            for d in os.listdir(hf_hub_path):
                if d.startswith("models--"):
                    repo_id = d.replace("models--", "").replace("--", "/")
                    if "whisper" in repo_id.lower() and not any(repo_id.endswith(x) for x in ["tiny.en", "tiny", "base.en", "base", "small.en", "small", "medium", "large-v3", "large-v2"]):
                        models[repo_id + " (Downloaded)"] = repo_id
    except Exception:
        pass
    return models

MODELS = _load_downloaded_whisper_models()

class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app, config):
        super().__init__(application=app, title="Protocol-7 Settings")
        self.config = config
        self.set_default_size(550, 750)
        
        # Apply dark theme
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        scrolled = Gtk.ScrolledWindow()
        self.set_child(scrolled)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(20)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        scrolled.set_child(vbox)
        
        self.recording_hotkey = False
        self.current_keycode = self.config.get("hotkey_keycode", 29)

        # -- Groq API Configuration --
        self.add_section_title(vbox, "Groq Cloud API (Whisper)")
        
        # Recommendation
        groq_info = Gtk.Label(label="Creating a Groq API Key is recommended and free! Get one at console.groq.com.")
        groq_info.set_wrap(True)
        vbox.append(groq_info)
        
        groq_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.use_groq_check = Gtk.CheckButton(label="Use Groq API for Whisper")
        self.use_groq_check.set_active(self.config.get("use_groq", False))
        groq_box.append(self.use_groq_check)
        vbox.append(groq_box)

        self.groq_api_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.groq_api_entry = Gtk.Entry()
        self.groq_api_entry.set_placeholder_text("gsk_...")
        self.groq_api_entry.set_text(self.config.get("groq_api_key", ""))
        self.groq_api_entry.set_visibility(False)
        self.groq_api_box.append(Gtk.Label(label="Groq API Key:"))
        self.groq_api_box.append(self.groq_api_entry)
        vbox.append(self.groq_api_box)
        
        self.groq_model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.groq_models = ["whisper-large-v3-turbo", "whisper-large-v3"]
        self.groq_model_combo = Gtk.DropDown.new_from_strings(self.groq_models)
        active_groq = self.config.get("groq_model", "whisper-large-v3-turbo")
        if active_groq in self.groq_models:
            self.groq_model_combo.set_selected(self.groq_models.index(active_groq))
        self.groq_model_box.append(Gtk.Label(label="Groq Model:"))
        self.groq_model_box.append(self.groq_model_combo)
        vbox.append(self.groq_model_box)

        # -- Model Library --
        self.add_section_title(vbox, "Local AI Models & Speed (Whisper)")
        
        whisper_type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.whisper_type_combo = Gtk.DropDown.new_from_strings(["Downloaded Models", "New Download (Custom HF Repo)"])
        whisper_type_box.append(Gtk.Label(label="Source:"))
        whisper_type_box.append(self.whisper_type_combo)
        vbox.append(whisper_type_box)
        
        self.whisper_builtin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.model_keys = list(MODELS.keys())
        self.model_combo = Gtk.DropDown.new_from_strings(self.model_keys)
        self.whisper_builtin_box.append(Gtk.Label(label="Select Model:"))
        self.whisper_builtin_box.append(self.model_combo)
        vbox.append(self.whisper_builtin_box)
        
        self.whisper_custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.custom_whisper_entry = Gtk.Entry()
        self.custom_whisper_entry.set_placeholder_text("e.g. Systran/faster-whisper-large-v3")
        self.whisper_custom_box.append(Gtk.Label(label="Repo ID:"))
        self.whisper_custom_box.append(self.custom_whisper_entry)
        
        whisper_apply_btn = Gtk.Button(label="Apply & Download")
        whisper_apply_btn.add_css_class("suggested-action")
        whisper_apply_btn.connect("clicked", self.on_save_clicked)
        self.whisper_custom_box.append(whisper_apply_btn)
        
        vbox.append(self.whisper_custom_box)
        
        def on_whisper_type_changed(dropdown, pspec):
            is_custom = dropdown.get_selected() == 1
            self.whisper_builtin_box.set_visible(not is_custom)
            self.whisper_custom_box.set_visible(is_custom)
            
        self.whisper_type_combo.connect("notify::selected-item", on_whisper_type_changed)
        
        is_whisper_custom = self.config.get("whisper_is_custom", False)
        if is_whisper_custom:
            self.whisper_type_combo.set_selected(1)
            self.custom_whisper_entry.set_text(self.config.get("custom_model", ""))
        else:
            self.whisper_type_combo.set_selected(0)
            active_model = self.config.get("model_size", "tiny.en")
            for i, k in enumerate(self.model_keys):
                if MODELS[k] == active_model:
                    self.model_combo.set_selected(i)
                    break
                    
        on_whisper_type_changed(self.whisper_type_combo, None)

        def on_use_groq_toggled(check):
            is_active = check.get_active()
            self.groq_api_box.set_visible(is_active)
            self.groq_model_box.set_visible(is_active)
            self.whisper_type_combo.set_sensitive(not is_active)
            self.whisper_builtin_box.set_sensitive(not is_active)
            self.whisper_custom_box.set_sensitive(not is_active)

        self.use_groq_check.connect("toggled", on_use_groq_toggled)
        on_use_groq_toggled(self.use_groq_check)
        
        # -- Hotkey Configuration --
        self.add_section_title(vbox, "Hotkey Configuration")
        
        hotkey_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hotkey_name = self.config.get("hotkey_name", f"Keycode {self.current_keycode}")
        self.hotkey_btn = Gtk.Button(label=f"Click to Record Hotkey (Current: {hotkey_name})")
        self.hotkey_btn.add_css_class("suggested-action")
        self.hotkey_btn.connect("clicked", self.on_record_hotkey)
        hotkey_box.append(Gtk.Label(label="Dictation Key:"))
        hotkey_box.append(self.hotkey_btn)
        vbox.append(hotkey_box)

        # Add key controller
        self.key_ctrl = Gtk.EventControllerKey()
        self.key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.key_ctrl.connect("key-released", self.on_key_released)
        self.add_controller(self.key_ctrl)

        # -- Input Device --
        self.add_section_title(vbox, "Microphone")
        
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        self.devices = []
        try:
            for i, dev in enumerate(sd.query_devices()):
                # Only require that the device can capture audio (this includes monitors/loopbacks)
                if dev['max_input_channels'] > 0:
                    self.devices.append((i, f"{i}: {dev['name']}"))
        except Exception:
            pass
            
        dev_strings = ["Default"] + [d[1] for d in self.devices]
        self.device_combo = Gtk.DropDown.new_from_strings(dev_strings)
        
        active_dev = self.config.get("input_device", None)
        if active_dev is not None:
            for i, d in enumerate(self.devices):
                if d[0] == active_dev:
                    self.device_combo.set_selected(i + 1)
                    break
                    
        device_box.append(Gtk.Label(label="Input Device:"))
        device_box.append(self.device_combo)
        vbox.append(device_box)

        # -- Language & Translation --
        self.add_section_title(vbox, "Language & Translation")
        
        lang_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.lang_keys = list(LANGUAGES.keys())
        self.lang_combo = Gtk.DropDown.new_from_strings(self.lang_keys)
        self.lang_combo.set_enable_search(True)
        
        active_lang = "auto" if self.config.get("auto_detect_language", True) else self.config.get("language", "en")
        for i, k in enumerate(self.lang_keys):
            if LANGUAGES[k] == active_lang:
                self.lang_combo.set_selected(i)
                break
        lang_box.append(Gtk.Label(label="Spoken Language:"))
        lang_box.append(self.lang_combo)
        vbox.append(lang_box)

        trans_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.trans_keys = list(TRANSLATE_TARGETS.keys())
        self.trans_combo = Gtk.DropDown.new_from_strings(self.trans_keys)
        self.trans_combo.set_enable_search(True)
        
        active_trans = self.config.get("translate_target", "")
        if not active_trans and self.config.get("translate", False):
            active_trans = "English"
            
        for i, k in enumerate(self.trans_keys):
            if TRANSLATE_TARGETS[k] == active_trans:
                self.trans_combo.set_selected(i)
                break
        trans_box.append(Gtk.Label(label="AI Translate To:"))
        trans_box.append(self.trans_combo)
        vbox.append(trans_box)
        
        # -- AI Grammar Engine Backend --
        self.add_section_title(vbox, "AI Grammar Engine Backend")
        
        backend_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.backend_combo = Gtk.DropDown.new_from_strings(["Built-in (Llama.cpp)", "Ollama Server"])
        backend_box.append(Gtk.Label(label="Engine Type:"))
        backend_box.append(self.backend_combo)
        vbox.append(backend_box)
        
        # Llama.cpp Settings
        self.llama_cpp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        
        llama_repo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.llama_repo_entry = Gtk.Entry()
        self.llama_repo_entry.set_text(self.config.get("llama_repo", "bartowski/Llama-3.2-3B-Instruct-GGUF"))
        llama_repo_box.append(Gtk.Label(label="HF Repo ID:"))
        llama_repo_box.append(self.llama_repo_entry)
        self.llama_cpp_box.append(llama_repo_box)
        
        llama_file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.llama_file_entry = Gtk.Entry()
        self.llama_file_entry.set_text(self.config.get("llama_filename", "Llama-3.2-3B-Instruct-Q4_K_M.gguf"))
        llama_file_box.append(Gtk.Label(label="GGUF Filename:"))
        llama_file_box.append(self.llama_file_entry)
        
        llama_apply_btn = Gtk.Button(label="Apply & Download")
        llama_apply_btn.add_css_class("suggested-action")
        llama_apply_btn.connect("clicked", self.on_save_clicked)
        llama_file_box.append(llama_apply_btn)
        
        self.llama_cpp_box.append(llama_file_box)
        vbox.append(self.llama_cpp_box)
        
        # Ollama Settings
        self.ollama_settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        
        ollama_endpoint_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.ollama_endpoint_entry = Gtk.Entry()
        self.ollama_endpoint_entry.set_text(self.config.get("ollama_endpoint", "http://127.0.0.1:11434"))
        ollama_endpoint_box.append(Gtk.Label(label="Ollama Endpoint:"))
        ollama_endpoint_box.append(self.ollama_endpoint_entry)
        self.ollama_settings_box.append(ollama_endpoint_box)
        
        ollama_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.ollama_entry = Gtk.Entry()
        self.ollama_entry.set_text(self.config.get("ollama_model", "llama3.2"))
        
        # Scan for models
        self.ollama_dropdown = Gtk.DropDown.new_from_strings(["Click Scan to fetch models..."])
        self.ollama_scan_btn = Gtk.Button(label="Scan")
        self.ollama_scan_btn.add_css_class("suggested-action")
        
        def on_scan_clicked(btn):
            endpoint = self.ollama_endpoint_entry.get_text().strip().rstrip("/")
            scanned_models = []
            try:
                import urllib.request
                import json
                req = urllib.request.Request(f"{endpoint}/api/tags")
                with urllib.request.urlopen(req, timeout=2.0) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    scanned_models = [m["name"] for m in data.get("models", [])]
            except Exception:
                pass
                
            if not scanned_models:
                scanned_models = ["No models found or server offline"]
                
            self.ollama_dropdown.set_model(Gtk.StringList.new(["Select Scanned Model..."] + scanned_models))
            
        self.ollama_scan_btn.connect("clicked", on_scan_clicked)
        
        def on_ollama_scanned_selected(dropdown, pspec):
            idx = dropdown.get_selected()
            if idx > 0:
                val = dropdown.get_selected_item().get_string()
                if "No models" not in val and "Click Scan" not in val:
                    self.ollama_entry.set_text(val)
                    
        self.ollama_dropdown.connect("notify::selected-item", on_ollama_scanned_selected)
        
        ollama_box.append(Gtk.Label(label="Ollama Model:"))
        ollama_box.append(self.ollama_entry)
        ollama_box.append(self.ollama_scan_btn)
        ollama_box.append(self.ollama_dropdown)
        
        self.ollama_settings_box.append(ollama_box)
        vbox.append(self.ollama_settings_box)
        
        def on_backend_changed(dropdown, pspec):
            is_ollama = dropdown.get_selected() == 1
            self.llama_cpp_box.set_visible(not is_ollama)
            self.ollama_settings_box.set_visible(is_ollama)
            
        self.backend_combo.connect("notify::selected-item", on_backend_changed)
        
        active_backend = self.config.get("llm_backend", "Built-in (Llama.cpp)")
        if active_backend == "Ollama Server":
            self.backend_combo.set_selected(1)
        else:
            self.backend_combo.set_selected(0)
            
        on_backend_changed(self.backend_combo, None)

        # -- AI System Prompt Editor --
        self.add_section_title(vbox, "AI System Prompt Editor")
        
        prompt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        
        default_prompt = """You are an expert Speech-to-Text (STT) editor. Your sole task is to process raw voice transcripts and output the speaker's final, intended message in clean, readable text.

### Core Directives:
1. Eliminate Disfluencies: Remove all filler words (um, uh, ah, like, you know) and stutters/repeated words (e.g., "I I went" becomes "I went").
2. Apply Self-Corrections: When the speaker changes their mind mid-sentence (using phrases like "I mean," "actually," "no wait"), strictly apply their final intent and delete the abandoned thought.
3. Fix Phonetic Typos: Correct obvious transcription errors based on context (e.g., "the dog parked" becomes "the dog barked").
4. Add Mechanics: Apply proper capitalization and punctuation to make the sentence grammatically correct.
5. Preserve Meaning: Do not summarize, paraphrase, or change the user's intended tone or vocabulary. Only fix the mechanics of the speech.
6. Absolute Constraint: Output ONLY the final cleaned text. Do not include introductory phrases, quotes, or explanations. 

### Examples:

Input: uh hey what's up man I I was wondering if you wanted to go
Output: Hey, what's up man? I was wondering if you wanted to go.

Input: set a timer for 10 or actually 15 minutes
Output: Set a timer for 15 minutes.

Input: let's meet at 7 a.m. oh wait i mean 8 a.m. no actually let's do noon in Los Angeles
Output: Let's meet at noon in Los Angeles.

Input: remind me to buy eggs milk and oh wait i don't need milk just eggs and bread
Output: Remind me to buy eggs and bread.

Input: the dog parked loudly at the mailman
Output: The dog barked loudly at the mailman."""
        
        self.prompt_textview = Gtk.TextView()
        self.prompt_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.prompt_textview.set_size_request(-1, 250)
        self.prompt_textview.add_css_class("card")
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.prompt_textview)
        scroll.set_size_request(-1, 250)
        
        buf = self.prompt_textview.get_buffer()
        buf.set_text(self.config.get("llm_system_prompt", default_prompt))
        
        prompt_box.append(Gtk.Label(label="Edit the System Prompt & Rules for the AI below:", halign=Gtk.Align.START))
        prompt_box.append(scroll)
        
        prompt_apply_btn = Gtk.Button(label="Apply & Save Custom Prompt")
        prompt_apply_btn.add_css_class("suggested-action")
        prompt_apply_btn.connect("clicked", self.on_save_clicked)
        prompt_box.append(prompt_apply_btn)
        
        vbox.append(prompt_box)

        # -- Appearance --
        self.add_section_title(vbox, "Appearance")
        
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.color_btn = Gtk.ColorButton()
        hex_color = self.config.get("accent_color", "#00ffcc")
        rgba = Gdk.RGBA()
        rgba.parse(hex_color)
        self.color_btn.set_rgba(rgba)
        
        color_box.append(Gtk.Label(label="Accent Color:"))
        color_box.append(self.color_btn)
        
        self.caelestia_check = Gtk.CheckButton(label="Dynamically match Caelestia Shell colors")
        self.caelestia_check.set_active(self.config.get("use_caelestia_colors", False))
        color_box.append(self.caelestia_check)
        
        vbox.append(color_box)

        # -- Integration --
        self.add_section_title(vbox, "Integration")
        
        integration_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        
        self.autostart_check = Gtk.CheckButton(label="Autostart on Login")
        self.autostart_check.set_active(self.config.get("autostart", False))
        integration_box.append(self.autostart_check)
        
        self.tray_check = Gtk.CheckButton(label="Show in System Tray")
        self.tray_check.set_active(self.config.get("show_tray", True))
        integration_box.append(self.tray_check)
        
        self.llm_check = Gtk.CheckButton(label="Enable AI Smart Clean-up & Correction")
        self.llm_check.set_active(self.config.get("enable_llm_rewrite", True))
        integration_box.append(self.llm_check)
        
        vbox.append(integration_box)

        # -- History --
        self.add_section_title(vbox, "History")
        history_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        view_history_btn = Gtk.Button(label="View Dictation History")
        
        def on_view_history_clicked(btn):
            hist_window = Gtk.Window(title="Dictation History")
            hist_window.set_default_size(600, 500)
            hist_window.set_transient_for(self)
            
            scrolled = Gtk.ScrolledWindow()
            textview = Gtk.TextView()
            textview.set_editable(False)
            textview.set_wrap_mode(Gtk.WrapMode.WORD)
            
            try:
                from config import load_history
                history = load_history()
                if not history:
                    content = "No history found."
                else:
                    import datetime
                    lines = []
                    for entry in history:
                        text = entry.get("text", "")
                        ts = entry.get("timestamp", 0)
                        dt = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                        lines.append(f"[{dt}]\n{text}\n")
                    content = "\n".join(lines)
            except Exception as e:
                content = f"Error loading history: {e}"
                
            textview.get_buffer().set_text(content)
            scrolled.set_child(textview)
            hist_window.set_child(scrolled)
            hist_window.present()
            
        view_history_btn.connect("clicked", on_view_history_clicked)
        history_box.append(Gtk.Label(label="View all past transcriptions:"))
        history_box.append(view_history_btn)
        vbox.append(history_box)

        # -- Developer & Debugging --
        self.add_section_title(vbox, "Developer & Debugging")
        
        debug_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        view_logs_btn = Gtk.Button(label="Open Log Window")
        
        def on_view_logs_clicked(btn):
            log_window = Gtk.Window(title="Protocol-7 Logs")
            log_window.set_default_size(600, 400)
            log_window.set_transient_for(self)
            
            scrolled = Gtk.ScrolledWindow()
            textview = Gtk.TextView()
            textview.set_editable(False)
            textview.set_monospace(True)
            
            try:
                with open("/tmp/whisper_log.txt", "r") as f:
                    content = f.read()
            except Exception:
                content = "No logs found at /tmp/whisper_log.txt yet."
                
            textview.get_buffer().set_text(content)
            
            adj = scrolled.get_vadjustment()
            GLib.idle_add(lambda: adj.set_value(adj.get_upper()))
            
            scrolled.set_child(textview)
            log_window.set_child(scrolled)
            log_window.present()
            
        view_logs_btn.connect("clicked", on_view_logs_clicked)
        debug_box.append(Gtk.Label(label="View real-time engine processing logs:"))
        debug_box.append(view_logs_btn)
        vbox.append(debug_box)

        # Save Button
        save_btn = Gtk.Button(label="Save & Exit")
        save_btn.set_margin_top(30)
        save_btn.connect("clicked", self.on_save_clicked)
        save_btn.add_css_class("suggested-action")
        vbox.append(save_btn)
        
        self.load_css()

    def add_section_title(self, box, title):
        label = Gtk.Label(label=title)
        label.set_halign(Gtk.Align.START)
        label.set_margin_top(20)
        label.set_margin_bottom(10)
        label.add_css_class("title-label")
        box.append(label)

    def on_record_hotkey(self, btn):
        if getattr(self, 'recording_hotkey', False):
            return
        
        self.recording_hotkey = True
        self.recorded_keys = []
        self.recorded_key_names = []
        self.currently_held_keys = set()
        self.hotkey_btn.set_label("Listening for 3s...")
        self.hotkey_btn.add_css_class("recording")
        
        # Stop recording after 3 seconds
        GLib.timeout_add(3000, self.stop_recording_hotkey)

    def stop_recording_hotkey(self):
        self.recording_hotkey = False
        self.hotkey_btn.remove_css_class("recording")
        
        if self.recorded_keys:
            if len(self.recorded_keys) == 1:
                self.current_keycode = self.recorded_keys[0]
                self.config["hotkey_name"] = self.recorded_key_names[0]
            else:
                self.current_keycode = self.recorded_keys
                # Handle double/triple tap naming
                if len(set(self.recorded_keys)) == 1:
                    taps = ["Single", "Double", "Triple", "Quadruple"]
                    tap_idx = min(len(self.recorded_keys) - 1, 3)
                    self.config["hotkey_name"] = f"{taps[tap_idx]} {self.recorded_key_names[0]}"
                else:
                    self.config["hotkey_name"] = " + ".join(self.recorded_key_names)
        
        # Update UI
        hotkey_name = self.config.get("hotkey_name", str(self.current_keycode))
        self.hotkey_btn.set_label(f"Click to Record Hotkey (Current: {hotkey_name})")
        return False

    def on_key_pressed(self, ctrl, keyval, keycode, state):
        if getattr(self, 'recording_hotkey', False):
            # GTK keycodes are exactly offset by +8 from raw Linux evdev keycodes
            evdev_keycode = keycode - 8
            
            if evdev_keycode not in self.currently_held_keys:
                self.currently_held_keys.add(evdev_keycode)
                self.recorded_keys.append(evdev_keycode)
                key_name = Gdk.keyval_name(keyval)
                self.recorded_key_names.append(key_name if key_name else f"Key {evdev_keycode}")
                
                # Check for double tap visual output
                if len(self.recorded_keys) > 1 and len(set(self.recorded_keys)) == 1:
                    taps = ["Single", "Double", "Triple", "Quadruple"]
                    tap_idx = min(len(self.recorded_keys) - 1, 3)
                    display_text = f"{taps[tap_idx]} {self.recorded_key_names[0]}"
                else:
                    display_text = " + ".join(self.recorded_key_names)
                    
                self.hotkey_btn.set_label(f"Recording: {display_text}")
            return True
        return False

    def on_key_released(self, ctrl, keyval, keycode, state):
        if getattr(self, 'recording_hotkey', False):
            evdev_keycode = keycode - 8
            self.currently_held_keys.discard(evdev_keycode)
            return True
        return False

    def on_save_clicked(self, btn):
        self.config["use_groq"] = self.use_groq_check.get_active()
        self.config["groq_api_key"] = self.groq_api_entry.get_text()
        self.config["groq_model"] = self.groq_models[self.groq_model_combo.get_selected()]

        buf = self.prompt_textview.get_buffer()
        start, end = buf.get_bounds()
        self.config["llm_system_prompt"] = buf.get_text(start, end, True)
        
        is_whisper_custom = self.whisper_type_combo.get_selected() == 1
        self.config["whisper_is_custom"] = is_whisper_custom
        if is_whisper_custom:
            self.config["model_size"] = self.custom_whisper_entry.get_text()
            self.config["custom_model"] = self.custom_whisper_entry.get_text()
        else:
            sel_idx = self.model_combo.get_selected()
            self.config["model_size"] = MODELS[self.model_keys[sel_idx]]
            
        self.config["llama_repo"] = self.llama_repo_entry.get_text()
        self.config["llama_filename"] = self.llama_file_entry.get_text()
        
        self.config["hotkey_keycode"] = self.current_keycode
        
        rgba = self.color_btn.get_rgba()
        self.config["accent_color"] = f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"
        self.config["use_caelestia_colors"] = self.caelestia_check.get_active()
        
        dev_idx = self.device_combo.get_selected()
        if dev_idx == 0:
            self.config["input_device"] = None
        else:
            self.config["input_device"] = self.devices[dev_idx - 1][0]
            
        lang_idx = self.lang_combo.get_selected()
        lang_val = LANGUAGES[self.lang_keys[lang_idx]]
        if lang_val == "auto":
            self.config["auto_detect_language"] = True
            self.config["language"] = "en"
        else:
            self.config["auto_detect_language"] = False
            self.config["language"] = lang_val
            
        trans_idx = self.trans_combo.get_selected()
        trans_val = TRANSLATE_TARGETS[self.trans_keys[trans_idx]]
        self.config["translate_target"] = trans_val
        
        # Keep whisper translate enabled if English, otherwise let LLM handle it
        self.config["translate"] = (trans_val == "English")
        
        self.config["autostart"] = self.autostart_check.get_active()
        self.config["show_tray"] = self.tray_check.get_active()
        self.config["enable_llm_rewrite"] = self.llm_check.get_active()
        self.config["llm_backend"] = "Ollama Server" if self.backend_combo.get_selected() == 1 else "Built-in (Llama.cpp)"
        self.config["ollama_endpoint"] = self.ollama_endpoint_entry.get_text()
        self.config["ollama_model"] = self.ollama_entry.get_text()
        
        self.update_autostart_file()
        save_config(self.config)
        
        # Automatically restart the application to apply tray icon and hotkey changes
        import subprocess
        import os
        APP_DIR = os.path.dirname(os.path.abspath(__file__))
        
        # Write a robust, detached restart script
        script_path = "/tmp/protocol7_restart.sh"
        with open(script_path, "w") as f:
            f.write(f"#!/bin/bash\n")
            f.write(f"sleep 1\n")
            f.write(f"pkill -f '[p]ython.*main.py'\n")
            f.write(f"pkill -f '[p]ython.*tray.py'\n")
            f.write(f"sleep 1\n")
            f.write(f"cd '{APP_DIR}'\n")
            f.write(f"nohup {APP_DIR}/venv/bin/python -u main.py > /tmp/protocol7_debug.log 2>&1 &\n")
        
        os.chmod(script_path, 0o755)
        subprocess.Popen([script_path], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        self.close()

    def update_autostart_file(self):
        import os
        import shutil
        autostart_dir = os.path.expanduser("~/.config/autostart")
        desktop_file = os.path.expanduser("~/.local/share/applications/protocol-7.desktop")
        autostart_target = os.path.join(autostart_dir, "protocol-7.desktop")
        
        if self.config.get("autostart", False):
            os.makedirs(autostart_dir, exist_ok=True)
            if os.path.exists(desktop_file):
                shutil.copy2(desktop_file, autostart_target)
        else:
            if os.path.exists(autostart_target):
                os.remove(autostart_target)

    def load_css(self):
        css_provider = Gtk.CssProvider()
        accent = self.config.get("accent_color", "#00ffcc")
        if self.config.get("use_caelestia_colors", False):
            caelestia = get_caelestia_colors()
            accent = caelestia.get("primary", accent)
        
        css = f"""
        .title-label {{
            font-size: 16px;
            font-weight: 800;
            color: {accent};
            letter-spacing: 1px;
        }}
        button.suggested-action {{
            background-color: {accent};
            color: #111111;
            font-weight: bold;
            border-radius: 8px;
        }}
        button.recording {{
            background-color: #ff3366;
            color: white;
        }}
        checkbutton check:checked {{
            background-color: {accent};
            border-color: {accent};
            color: #111111;
        }}
        dropdown row:selected {{
            background-color: {accent};
            color: #111111;
        }}
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


import os
def get_caelestia_colors():
    colors = {}
    try:
        path = os.path.expanduser("~/.local/state/caelestia/theme/sddm-theme.conf")
        if os.path.exists(path):
            with open(path, 'r') as f:
                for line in f:
                    if line.startswith("surface="):
                        colors["bg"] = line.strip().split('=')[1]
                    elif line.startswith("text="):
                        colors["fg"] = line.strip().split('=')[1]
                    elif line.startswith("primary="):
                        colors["primary"] = line.strip().split('=')[1]
    except: pass
    return colors

class SettingsApp(Gtk.Application):
    def __init__(self, config):
        super().__init__(application_id="com.whisper.flow.settings")
        self.config = config
        
        # Force light theme for settings window
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", False)

    def do_activate(self):
        win = SettingsWindow(self, self.config)
        win.present()

def run_settings(config):
    app = SettingsApp(config)
    app.run(None)
