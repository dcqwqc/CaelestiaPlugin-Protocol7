import builtins
import datetime as _dt_module

_original_print = builtins.print

def _timestamped_print(*args, **kwargs):
    stamp = _dt_module.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _original_print(f"[{stamp}]", *args, **kwargs)

builtins.print = _timestamped_print

import sys
import os


# Linux presentation now lives inside Caelestia's native QML shell.
# Protocol-7 is backend-only on Linux and no longer owns a Wayland overlay.

from config import load_config

if __name__ == "__main__":
    config = load_config()
    
    if "--settings" in sys.argv:
        if sys.platform == "win32":
            import settings_ui_win
            settings_ui_win.run_settings(config)
        else:
            import settings_ui_linux
            settings_ui_linux.run_settings(config)
        sys.exit(0)
        
    if sys.platform == "win32":
        import main_win
        app = main_win.Protocol7App()
        app.run()
    else:
        import main_linux
        app = main_linux.Protocol7App()
        app.run()
