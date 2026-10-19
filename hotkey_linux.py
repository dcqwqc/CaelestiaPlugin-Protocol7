import evdev
import threading
import time
import os

HOTKEY_CAPTURE_FLAG = "/tmp/protocol7_hotkey_capture"
FOCUS_KEYS = {
    evdev.ecodes.KEY_TAB, evdev.ecodes.KEY_ENTER,
    evdev.ecodes.KEY_F6, evdev.ecodes.KEY_ESC,
    evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT,
}
POINTER_BUTTONS = {
    evdev.ecodes.BTN_LEFT, evdev.ecodes.BTN_RIGHT,
    evdev.ecodes.BTN_MIDDLE, evdev.ecodes.BTN_TOUCH,
}

class HotkeyListener:
    def __init__(self, keycode, on_trigger_callback):
        self.keycode = keycode
        self.on_trigger_callback = on_trigger_callback
        self.running = False
        self.last_tap_time = 0
        # A sequence tap must be intentional and quick.  We measure between
        # completed taps (key releases), not key-downs, so modifier shortcuts
        # such as Ctrl+C followed by Ctrl+V cannot masquerade as Double Ctrl.
        self.double_tap_threshold = 0.46  # allow deliberate double-taps under CPU load
        self.max_tap_duration = 0.35      # reject held modifiers, allow normal key presses
        self.min_tap_gap = 0.035          # reject duplicate/echoed events
        # Guards the tap counters. One listener thread runs per input device,
        # and they all share this state -- two devices reporting the same key,
        # or a virtual keyboard echoing it, could otherwise interleave and
        # leave the count somewhere neither tap expected.
        self._tap_lock = __import__('threading').Lock()
        self.tap_count = 0
        self._focus_count = 0
        self._focus_lock = threading.Lock()

        # For multi-key combos
        self.pressed_keys = set()
        
    def _trigger(self):
        """Fire the configured action unless the settings UI is recording a new binding."""
        try:
            if os.path.exists(HOTKEY_CAPTURE_FLAG):
                age = time.time() - os.path.getmtime(HOTKEY_CAPTURE_FLAG)
                if age < 10:
                    return
                # Crash-safe cleanup: never let an abandoned capture flag disable dictation forever.
                os.unlink(HOTKEY_CAPTURE_FLAG)
        except OSError:
            pass
        self.on_trigger_callback()

    def focus_epoch(self):
        """Count user actions likely to relocate the caret, not mouse movement."""
        with self._focus_lock:
            return self._focus_count

    def _record_focus_action(self, code, value):
        if value == 1 and code in (FOCUS_KEYS | POINTER_BUTTONS):
            with self._focus_lock:
                self._focus_count += 1

    def find_pointers(self, keyboard_paths=None):
        keyboards = set(keyboard_paths or [])
        pointers = []
        for path in evdev.list_devices():
            if path in keyboards:
                continue
            try:
                dev = evdev.InputDevice(path)
                keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
                if any(button in keys for button in POINTER_BUTTONS):
                    pointers.append(dev)
            except OSError:
                continue
        return pointers

    def _listen_pointer(self, device):
        try:
            for event in device.read_loop():
                if not self.running:
                    break
                if event.type == evdev.ecodes.EV_KEY:
                    self._record_focus_action(event.code, event.value)
        except OSError:
            pass

    def find_keyboards(self):
        keyboards = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                capabilities = dev.capabilities()
                if evdev.ecodes.EV_KEY in capabilities:
                    if evdev.ecodes.KEY_A in capabilities[evdev.ecodes.EV_KEY]:
                        keyboards.append(dev)
            except Exception:
                continue
        return keyboards

    def _listen_device(self, device):
        # Sequence state is local to each physical input device.  This lets us
        # prove that a Ctrl press was a standalone tap before adding it to the
        # shared multi-tap counter.
        sequence_key_down = False
        sequence_key_down_time = 0.0
        sequence_disqualified = False

        try:
            for event in device.read_loop():
                if not self.running:
                    break
                if event.type == evdev.ecodes.EV_KEY:
                    self._record_focus_action(event.code, event.value)
                    # Update pressed keys state
                    if event.value == 1:
                        self.pressed_keys.add(event.code)
                    elif event.value == 0:
                        self.pressed_keys.discard(event.code)

                    # If it's a list (combo or sequence)
                    if isinstance(self.keycode, list):
                        # Detect a multi-tap sequence of the SAME key.
                        if len(set(self.keycode)) == 1:
                            target = self.keycode[0]

                            if event.code == target:
                                if event.value == 1:  # key down
                                    sequence_key_down = True
                                    sequence_key_down_time = time.monotonic()
                                    sequence_disqualified = False

                                elif event.value == 0 and sequence_key_down:  # key up
                                    current_time = time.monotonic()
                                    tap_duration = current_time - sequence_key_down_time
                                    valid_tap = (
                                        not sequence_disqualified
                                        and tap_duration <= self.max_tap_duration
                                    )
                                    sequence_key_down = False

                                    fire = False
                                    with self._tap_lock:
                                        if not valid_tap:
                                            self.tap_count = 0
                                            self.last_tap_time = 0
                                        else:
                                            time_diff = (
                                                current_time - self.last_tap_time
                                                if self.last_tap_time
                                                else float("inf")
                                            )
                                            if (
                                                self.tap_count > 0
                                                and self.min_tap_gap <= time_diff <= self.double_tap_threshold
                                            ):
                                                self.tap_count += 1
                                            else:
                                                self.tap_count = 1

                                            self.last_tap_time = current_time
                                            if self.tap_count >= len(self.keycode):
                                                self.tap_count = 0
                                                self.last_tap_time = 0
                                                fire = True

                                    if fire:
                                        self._trigger()

                            # Any other key pressed while the sequence key is
                            # held turns this into a modifier shortcut, not a
                            # tap.  Reset the whole pending sequence so
                            # Ctrl+C -> Ctrl+V can never trigger Protocol 7.
                            elif event.value == 1 and sequence_key_down:
                                sequence_disqualified = True
                                with self._tap_lock:
                                    self.tap_count = 0
                                    self.last_tap_time = 0

                        else:
                            # It's a simultaneous combo (e.g. Ctrl + Shift + R)
                            if event.value == 1 and all(k in self.pressed_keys for k in self.keycode):
                                self._trigger()

                    # If it's a single key (single tap)
                    elif isinstance(self.keycode, int):
                        if event.value == 1 and event.code == self.keycode:
                            # SINGLE TAP triggers immediately
                            self._trigger()
        except OSError:
            pass  # Device disconnected

    def _watchdog(self):
        active_threads = {}
        pointer_threads = {}
        while self.running:
            try:
                keyboards = self.find_keyboards()
                current_paths = {kb.path: kb for kb in keyboards}
                
                # Start threads for new keyboards
                for path, kb in current_paths.items():
                    if path not in active_threads or not active_threads[path].is_alive():
                        t = threading.Thread(target=self._listen_device, args=(kb,), daemon=True)
                        t.start()
                        active_threads[path] = t

                for pointer in self.find_pointers(current_paths):
                    if pointer.path not in pointer_threads or not pointer_threads[pointer.path].is_alive():
                        t = threading.Thread(target=self._listen_pointer, args=(pointer,), daemon=True)
                        t.start()
                        pointer_threads[pointer.path] = t
            except Exception as e:
                pass # Prevent any OS/udev race conditions from killing the watchdog
                
            time.sleep(2.0)  # Check every 2 seconds for new/reconnected devices

    def start(self):
        self.running = True
        t = threading.Thread(target=self._watchdog, daemon=True)
        t.start()

    def stop(self):
        self.running = False
