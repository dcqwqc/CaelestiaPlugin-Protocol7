"""Ghost Wave: detached processing, focus-safe delivery and explicit recovery."""
import threading
import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import numpy as np

from ghost_jobs import GhostJobs
from control_ipc import valid_command
from ghost_wave import Target, capture_target


class UI:
    def __init__(self):
        self.states = []
    def set_jobs(self, jobs): self.states.append(jobs)


class App:
    def __init__(self, text="test dictation"):
        self.ui_manager = UI()
        self.is_active = False
        self.pasted = []
        self.whisper_engine = type("Whisper", (), {"transcribe": lambda _, audio: text})()
        self.llm_rewriter = type("Rewrite", (), {"rewrite": lambda _, result: result})()
    def paste_text(self, text):
        self.pasted.append(text)
        return True


def await_status(manager, expected, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        with manager.lock:
            if any(job.status == expected for job in manager.jobs.values()):
                return
        time.sleep(.01)
    raise AssertionError(f"job never reached {expected}")


class GhostJobsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._home_patcher = patch("ghost_jobs.Path.home", return_value=Path(self._tmp.name))
        self._home_patcher.start()
        self.addCleanup(self._home_patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self._field_patcher = patch("ghost_wave.focused_rect", return_value=None)
        self._field_patcher.start()
        self.addCleanup(self._field_patcher.stop)
        self._vision_patcher = patch("composer_vision.capture_composer", return_value=None)
        self._vision_patcher.start()
        self.addCleanup(self._vision_patcher.stop)

    def make_manager(self, app):
        manager = GhostJobs(app)
        # Jobs expose status before their final persistence write completes.
        # Drain all writes before removing temporary state or home patches.
        self.addCleanup(manager.executor.shutdown, wait=True)
        return manager

    def test_switching_windows_retains_result_without_injection(self):
        app = App()
        manager = self.make_manager(app)
        target = Target("0x123", "zen", "eDP-1", 110, 220)
        with patch.object(Target, "same_window_active", return_value=False), patch("ghost_jobs.add_history"):
            job = manager.enqueue(np.ones(16000, dtype=np.float32), target)
            await_status(manager, "ready")
            self.assertEqual(app.pasted, [])
            self.assertEqual(manager.counts()["ready"], 1)
            self.assertEqual(manager.insert(job.id), {"ok": True, "inserted": True, "sent": False})
            self.assertEqual(app.pasted, ["test dictation"])

    def test_same_window_autoinserts_once(self):
        app = App()
        manager = self.make_manager(app)
        target = Target("0x123", "zen", "eDP-1", 110, 220)
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = manager.enqueue(np.ones(16000, dtype=np.float32), target)
            await_status(manager, "inserted")
            self.assertEqual(app.pasted, ["test dictation"])
            self.assertFalse(manager.insert(job.id)["ok"])

    def test_user_click_prevents_automatic_insertion(self):
        app = App()
        event, resume = threading.Event(), threading.Event()
        class Listener:
            current = 10
            def focus_epoch(self): return self.current
        app.hotkey = Listener()
        def transcribe(audio):
            event.set()
            resume.wait(1)
            return "safe result"
        app.whisper_engine.transcribe = transcribe
        manager = self.make_manager(app)
        target = Target("0x123", "zen", "eDP-1", 100, 100)
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = manager.enqueue(np.ones(16000, dtype=np.float32), target, focus_epoch=10)
            self.assertTrue(event.wait(1))
            app.hotkey.current = 11  # clicked another input within same Zen window
            resume.set()
            await_status(manager, "ready")
            self.assertEqual(app.pasted, [])
            self.assertFalse(manager.insert(job.id, require_target=True)["ok"])
            self.assertTrue(manager.insert(job.id)["ok"])

    def test_error_keeps_chip_and_can_retry(self):
        app = App("")
        manager = self.make_manager(app)
        with patch("ghost_jobs.add_history"):
            job = manager.enqueue(np.ones(16000, dtype=np.float32))
            await_status(manager, "error")
            self.assertEqual(manager.snapshot()[0]["status"], "error")
            app.whisper_engine.transcribe = lambda audio: "recovered"
            self.assertTrue(manager.retry(job.id)["ok"])
            await_status(manager, "ready")
            self.assertEqual(app.pasted, [])

    def test_cancelled_work_never_inserts_late_result(self):
        app = App()
        started = threading.Event()
        go = threading.Event()
        def slow(audio):
            started.set()
            go.wait(2)
            return "late"
        app.whisper_engine.transcribe = slow
        manager = self.make_manager(app)
        with patch("ghost_jobs.add_history"):
            job = manager.enqueue(np.ones(16000, dtype=np.float32))
            self.assertTrue(started.wait(1))
            self.assertTrue(manager.cancel(job.id)["ok"])
            go.set()
            time.sleep(.07)
            self.assertEqual(app.pasted, [])
            self.assertEqual(manager.snapshot(), [])

    def test_timeout_never_inserts_late_result(self):
        app = App()
        started, release = threading.Event(), threading.Event()
        def slow(audio):
            started.set()
            release.wait(1)
            return "late text"
        app.whisper_engine.transcribe = slow
        manager = self.make_manager(app)
        manager.job_timeout_seconds = 0.045
        with patch("ghost_jobs.add_history"):
            target = Target("0x123", "zen", "eDP-1", 100, 100)
            with patch.object(Target, "same_window_active", return_value=True):
                job = manager.enqueue(np.ones(16000, dtype=np.float32), target)
                self.assertTrue(started.wait(1))
                await_status(manager, "error")
                release.set()
                time.sleep(.1)
                self.assertEqual(app.pasted, [])
                self.assertEqual(job.status, "error")

    def test_pending_text_restored_without_stale_window_injection(self):
        app = App()
        manager = self.make_manager(app)
        target = Target("0xABC", "zen", "eDP-1", 200, 250)
        with patch("ghost_jobs.add_history"), patch.object(Target, "same_window_active", return_value=False):
            job = manager.enqueue(np.ones(16000, dtype=np.float32), target)
            await_status(manager, "ready")
            manager._persist()
        restored = self.make_manager(App())
        self.assertEqual(restored.snapshot()[0]["status"], "ready")
        self.assertFalse(restored.jobs[job.id].target.same_window_active())
        self.assertEqual(restored.jobs[job.id].text, "test dictation")

    def test_ipc_allows_only_bounded_job_actions(self):
        self.assertTrue(valid_command("insert:012345abcdef"))
        for bad in ("insert:bad", "insert:012345abcdef;rm", "insert:../../blah", "delete:012345abcdef"):
            self.assertFalse(valid_command(bad))

    def test_ready_result_archives_without_losing_history(self):
        app = App()
        manager = self.make_manager(app)
        manager.ready_visible_seconds = 0.07
        with patch("ghost_jobs.add_history") as saved:
            job = manager.enqueue(np.ones(16000, dtype=np.float32))
            await_status(manager, "ready")
            self.assertEqual(job.text, "test dictation")
            saved.assert_called_with("test dictation")
            await_status(manager, "archived")
            manager._persist()  # wait for archive's asynchronous disk write
            self.assertEqual(manager.snapshot(), [])
            self.assertEqual(app.pasted, [])

    def test_restored_result_schedules_auto_hide(self):
        app = App()
        manager = self.make_manager(app)
        with patch("ghost_jobs.add_history"):
            job = manager.enqueue(np.ones(16000, dtype=np.float32))
            await_status(manager, "ready")
            manager._persist()
        with patch.object(GhostJobs, "_archive_after") as schedule:
            restored = self.make_manager(App())
            self.assertEqual(restored.snapshot()[0]["id"], job.id)
            schedule.assert_called_once_with(job.id, "ready", restored.ready_visible_seconds)

    def test_ready_stays_visible_long_enough_for_manual_recovery(self):
        manager = self.make_manager(App())
        self.assertGreaterEqual(manager.ready_visible_seconds, 40.0)
        self.assertGreaterEqual(manager.error_visible_seconds, 12.0)

    def test_failed_recording_auto_dismisses(self):
        app = App("")
        manager = self.make_manager(app)
        manager.error_visible_seconds = .06
        job = manager.enqueue(np.ones(16000, dtype=np.float32))
        await_status(manager, "error")
        await_status(manager, "archived")
        manager._persist()  # wait for the archive write
        self.assertEqual(manager.snapshot(), [])

    def test_new_processing_job_is_not_hidden_by_old_error(self):
        app = App("")
        manager = self.make_manager(app)
        old = manager.enqueue(np.ones(16000, dtype=np.float32))
        await_status(manager, "error")
        hold = threading.Event()
        entered = threading.Event()
        def slow(audio):
            entered.set()
            hold.wait(1)
            return "fine"
        app.whisper_engine.transcribe = slow
        new = manager.enqueue(np.ones(16000, dtype=np.float32))
        self.assertTrue(entered.wait(1))
        self.assertEqual(manager.snapshot()[0]["id"], new.id)
        hold.set()

    def test_narrow_chatgpt_composer_stays_above_left_not_at_pointer(self):
        active = {"address": "0xab", "class": "chatgpt", "monitor": 0,
                  "at": [991, 21], "size": [908, 1158]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors]):
            result = capture_target({"x": 1050, "y": 200})
        self.assertEqual(result.source, "composer-layout")
        self.assertEqual((result.x, result.y), (1465, 1136))

    def test_window_left_edge_does_not_clamp_left_composer_placement(self):
        from ghost_wave import composer_fallback_anchor
        monitor = {"x": 0, "y": 0, "width": 2880, "height": 1800,
                   "scale": 1.5}
        # The ChatGPT window is tiled on the right. The pill may float into
        # screen space just left of that window without clamping to its edge.
        x, y = composer_fallback_anchor({"at": [991, 21], "size": [908, 1158]}, monitor)
        self.assertLess(x, 991)
        self.assertEqual((x, y), (983, 1098))

    def test_leftmost_window_falls_back_to_above_left(self):
        from ghost_wave import composer_fallback_anchor
        monitor = {"x": 0, "y": 0, "width": 2880, "height": 1800,
                   "scale": 1.5}
        x, y = composer_fallback_anchor({"at": [0, 21], "size": [908, 1158]}, monitor)
        self.assertGreaterEqual(x - 56, 72)  # preserve the Caelestia rail
        self.assertLess(y, 21 + 1158 - 114)

    def test_chatgpt_zen_tab_prefers_composer_over_cursor(self):
        active={"address":"0xab", "class":"app.zen_browser.zen", "title":"ChatGPT - conversation", "monitor":0, "at":[71,21], "size":[1828,1158]}
        monitors=[{"id":0,"name":"eDP-1","x":0,"y":0,"width":2880,"height":1800,"scale":1.5}]
        with patch("ghost_wave.hypr_json",side_effect=[active,monitors]):
            target=capture_target({"x":1400,"y":200})
        self.assertEqual((target.x,target.y,target.source),(477,1098,"composer-fallback"))

    def test_wide_chatgpt_places_ghost_beside_composer(self):
        active = {"address": "0xab", "class": "chatgpt", "monitor": 0,
                  "at": [71, 21], "size": [1828, 1158]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors]):
            result = capture_target({"x": 1700, "y": 80})
        self.assertEqual((result.x, result.y, result.source), (1072, 1136, "composer-layout"))
        # Right-hand pane begins at about x=1135; the pill ends precisely there.
        self.assertLessEqual(result.x + 56 + 7, 1135)

    def test_narrow_composer_pill_stays_within_window(self):
        from ghost_wave import composer_fallback_anchor
        active = {"at": [100, 30], "size": [420, 700]}
        monitor = {"x": 0, "y": 0, "width": 2880, "height": 1800,
                   "scale": 1.5}
        x, y = composer_fallback_anchor(active, monitor)
        self.assertGreaterEqual(x - 56, 72)
        self.assertLessEqual(x + 56, 520)
        self.assertLess(y, 30 + 700 - 114)

    def test_exact_focused_field_prefers_side_when_there_is_room(self):
        from ghost_wave import field_anchor
        active = {"monitor": 0, "at": [50, 20], "size": [1100, 1000]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 1920, "height": 1080, "scale": 1}]
        # Field starts deep enough into this window for a 136px pill to sit
        # immediately to its left, vertically aligned to its first line.
        mon, x, y = field_anchor((450, 820, 620, 68), active, monitors)
        self.assertEqual((x, y), (387, 853))

    def test_measured_composer_beats_layout_heuristic(self):
        active = {"address": "0xab", "class": "chatgpt", "monitor": 0,
                  "at": [71, 21], "size": [908, 1158]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        with patch("composer_vision.capture_composer", return_value=(608, 1115, 359, 43)):
            with patch("ghost_wave.hypr_json", side_effect=[active, monitors]):
                result = capture_target({"x": 175, "y": 240})
        self.assertEqual((result.x, result.y, result.source), (545, 1136, "composer-measured"))

    def test_chatgpt_split_layout_matches_mirai_two_pane_capture(self):
        from ghost_wave import chatgpt_split_composer
        monitor = {"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                   "width": 2880, "height": 1800, "scale": 1.5}
        x, y = chatgpt_split_composer({"at": [71, 21], "size": [908, 1158]}, monitor)
        self.assertEqual((x,y), (545, 1136))
        # Visual composer left x=608 and vertical center y=1136.5.
        self.assertLessEqual(abs((x+56+7)-608), 1)
        self.assertLessEqual(abs(y-1137), 2)

    def test_chatgpt_exact_editable_beats_composer_fallback(self):
        active = {"address": "0xab", "class": "chatgpt", "monitor": 0,
                  "at": [100, 21], "size": [908, 1100]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        with patch("ghost_wave.focused_rect", return_value=(180, 885, 700, 40)):
            with patch("ghost_wave.hypr_json", side_effect=[active, monitors]):
                result = capture_target({"x": 1000, "y": 250})
        self.assertEqual((result.x, result.y, result.source), (236, 859, "focused-field"))

    def test_selected_input_field_anchors_above_left_when_no_side_room(self):
        from ghost_wave import field_anchor
        active = {"address": "0xab", "class": "app.zen_browser.zen",
                  "monitor": 0, "at": [70, 20], "size": [900, 1100]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        rect = (100, 950, 760, 42)
        self.assertEqual(field_anchor(rect, active, monitors)[1:], (156, 924))
        with patch("ghost_wave.focused_rect", return_value=rect):
            with patch("ghost_wave.hypr_json", side_effect=[active, monitors, {"x": 920, "y": 240}]):
                target = capture_target()
        self.assertEqual((target.monitor, target.x, target.y), ("eDP-1", 156, 924))
        self.assertEqual(target.source, "focused-field")

    def test_field_on_scaled_screen_normalizes_physical_geometry(self):
        from ghost_wave import field_anchor
        active = {"monitor": 0, "at": [40, 10], "size": [1100, 1000]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        # Physical bounding box must be divided to match logical window.
        self.assertEqual(field_anchor((330, 1200, 1200, 72), active, monitors)[1:],
                         (157, 824))

    def test_outside_window_focus_rectangle_rejected(self):
        from ghost_wave import field_anchor
        active = {"monitor": 0, "at": [60, 20], "size": [800, 800]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 1920, "height": 1080, "scale": 1}]
        self.assertIsNone(field_anchor((1400, 900, 120, 40), active, monitors))

    def test_accessibility_unavailable_uses_pointer_snapshot(self):
        active = {"address": "0xab", "class": "zen", "monitor": 0,
                  "at": [70, 20], "size": [900, 1000]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 1920, "height": 1080, "scale": 1}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors]):
            target = capture_target({"x": 422, "y": 534})
        self.assertEqual((target.x, target.y), (422, 498))
        self.assertEqual(target.source, "pointer")

    def test_scaled_monitor_coordinates_use_logical_bounds(self):
        active = {"address": "0x1", "class": "zen", "monitor": 1,
                  "at": [1500, 250], "size": [650, 800]}
        monitors = [{"id": 1, "name": "eDP-1", "x": 0, "y": 0,
                     "width": 2880, "height": 1800, "scale": 1.5}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors, {"x": 1527, "y": 400}]):
            target = capture_target()
        self.assertEqual((target.x, target.y), (1527, 364))

    def test_pointer_monitor_wins_over_active_window(self):
        active = {"address": "0xab", "class": "zen", "monitor": 0,
                  "at": [100, 20], "size": [700, 850]}
        monitors = [
            {"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 2880, "height": 1800, "scale": 1.5},
            {"id": 1, "name": "DP-2", "x": 1920, "y": 0, "width": 1920, "height": 1080, "scale": 2.0},
        ]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors, {"x": 2340, "y": 480}]):
            target = capture_target()
        self.assertEqual((target.monitor, target.x, target.y), ("DP-2", 420, 444))
        self.assertEqual((target.address, target.app), ("0xab", "zen"))

    def test_hotkey_pointer_snapshot_is_kept_even_if_cursor_moves(self):
        active = {"address": "0xab", "class": "zen", "monitor": 0, "at": [20, 20], "size": [500, 500]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors]):
            target = capture_target({"x": 250, "y": 570})
        self.assertEqual((target.x, target.y), (250, 534))

    def test_anchor_does_not_follow_mouse_after_capture(self):
        active = {"address": "0xab", "class": "zen", "monitor": 0, "at": [20, 20], "size": [500, 500]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors, {"x": 320, "y": 540}]):
            target = capture_target()
        self.assertEqual((target.x, target.y), (320, 504))

    def test_absent_cursor_falls_back_to_window(self):
        active = {"address": "0xab", "class": "zen", "monitor": 0, "at": [100, 100], "size": [600, 700]}
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors, {}]):
            target = capture_target()
        self.assertEqual((target.x, target.y), (400, 414))

    def test_target_coordinates_are_monitor_relative(self):
        active = {"address": "0x1", "class": "zen", "monitor": 2, "at": [2200, 450], "size": [800, 500]}
        monitors = [{"id": 2, "name": "DP-2", "x": 1920, "y": 0, "width": 1440, "height": 900}]
        with patch("ghost_wave.hypr_json", side_effect=[active, monitors, {"x": 2200, "y": 450}]):
            target = capture_target()
        self.assertEqual((target.monitor, target.x, target.y), ("DP-2", 280, 414))


if __name__ == "__main__":
    unittest.main()
