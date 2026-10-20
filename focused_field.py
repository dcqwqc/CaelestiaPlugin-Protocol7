#!/usr/bin/env python3
"""Read ONLY the focused editable's bounds from AT-SPI, not any typed text.

Runs as a short-lived, time-bounded process: a crashed/unresponsive app's
accessibility bridge cannot freeze the dictation hotkey.
"""
from __future__ import annotations

import json
import sys

TEXT_ROLES = frozenset({
    "entry", "password text", "autocomplete",
})


def app_matches(name: str, application_class: str) -> bool:
    name = name.casefold().strip()
    cls = application_class.casefold().strip()
    if not name or not cls:
        return False
    if "zen" in cls:
        return name in ("zen", "zen browser")
    if "firefox" in cls:
        return "firefox" in name
    if "ghostty" in cls:
        return "ghostty" in name
    if "chromium" in cls or "chrome" in cls:
        return "chrom" in name or "chrome" in name
    return name in cls or cls in name


def field_bounds(application_class: str, pid: int = 0):
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        desktop = Atspi.get_desktop(0)
        state = Atspi.StateSet.new([Atspi.StateType.FOCUSED])
        match = Atspi.CollectionMatchType.ALL
        rule = Atspi.MatchRule.new(
            state, match, {}, match, [],
            Atspi.CollectionMatchType.INVALID, [], match, False,
        )
        found = []
        for idx in range(min(desktop.get_child_count(), 80)):
            app = desktop.get_child_at_index(idx)
            # Flatpak application's accessibility PID can differ from the
            # compositor's host PID; app name is then the safe identity.
            if app.get_process_id() != pid and not app_matches(app.get_name(), application_class):
                continue
            for win_idx in range(min(app.get_child_count(), 30)):
                win = app.get_child_at_index(win_idx)
                for acc in Atspi.Collection.get_matches(
                    win, rule, Atspi.CollectionSortOrder.CANONICAL, 12, True
                ):
                    states = acc.get_state_set()
                    role = acc.get_role_name().casefold()
                    if not states.contains(Atspi.StateType.FOCUSED):
                        continue
                    if not (states.contains(Atspi.StateType.EDITABLE) or role in TEXT_ROLES):
                        continue
                    if not states.contains(Atspi.StateType.SHOWING):
                        continue
                    component = acc.get_component()
                    if component is None:
                        continue
                    rect = component.get_extents(Atspi.CoordType.SCREEN)
                    if rect.width < 12 or rect.height < 12:
                        continue
                    if rect.width > 12000 or rect.height > 12000:
                        continue
                    found.append([rect.x, rect.y, rect.width, rect.height])
        # Only one focus target is trustworthy; ambiguity is not a reason to
        # place an overlay near the wrong field.
        return found[0] if len(found) == 1 else None
    except Exception:
        return None


def main() -> None:
    try:
        pid = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        value = field_bounds(sys.argv[1], pid)
        print(json.dumps(value, separators=(",", ":")))
    except (ValueError, IndexError):
        print("null")


if __name__ == "__main__":
    main()
