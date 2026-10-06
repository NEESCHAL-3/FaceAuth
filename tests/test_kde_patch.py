import glob
import os
import tempfile
import unittest
from unittest import mock

from _paths import load_script

patch = load_script("faceauth_kde_patch", "kde-overlay/faceauth_kde_patch.py")

REAL_LOCKSCREEN = "/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml"


def sample_qml():
    """A minimal lock screen QML that contains the anchor exactly once."""
    return "Item {\n    Item {\n" + patch.ANCHOR + "\n    }\n}\n"


class KdePatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = os.path.join(self.tmp.name, "LockScreenUi.qml")
        self.overlay_src = os.path.join(self.tmp.name, "src-FaceAuthOsd.qml")
        with open(self.overlay_src, "w") as f:
            f.write("// overlay v1\n")

        for p in (
            mock.patch.object(patch, "CANDIDATE_PATHS", [self.target]),
            mock.patch.object(patch, "OVERLAY_SRC", self.overlay_src),
        ):
            p.start()
            self.addCleanup(p.stop)

    def write_target(self, content):
        with open(self.target, "w") as f:
            f.write(content)

    def read_target(self):
        with open(self.target) as f:
            return f.read()

    def overlay_dst(self):
        return os.path.join(self.tmp.name, patch.OVERLAY_FILENAME)

    def roundtrip(self, original):
        self.write_target(original)
        self.assertEqual(patch.status()[0], "not-patched")

        self.assertEqual(patch.apply(), 0)
        self.assertEqual(patch.status()[0], "patched")
        self.assertEqual(self.read_target().count(patch.MARKER_BEGIN), 1)
        self.assertTrue(os.path.isfile(self.overlay_dst()))

        self.assertEqual(patch.remove(), 0)
        self.assertEqual(self.read_target(), original)
        self.assertFalse(os.path.exists(self.overlay_dst()))

    def test_apply_remove_restores_exact_content(self):
        self.roundtrip(sample_qml())

    @unittest.skipUnless(glob.glob(REAL_LOCKSCREEN + ".faceauth-backup-*"),
                         "no real unpatched Plasma lock screen backup on this machine")
    def test_roundtrip_on_real_plasma_file(self):
        latest = sorted(glob.glob(REAL_LOCKSCREEN + ".faceauth-backup-*"))[-1]
        with open(latest) as f:
            self.roundtrip(f.read())

    def test_apply_is_idempotent_and_refreshes_overlay(self):
        self.write_target(sample_qml())
        self.assertEqual(patch.apply(), 0)
        once = self.read_target()

        with open(self.overlay_src, "w") as f:
            f.write("// overlay v2\n")
        self.assertEqual(patch.apply(), 0)

        self.assertEqual(self.read_target(), once)
        with open(self.overlay_dst()) as f:
            self.assertEqual(f.read(), "// overlay v2\n")

    def test_refuses_unrecognized_file(self):
        self.write_target("Item { }\n")
        self.assertEqual(patch.apply(), 1)
        self.assertEqual(self.read_target(), "Item { }\n")

    def test_backups_are_pruned(self):
        for i in range(patch.BACKUPS_TO_KEEP + 2):
            open(f"{self.target}.faceauth-backup-2026010{i}-000000", "w").close()
        self.write_target(sample_qml())
        self.assertEqual(patch.apply(), 0)
        backups = glob.glob(self.target + ".faceauth-backup-*")
        self.assertEqual(len(backups), patch.BACKUPS_TO_KEEP)

    def test_remove_does_not_restore_old_backup(self):
        # A backup from an older Plasma version must never replace the current file.
        with open(self.target + ".faceauth-backup-20200101-000000", "w") as f:
            f.write("OLD PLASMA\n" + patch.ANCHOR)
        current = sample_qml().replace("Item {", "Item { // newer plasma", 1)
        self.write_target(current)
        patch.apply()
        patch.remove()
        self.assertEqual(self.read_target(), current)


if __name__ == "__main__":
    unittest.main()
