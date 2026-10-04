from __future__ import annotations

import fcntl
import json
import os
import socket
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2

MAX_PAYLOAD = 4096


def runtime_lock_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "protocol7-chatgpt-voice.lock"


def runtime_socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "protocol7-chatgpt-voice.sock"


STATUS_SCRIPT = r'''(() => {
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const label = (el) => [
    el?.getAttribute?.('aria-label') || '',
    el?.getAttribute?.('title') || '',
    el?.getAttribute?.('data-testid') || '',
    el?.textContent || ''
  ].join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
  const entries = [...document.querySelectorAll('button,[role="button"],a')]
    .filter(visible)
    .map(el => ({el, label: label(el), href: el.getAttribute?.('href') || ''}));
  const loginVisible = entries.some(({label, href}) =>
    /^(log in|login|sign up|sign up for free|log in or create account)$/.test(label) || /\/auth\/login/.test(href)
  );
  const activeControl = entries.some(({label}) =>
    /((end|exit|leave|close|stop).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|exit|leave|close|stop))/.test(label)
  );
  const voiceReady = entries.some(({label, href}) =>
    label === 'start voice' || /(start|open|enter|begin).*(voice|sprach|stimm)/.test(label) || /[?&]mode=voice(?:$|&)/.test(href)
  );
  const active = activeControl || /[?&]mode=voice(?:$|&)/.test(location.search);
  const phase = active ? 'active' : (loginVisible ? 'needs-login' : (voiceReady ? 'ready' : 'unavailable'));
  return JSON.stringify({
    ok:true, phase, active, loggedOut:loginVisible, authenticated:!loginVisible,
    voiceReady, url:location.href, title:document.title
  });
})()'''

VOICE_SCRIPT = r'''(() => {
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const label = (el) => [
    el?.getAttribute?.('aria-label') || '', el?.getAttribute?.('title') || '',
    el?.getAttribute?.('data-testid') || '', el?.textContent || ''
  ].join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
  const entries = [...document.querySelectorAll('button,[role="button"],a')]
    .filter(visible).map(el => ({el, label: label(el), href: el.getAttribute?.('href') || ''}));
  const loginVisible = entries.some(({label, href}) =>
    /^(log in|login|sign up|sign up for free|log in or create account)$/.test(label) || /\/auth\/login/.test(href)
  );
  if (loginVisible) return JSON.stringify({ok:false,result:'needs-login',phase:'needs-login'});
  const active = entries.some(({label}) =>
    /((end|exit|leave|close|stop).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|exit|leave|close|stop))/.test(label)
  ) || /[?&]mode=voice(?:$|&)/.test(location.search);
  if (active) return JSON.stringify({ok:true,result:'already-active',phase:'active'});
  const voice = entries.find(({label,href}) =>
    label === 'start voice' || /(start|open|enter|begin).*(voice|sprach|stimm)/.test(label) || /[?&]mode=voice(?:$|&)/.test(href)
  );
  if (!voice?.el) return JSON.stringify({ok:false,result:'voice-control-unavailable',phase:'unavailable'});
  voice.el.focus?.({preventScroll:true});
  voice.el.click();
  return JSON.stringify({ok:true,result:'clicked',phase:'starting'});
})()'''

END_SCRIPT = r'''(() => {
  const visible = (el) => {
    const style = getComputedStyle(el); const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const label = (el) => [el?.getAttribute?.('aria-label') || '', el?.getAttribute?.('title') || '', el?.textContent || '']
    .join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
  const entries = [...document.querySelectorAll('button,[role="button"]')].filter(visible).map(el => ({el,label:label(el)}));
  const end = entries.find(({label}) =>
    /((end|exit|leave|close|stop).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|exit|leave|close|stop))/.test(label)
  );
  if (!end) return JSON.stringify({ok:true,result:'already-inactive'});
  end.el.click();
  return JSON.stringify({ok:true,result:'clicked-end'});
})()'''


class VoiceRuntime:
    def __init__(self):
        # Exactly one WebKit runtime may own the persistent profile/socket.
        # This prevents stale development/runtime copies from racing each
        # other and corrupting debug/auth/Voice state.
        lock_path = runtime_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = lock_path.open("a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit("Tabby ChatGPT Voice runtime is already running") from error

        home = Path.home()
        data_root = home / ".local/share/protocol-7/chatgpt-voice"
        cache_root = home / ".cache/protocol-7/chatgpt-voice"
        data_root.mkdir(parents=True, exist_ok=True)
        cache_root.mkdir(parents=True, exist_ok=True)
        # The profile contains authentication cookies/credentials. Keep the
        # profile private even if the user's default umask is permissive.
        data_root.chmod(0o700)
        cache_root.chmod(0o700)

        manager = WebKit2.WebsiteDataManager(
            base_data_directory=str(data_root), base_cache_directory=str(cache_root)
        )
        # WebKitGTK does NOT persist cookies just because the data/cache
        # directories are persistent. Explicitly opt into a durable cookie DB
        # so ChatGPT auth survives runtime and shell restarts.
        manager.set_persistent_credential_storage_enabled(True)
        cookie_manager = manager.get_cookie_manager()
        cookie_db = data_root / "cookies.sqlite"
        cookie_manager.set_persistent_storage(
            str(cookie_db), WebKit2.CookiePersistentStorage.SQLITE
        )
        self.cookie_db = cookie_db
        self.context = WebKit2.WebContext.new_with_website_data_manager(manager)
        self.child_windows: list[tuple[Gtk.Window, WebKit2.WebView]] = []

        self.view = WebKit2.WebView.new_with_context(self.context)
        self._configure_view(self.view, main=True)

        self.window = Gtk.Window(title="Tabby — ChatGPT sign in")
        self.window.set_default_size(560, 780)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.connect("delete-event", self._hide_instead)
        self.window.add(self.view)

        self.loaded = False
        self.login_visible = False
        self.debug_visible = False
        self._server = None
        self._server_thread = None
        self._stop = threading.Event()
        self.view.load_uri(os.environ.get("PROTOCOL7_CHATGPT_URL", "https://chatgpt.com/").strip() or "https://chatgpt.com/")

    def _configure_view(self, view: WebKit2.WebView, main: bool = False) -> None:
        settings = view.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_webrtc(True)
        settings.set_enable_media_stream(True)
        settings.set_enable_webaudio(True)
        settings.set_enable_media(True)
        settings.set_media_playback_requires_user_gesture(False)
        view.connect("permission-request", self._on_permission_request)
        view.connect("create", self._on_create)
        if main:
            view.connect("load-changed", self._on_main_load_changed)

    def _hide_instead(self, *args):
        # In debug mode, visibility is owned by the plugin settings toggle,
        # so the window close button cannot silently desync that setting.
        if self.debug_visible:
            return True
        self._hide_all_windows()
        return True

    def _on_main_load_changed(self, view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self.loaded = True

    def _on_permission_request(self, view, request):
        if isinstance(request, (WebKit2.UserMediaPermissionRequest, WebKit2.DeviceInfoPermissionRequest)):
            request.allow()
            return True
        return False

    def _on_create(self, source, navigation_action):
        child = WebKit2.WebView.new_with_context(self.context)
        self._configure_view(child)
        popup = Gtk.Window(title="Tabby — ChatGPT sign in")
        popup.set_default_size(560, 780)
        popup.set_skip_taskbar_hint(True)
        popup.set_skip_pager_hint(True)
        popup.add(child)
        self.child_windows.append((popup, child))

        def loaded(view, event):
            if event != WebKit2.LoadEvent.FINISHED:
                return
            uri = view.get_uri() or ""
            # OAuth providers often return in the popup. Shared context means
            # cookies are already available to the main view; refresh it so
            # the normal ChatGPT surface immediately reflects the login.
            if uri.startswith("https://chatgpt.com/") and "/auth/" not in uri:
                GLib.timeout_add(250, lambda: (self.view.reload(), False)[1])

        def closed(*args):
            try: popup.destroy()
            except Exception: pass
            self.child_windows[:] = [(w, v) for w, v in self.child_windows if w is not popup]
            GLib.timeout_add(250, lambda: (self.view.reload(), False)[1])
            return True

        child.connect("load-changed", loaded)
        child.connect("close", closed)
        popup.connect("delete-event", closed)
        popup.show_all()
        popup.present()
        return child

    def _eval(self, script: str, timeout: float = 4.0) -> dict:
        done = threading.Event()
        box: dict = {}

        def run():
            def callback(view, result):
                try:
                    js_result = view.run_javascript_finish(result)
                    raw = js_result.get_js_value().to_string()
                    box["result"] = json.loads(raw)
                except Exception as error:
                    box["result"] = {"ok": False, "result": "javascript-error", "error": str(error)}
                finally:
                    done.set()
            try:
                self.view.run_javascript(script, None, callback)
            except Exception as error:
                box["result"] = {"ok": False, "result": "javascript-start-error", "error": str(error)}
                done.set()
            return False

        GLib.idle_add(run)
        if not done.wait(timeout):
            return {"ok": False, "result": "timeout"}
        return box.get("result", {"ok": False, "result": "empty-result"})

    def _show_login(self) -> None:
        self.login_visible = True
        def show():
            self.window.show_all(); self.window.present(); return False
        GLib.idle_add(show)

    def _set_debug_visible(self, enabled: bool) -> None:
        self.debug_visible = bool(enabled)
        def apply():
            if self.debug_visible:
                self.window.set_title("Tabby — ChatGPT WebView Debug")
                self.window.show_all()
                self.window.present()
            elif not self.login_visible:
                self.window.hide()
            return False
        GLib.idle_add(apply)

    def _hide_all_windows(self) -> None:
        self.login_visible = False
        def hide():
            if self.debug_visible:
                self.window.set_title("Tabby — ChatGPT WebView Debug")
                self.window.show_all()
            else:
                self.window.hide()
            for popup, _ in list(self.child_windows):
                try: popup.hide()
                except Exception: pass
            return False
        GLib.idle_add(hide)

    def _status(self) -> dict:
        if not self.loaded:
            return {"ok": True, "loaded": False, "phase": "loading", "active": False}
        result = self._eval(STATUS_SCRIPT)
        result["loaded"] = True
        result["debugVisible"] = self.debug_visible
        return result

    def _activate(self) -> dict:
        status = self._status()
        if status.get("phase") == "needs-login":
            self._show_login()
            return {"ok": False, "result": "needs-login", "phase": "needs-login"}
        if status.get("phase") == "active":
            self._hide_all_windows()
            return {"ok": True, "result": "already-active", "phase": "active"}
        if status.get("phase") != "ready":
            return {"ok": False, "result": "voice-control-unavailable", "phase": status.get("phase", "unavailable")}

        clicked = self._eval(VOICE_SCRIPT)
        if not clicked.get("ok"):
            if clicked.get("phase") == "needs-login": self._show_login()
            return clicked

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            time.sleep(0.25)
            status = self._status()
            if status.get("phase") == "active":
                self._hide_all_windows()
                return {"ok": True, "result": "active", "phase": "active"}
            if status.get("phase") == "needs-login":
                self._show_login()
                return {"ok": False, "result": "needs-login", "phase": "needs-login"}
        return {"ok": False, "result": "voice-not-active", "phase": status.get("phase", "unknown")}

    def command(self, payload: dict) -> dict:
        command = str(payload.get("command", "")).strip().lower()
        if command == "ping": return {"ok": True, "loaded": self.loaded}
        if command == "status": return self._status()
        if command == "activate": return self._activate()
        if command == "end":
            result = self._eval(END_SCRIPT) if self.loaded else {"ok": True, "result": "already-inactive"}
            self._hide_all_windows(); return result
        if command == "show-login": self._show_login(); return {"ok": True}
        if command == "hide": self._hide_all_windows(); return {"ok": True}
        if command == "set-debug":
            self._set_debug_visible(bool(payload.get("enabled", False)))
            return {"ok": True, "debugVisible": self.debug_visible}
        return {"ok": False, "result": "unsupported-command"}

    def _serve(self) -> None:
        path = runtime_socket_path()
        try: path.unlink()
        except FileNotFoundError: pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path)); os.chmod(path, 0o600); server.listen(8); server.settimeout(0.3)
        self._server = server
        while not self._stop.is_set():
            try: conn, _ = server.accept()
            except socket.timeout: continue
            except OSError: break
            with conn:
                try:
                    data = conn.recv(MAX_PAYLOAD + 1)
                    if not data or len(data) > MAX_PAYLOAD:
                        result = {"ok": False, "result": "invalid-payload"}
                    else:
                        payload = json.loads(data.decode("utf-8"))
                        result = self.command(payload if isinstance(payload, dict) else {})
                except Exception as error:
                    result = {"ok": False, "result": "request-error", "error": str(error)}
                try: conn.sendall(json.dumps(result, separators=(",", ":")).encode("utf-8"))
                except OSError: pass
        try: server.close()
        except OSError: pass
        try: path.unlink()
        except FileNotFoundError: pass

    def start(self) -> None:
        self._server_thread = threading.Thread(target=self._serve, name="tabby-voice-ipc", daemon=True)
        self._server_thread.start(); Gtk.main()


if __name__ == "__main__":
    VoiceRuntime().start()
