import builtins
import datetime as _dt_module

_original_print = builtins.print

def _timestamped_print(*args, **kwargs):
    stamp = _dt_module.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _original_print(f"[{stamp}]", *args, **kwargs)

builtins.print = _timestamped_print

import sys
import os


def _ensure_layer_shell_preloaded():
    """Put gtk4-layer-shell in the process before libwayland-client gets there.

    If it loses that race the overlay stops being a layer surface -- it still
    runs, it just is not anchored to the screen edge any more, which is a
    confusing way to fail. The ctypes preload in ui_linux is the usual trick and
    it is not dependable: depending on what the interpreter has already pulled
    in, libwayland can be loaded first. The library's own guidance is LD_PRELOAD,
    which cannot lose, so set it and re-exec once.
    """
    if sys.platform == "win32" or os.environ.get("PROTOCOL7_PRELOADED"):
        return

    for prefix in ("/usr/lib", "/usr/lib64", "/usr/local/lib"):
        for name in ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so"):
            path = os.path.join(prefix, name)
            if not os.path.exists(path):
                continue
            current = os.environ.get("LD_PRELOAD", "")
            if path in current:
                return
            os.environ["LD_PRELOAD"] = f"{path}:{current}" if current else path
            os.environ["PROTOCOL7_PRELOADED"] = "1"
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                return  # start unpreloaded rather than not at all
    return


_ensure_layer_shell_preloaded()

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
