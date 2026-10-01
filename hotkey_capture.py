#!/usr/bin/env python3
"""Capture a Protocol 7 hotkey from Linux evdev and print one JSON result."""

import json
import os
import select
import time

import evdev
from evdev import ecodes

FLAG = "/tmp/protocol7_hotkey_capture"


def keyboard_devices():
    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps and ecodes.KEY_A in caps[ecodes.EV_KEY]:
                devices.append(dev)
        except Exception:
            pass
    return devices


def display_name(code):
    aliases = {
        ecodes.KEY_LEFTCTRL: "Control_L",
        ecodes.KEY_RIGHTCTRL: "Control_R",
        ecodes.KEY_LEFTSHIFT: "Shift_L",
        ecodes.KEY_RIGHTSHIFT: "Shift_R",
        ecodes.KEY_LEFTALT: "Alt_L",
        ecodes.KEY_RIGHTALT: "Alt_R",
        ecodes.KEY_LEFTMETA: "Super_L",
        ecodes.KEY_RIGHTMETA: "Super_R",
        ecodes.KEY_SPACE: "Space",
        ecodes.KEY_ENTER: "Enter",
        ecodes.KEY_ESC: "Escape",
    }
    if code in aliases:
        return aliases[code]
    name = ecodes.KEY.get(code, f"Key {code}")
    if isinstance(name, (list, tuple)):
        name = name[0]
    return str(name).removeprefix("KEY_").replace("_", " ").title()


def result_name(codes):
    names = [display_name(c) for c in codes]
    if len(codes) == 1:
        return names[0]
    if len(set(codes)) == 1:
        taps = {2: "Double", 3: "Triple", 4: "Quadruple"}
        return f"{taps.get(len(codes), str(len(codes)) + 'x')} {names[0]}"
    return " + ".join(names)


def main():
    devices = keyboard_devices()
    if not devices:
        print(json.dumps({"error": "No keyboard input devices available"}))
        return

    open(FLAG, "w").close()
    pressed = set()
    codes = []
    deadline = time.monotonic() + 3.0

    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select(devices, [], [], 0.08)
            for dev in ready:
                try:
                    for event in dev.read():
                        if event.type != ecodes.EV_KEY:
                            continue
                        if event.value == 1 and event.code not in pressed:
                            pressed.add(event.code)
                            codes.append(event.code)
                        elif event.value == 0:
                            pressed.discard(event.code)
                except OSError:
                    pass
    finally:
        try:
            os.unlink(FLAG)
        except FileNotFoundError:
            pass
        for dev in devices:
            try:
                dev.close()
            except Exception:
                pass

    if not codes:
        print(json.dumps({"error": "No key was captured"}))
        return

    # Avoid accidental long sequences caused by interacting with the UI after recording.
    codes = codes[:4]
    payload = codes[0] if len(codes) == 1 else codes
    print(json.dumps({"keycode": payload, "name": result_name(codes)}))


if __name__ == "__main__":
    main()
