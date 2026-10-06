import os
import tempfile
import time
import unittest
from unittest import mock

from _paths import load_script

pam = load_script("faceauth_pam", "faceauth_pam.py")

USER = "alice"


class CheckTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token = os.path.join(self.tmp.name, "token")
        patches = [
            mock.patch.object(pam, "get_user_uid", return_value=os.getuid()),
            mock.patch.object(pam, "get_token_file", return_value=self.token),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def write(self, content, mode=0o600):
        with open(self.token, "w") as f:
            f.write(content)
        os.chmod(self.token, mode)

    def test_fresh_token_is_accepted_once(self):
        self.write(f"{USER}:{time.time()}")
        self.assertTrue(pam.check_token(USER))
        self.assertFalse(os.path.exists(self.token))
        self.assertFalse(pam.check_token(USER))

    def test_missing_token(self):
        self.assertFalse(pam.check_token(USER))

    def test_empty_username(self):
        self.write(f":{time.time()}")
        self.assertFalse(pam.check_token(""))

    def test_stale_token(self):
        self.write(f"{USER}:{time.time() - pam.TOKEN_VALIDITY - 1}")
        self.assertFalse(pam.check_token(USER))

    def test_future_token_is_rejected(self):
        # Previously accepted: a negative age is always < TOKEN_VALIDITY.
        self.write(f"{USER}:{time.time() + 3600}")
        self.assertFalse(pam.check_token(USER))
        self.assertTrue(os.path.exists(self.token))

    def test_non_finite_timestamps(self):
        for value in ("inf", "-inf", "nan"):
            self.write(f"{USER}:{value}")
            self.assertFalse(pam.check_token(USER), value)

    def test_other_user(self):
        self.write(f"bob:{time.time()}")
        self.assertFalse(pam.check_token(USER))

    def test_group_or_world_readable(self):
        for mode in (0o640, 0o604, 0o666):
            self.write(f"{USER}:{time.time()}", mode)
            self.assertFalse(pam.check_token(USER), oct(mode))

    def test_wrong_owner(self):
        self.write(f"{USER}:{time.time()}")
        with mock.patch.object(pam, "get_user_uid", return_value=os.getuid() + 1):
            self.assertFalse(pam.check_token(USER))

    def test_malformed(self):
        for content in ("", "garbage", f"{USER}", f"{USER}:abc", f"{USER}:1:2"):
            self.write(content)
            self.assertFalse(pam.check_token(USER), content)

    def test_unknown_user(self):
        with mock.patch.object(pam, "get_user_uid", side_effect=KeyError(USER)):
            self.assertFalse(pam.check_token(USER))


if __name__ == "__main__":
    unittest.main()
