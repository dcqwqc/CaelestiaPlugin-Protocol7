from __future__ import annotations

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


def runtime_socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "protocol7-chatgpt-voice.sock"


VOICE_SCRIPT = r'''(() => {
  const label = (el) => [
    el?.getAttribute?.('aria-label') || '',
    el?.getAttribute?.('title') || '',
    el?.getAttribute?.('data-testid') || '',
    el?.textContent || ''
  ].join(' ').replace(/\s+/g, ' ').trim().toLowerCase();

  const els = [...document.querySelectorAll('button,[role="button"],a')];
  const entries = els.map(el => ({el, label: label(el)}));
  const loggedOut = entries.some(({label}) => /^(log in|sign up for free|log in or create account)$/.test(label));
  const active = entries.find(({label}) =>
    /((end|exit|leave|close).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|exit|leave|close))/.test(label)
  );
  if (active) return JSON.stringify({ok:true, result:'already-active', active:true, loggedOut:false});

  const voice = entries.find(({label}) =>
    /(start voice mode|open voice mode|enter voice mode|voice mode|sprachmodus|stimmenmodus)/.test(label)
  ) || entries.find(({label}) =>
    /(voice|sprach|stimm)/.test(label) &&
    !/(dictat|transcrib|speech to text|start dictation|microphone|mic|stop|end|leave|exit|close)/.test(label)
  );

  if (voice?.el) {
    voice.el.focus?.({preventScroll:true});
    voice.el.click();
    return JSON.stringify({ok:true, result:'clicked', active:true, loggedOut:false});
  }

  return JSON.stringify({ok:false, result: loggedOut ? 'needs-login' : 'voice-button-not-found', active:false, loggedOut});
})()'''

STATUS_SCRIPT = r'''(() => {
  const label = (el) => [
    el?.getAttribute?.('aria-label') || '',
    el?.getAttribute?.('title') || '',
    el?.getAttribute?.('data-testid') || '',
    el?.textContent || ''
  ].join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
  const entries = [...document.querySelectorAll('button,[role="button"],a')].map(el => ({el, label: label(el)}));
  const loggedOut = entries.some(({label}) => /^(log in|sign up for free|log in or create account)$/.test(label));
  const active = entries.some(({label}) =>
    /((end|exit|leave|close).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|exit|leave|close))/.test(label)
  );
  return JSON.stringify({ok:true, active, loggedOut, url:location.href, title:document.title});
})()'''

END_SCRIPT = r'''(() => {
  const label = (el) => [el?.getAttribute?.('aria-label') || '', el?.getAttribute?.('title') || '', el?.textContent || '']
    .join(' ').replace(/\s+/g, ' ').trim().toLowerCase();
  const entries = [...document.querySelectorAll('button,[role="button"]')].map(el => ({el,label:label(el)}));
  const end = entries.find(({label}) =>
    /((end|exit|leave|close).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|exit|leave|close))/.test(label)
  );
  if (!end) return JSON.stringify({ok:true,result:'already-inactive'});
  end.el.click();
  return JSON.stringify({ok:true,result:'clicked-end'});
})()'''


class VoiceRuntime:
    def __init__(self):
        home = Path.home()
        data_root = home / ".local/share/protocol-7/chatgpt-voice"
        cache_root = home / ".cache/protocol-7/chatgpt-voice"
        data_root.mkdir(parents=True, exist_ok=True)
        cache_root.mkdir(parents=True, exist_ok=True)

        manager = WebKit2.WebsiteDataManager(
            base_data_directory=str(data_root),
            base_cache_directory=str(cache_root),
        )
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
        self.view = WebKit2.WebView.new_with_context(context)
        settings = self.view.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_webrtc(True)
        settings.set_enable_media_stream(True)
        settings.set_enable_webaudio(True)
        settings.set_enable_media(True)
        settings.set_media_playback_requires_user_gesture(False)

        self.window = Gtk.Window(title="Tabby — ChatGPT sign in")
        self.window.set_default_size(520, 760)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.connect("delete-event", self._hide_instead)
        self.window.add(self.view)

        self.loaded = False
        self.view.connect("load-changed", self._on_load_changed)
        self.view.connect("permission-request", self._on_permission_request)
        self.view.load_uri(os.environ.get("PROTOCOL7_CHATGPT_URL", "https://chatgpt.com/").strip() or "https://chatgpt.com/")

        self._server = None
        self._server_thread = None
        self._stop = threading.Event()

    def _hide_instead(self, *args):
        self.window.hide()
        return True

    def _on_load_changed(self, view, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self.loaded = True

    def _on_permission_request(self, view, request):
        if isinstance(request, (WebKit2.UserMediaPermissionRequest, WebKit2.DeviceInfoPermissionRequest)):
            request.allow()
            return True
        return False

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
        def show():
            self.window.show_all()
            self.window.present()
            return False
        GLib.idle_add(show)

    def _hide_window(self) -> None:
        GLib.idle_add(lambda: (self.window.hide(), False)[1])

    def command(self, payload: dict) -> dict:
        command = str(payload.get("command", "")).strip().lower()
        if command == "ping":
            return {"ok": True, "loaded": self.loaded}
        if command == "status":
            if not self.loaded:
                return {"ok": True, "active": False, "loaded": False}
            result = self._eval(STATUS_SCRIPT)
            result["loaded"] = True
            return result
        if command == "activate":
            if not self.loaded:
                return {"ok": False, "result": "loading"}
            result = self._eval(VOICE_SCRIPT)
            if result.get("result") == "needs-login":
                self._show_login()
            elif result.get("ok"):
                self._hide_window()
            return result
        if command == "end":
            result = self._eval(END_SCRIPT) if self.loaded else {"ok": True, "result": "already-inactive"}
            self._hide_window()
            return result
        if command == "show-login":
            self._show_login()
            return {"ok": True}
        if command == "hide":
            self._hide_window()
            return {"ok": True}
        return {"ok": False, "result": "unsupported-command"}

    def _serve(self) -> None:
        path = runtime_socket_path()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(8)
        server.settimeout(0.3)
        self._server = server
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
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
                try:
                    conn.sendall(json.dumps(result, separators=(",", ":")).encode("utf-8"))
                except OSError:
                    pass
        try:
            server.close()
        except OSError:
            pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def start(self) -> None:
        self._server_thread = threading.Thread(target=self._serve, name="tabby-voice-ipc", daemon=True)
        self._server_thread.start()
        Gtk.main()


if __name__ == "__main__":
    runtime = VoiceRuntime()
    runtime.start()
