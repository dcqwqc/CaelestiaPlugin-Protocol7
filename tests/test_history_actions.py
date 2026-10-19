import json
from pathlib import Path
from unittest.mock import patch

from history_actions import selected_text, paste_timestamp


def test_selects_matching_timestamp_only(tmp_path: Path):
    history = tmp_path / "history.json"
    history.write_text(json.dumps([
        {"timestamp": 123.25, "text": "First dictation"},
        {"timestamp": 125.1, "text": "Second dictation"},
    ]))
    assert selected_text("125.1", history) == "Second dictation"
    assert selected_text("125.2", history) == ""
    assert selected_text("nan", history) == ""
    assert selected_text("123.25", tmp_path / "absent") == ""


def test_paste_short_text_does_not_touch_clipboard(tmp_path: Path):
    path = tmp_path / "history.json"
    path.write_text('[{"timestamp":123,"text":"hello world"}]')
    with patch("history_actions.type_text", return_value=True) as typed, patch("history_actions.clipboard_paste") as copied:
        assert paste_timestamp("123", path)
        typed.assert_called_once_with("hello world")
        copied.assert_not_called()


def test_long_codex_text_avoids_image_clipboard_race(tmp_path: Path):
    path = tmp_path / "history.json"
    message = "example " * 20
    path.write_text(json.dumps([{"timestamp":123,"text":message}]))
    with patch("history_actions.focused_is_sumi", return_value=False), patch("history_actions.clipboard_paste") as copied, patch("history_actions.type_text", return_value=True) as typed:
        assert paste_timestamp("123", path)
        copied.assert_not_called()
        typed.assert_called_once_with(message)


def test_untrusted_or_missing_entry_fails_closed(tmp_path: Path):
    path = tmp_path / "history.json"
    path.write_text('[{"timestamp":123,"text":5}]')
    with patch("history_actions.type_text") as typed:
        assert not paste_timestamp("123", path)
        typed.assert_not_called()



def test_sumi_remains_compatible_with_clipboard_paste(tmp_path: Path):
    path = tmp_path / "history.json"
    message = "Sumi entry " * 20
    path.write_text(json.dumps([{"timestamp": 123, "text": message}]))
    with patch("history_actions.focused_is_sumi", return_value=True), patch("history_actions.clipboard_paste", return_value=True) as copied:
        assert paste_timestamp("123", path)
        copied.assert_called_once_with(message)
