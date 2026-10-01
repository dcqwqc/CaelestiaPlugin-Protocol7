import os
import json

CONFIG_DIR = os.path.expanduser("~/.config/protocol-7")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "model_size": "tiny.en",
    "compute_type": "default",
    "language": "en",
    "auto_detect_language": True,
    "translate": False,
    "target_language": "en",
    "accent_color": "#B57EDC",  # Lavender purple
    "use_caelestia_colors": False,
    "input_device": None,
    "hotkey_keycode": [29, 29],  # Double KEY_LEFTCTRL
    "hotkey_name": "Double Control_L",
    "theme_mode": "system", # system, dark, light
    "use_groq": False,
    "groq_api_key": "",
    "groq_model": "whisper-large-v3-turbo",
    "enable_llm_rewrite": False,
    "llm_backend": "Built-in (Llama.cpp)",
    "llama_repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
    "llama_filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "ollama_endpoint": "http://127.0.0.1:11434",
    "ollama_model": "llama3.2",
    "llm_system_prompt": "You are an STT editor. Remove filler words, fix stutters and self-corrections, correct obvious transcription errors, and add punctuation. Output only the cleaned text.",
    "show_tray": True
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            # Merge with default config to ensure all keys exist
            for k, v in DEFAULT_CONFIG.items():
                if k not in config:
                    config[k] = v
            return config
    except Exception as e:
        print(f"Error loading config: {e}")
        return DEFAULT_CONFIG.copy()

def save_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

HISTORY_FILE = os.path.join(CONFIG_DIR, "history.json")

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading history: {e}")
        return []

def add_history(text):
    if not text or not text.strip():
        return
    history = load_history()
    # Add to beginning of list
    import time
    entry = {
        "timestamp": time.time(),
        "text": text.strip()
    }
    history.insert(0, entry)
    
    # Keep up to 100 entries max to prevent file from growing indefinitely
    history = history[:100]
    
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
    except Exception as e:
        print(f"Error saving history: {e}")
