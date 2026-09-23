import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import _paths  # noqa: F401  (adds the repo root to sys.path)
import faceauth_common as common


class ClassifyFrameTest(unittest.TestCase):
    def test_grayscale_3_channel_is_ir(self):
        gray = np.random.randint(0, 255, (48, 64), dtype=np.uint8)
        frame = np.stack([gray, gray, gray], axis=2)
        kind, diff, _ = common.classify_frame(frame)
        self.assertEqual(kind, "ir")
        self.assertEqual(diff, 0.0)

    def test_single_channel_is_ir(self):
        frame = np.full((48, 64), 80, dtype=np.uint8)
        self.assertEqual(common.classify_frame(frame)[0], "ir")

    def test_washed_out_color_is_rgb(self):
        # Blue and red nearly equal but green differs - the old b-r only check
        # called this IR.
        base = np.full((48, 64), 120, dtype=np.int16)
        frame = np.stack([base, base + 6, base + 1], axis=2).astype(np.uint8)
        self.assertEqual(common.classify_frame(frame)[0], "rgb")

    def test_dark_frames_are_not_usable(self):
        self.assertFalse(common.is_usable_frame(None))
        self.assertFalse(common.is_usable_frame(np.full((10, 10, 3), 3, dtype=np.uint8)))
        self.assertTrue(common.is_usable_frame(np.full((10, 10, 3), 50, dtype=np.uint8)))


class PickCameraTest(unittest.TestCase):
    CAMS = [
        {"index": 0, "kind": "rgb"},
        {"index": 1, "kind": "ir"},
        {"index": 2, "kind": "ir"},
        {"index": 3, "kind": None},
    ]

    def test_prefers_ir(self):
        self.assertEqual(common.pick_camera(self.CAMS)["index"], 1)

    def test_prefers_ir_that_sees_face(self):
        picked = common.pick_camera(self.CAMS, sees_face=lambda i: i == 2)
        self.assertEqual(picked["index"], 2)

    def test_falls_back_to_rgb_that_sees_face(self):
        picked = common.pick_camera(self.CAMS, sees_face=lambda i: i == 0)
        self.assertEqual(picked["index"], 0)

    def test_nobody_in_view_still_prefers_ir(self):
        picked = common.pick_camera(self.CAMS, sees_face=lambda i: False)
        self.assertEqual(picked["index"], 1)

    def test_rgb_only(self):
        self.assertEqual(common.pick_camera([{"index": 4, "kind": "rgb"}])["index"], 4)

    def test_none(self):
        self.assertIsNone(common.pick_camera([{"index": 0, "kind": None}]))


class BestDistanceTest(unittest.TestCase):
    def test_min_over_samples(self):
        known = [np.zeros(128), np.full(128, 0.1)]
        probe = np.full(128, 0.1)
        self.assertAlmostEqual(common.best_distance(known, probe), 0.0)

    def test_no_samples(self):
        self.assertEqual(common.best_distance([], np.zeros(128)), float("inf"))


class ConfigAndEncodingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(common, "user_home", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def test_missing_config_returns_defaults_and_error(self):
        config, error = common.load_config("alice")
        self.assertEqual(config, common.DEFAULT_CONFIG)
        self.assertIn("not found", error)

    def test_invalid_json(self):
        path = common.config_path("alice")
        path.parent.mkdir()
        path.write_text("{not json")
        config, error = common.load_config("alice")
        self.assertEqual(config["tolerance"], common.DEFAULT_CONFIG["tolerance"])
        self.assertIn("could not be read", error)

    def test_non_object_json(self):
        path = common.config_path("alice")
        path.parent.mkdir()
        path.write_text("[1, 2]")
        self.assertIn("JSON object", common.load_config("alice")[1])

    def test_round_trip_keeps_unknown_keys(self):
        common.save_config("alice", {"ir_camera": 2, "tolerance": 0.5, "custom": True})
        config, error = common.load_config("alice")
        self.assertIsNone(error)
        self.assertEqual(config["ir_camera"], 2)
        self.assertEqual(config["tolerance"], 0.5)
        self.assertTrue(config["custom"])
        self.assertEqual(config["max_scan_seconds"], common.DEFAULT_CONFIG["max_scan_seconds"])
        self.assertEqual(common.config_path("alice").stat().st_mode & 0o777, 0o600)

    def test_encodings_round_trip(self):
        samples = [np.random.rand(128) for _ in range(3)]
        common.save_encodings("alice", samples)
        loaded = common.load_encodings("alice")
        self.assertEqual(len(loaded), 3)
        np.testing.assert_allclose(loaded[1], samples[1])
        self.assertEqual(len(json.loads(common.encodings_path("alice").read_text())), 3)
        self.assertEqual(common.encodings_path("alice").stat().st_mode & 0o777, 0o600)

    def test_no_enrollment(self):
        self.assertEqual(common.load_encodings("alice"), [])


if __name__ == "__main__":
    unittest.main()
