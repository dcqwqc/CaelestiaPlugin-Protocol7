"""Screenshot-free geometry fixtures. No user screenshot or visible text saved."""
import subprocess
import unittest
from unittest.mock import patch

import numpy as np

import composer_vision


def synthetic_composer(dark=False):
    # 908x388 logical pixels on a 150%-scaled monitor.
    scene = np.full((582, 1362), 249, dtype=np.uint8)
    scene[:, 780:782] = 237  # stable sidebar / chat pane divider
    scene[:, 782:] = 253
    scene[487:489, 825:1330] = 228  # composer top edge
    scene[552:554, 825:1330] = 228  # composer bottom edge
    scene[497:543, 805:807] = 228  # left edge, visible inside rounded corners
    scene[576:579, 782:] = 232  # app outer bottom border
    return 255 - scene if dark else scene


class VisionGeometryTests(unittest.TestCase):
    def test_split_and_input_box_at_fractional_scale(self):
        bounds = composer_vision.detect_rect(
            synthetic_composer(), window_x=71, crop_y=790,
            logical_width=908, logical_height=388,
        )
        self.assertIsNotNone(bounds)
        x,y,w,h = bounds
        self.assertLessEqual(abs(x-608), 5)
        self.assertLessEqual(abs(y-1115), 3)
        self.assertTrue(340 <= w <= 380)
        self.assertLessEqual(abs(h-43), 3)

    def test_dark_ui_edges_detected_without_color_assumptions(self):
        bounds = composer_vision.detect_rect(
            synthetic_composer(True), window_x=71, crop_y=790,
            logical_width=908, logical_height=388,
        )
        self.assertIsNotNone(bounds)

    def test_no_sidebar_divider_is_not_misreported_as_exact(self):
        blank = np.full((582,1362), 248, dtype=np.uint8)
        self.assertIsNone(composer_vision.detect_rect(
            blank, window_x=71, crop_y=790, logical_width=908, logical_height=388,
        ))

    def test_screencopy_timeout_is_bounded_and_cached(self):
        app = {"at": [71,21], "size": [908,1158]}
        with patch.object(composer_vision, "_last_capture_failure", 0.0), \
             patch("composer_vision.subprocess.run", side_effect=subprocess.TimeoutExpired("grim", .42)) as capture:
            self.assertIsNone(composer_vision.capture_composer(app))
            self.assertIsNone(composer_vision.capture_composer(app))
            capture.assert_called_once()
            self.assertEqual(capture.call_args.kwargs["timeout"], .42)

if __name__ == "__main__":
    unittest.main()
