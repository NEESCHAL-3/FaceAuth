#!/usr/bin/env python3
"""
Shared helpers for the FaceAuth daemon and faceauthctl.

Installed to /usr/local/lib/faceauth/. Heavy imports (cv2, face_recognition)
are done lazily inside functions so lightweight commands like
`faceauthctl status` stay fast and tests can run without a camera.
"""
import json
import os
import pwd
import warnings
from contextlib import contextmanager
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)

VERSION = "0.3.0"

DEFAULT_CONFIG = {
    "ir_camera": 0,
    "tolerance": 0.6,
    "max_scan_seconds": 30,
    "scan_retry_cooldown_seconds": 20,
    "desktop": "",
    "display_manager": "",
}

# Frames darker than this are skipped. IR cameras strobe their emitter, so
# every other frame is almost black (mean ~3) and contains no usable face.
DARK_FRAME_THRESHOLD = 15

# A frame is treated as IR when its color channels are (nearly) identical.
# Real IR sensors report exactly 0; washed-out RGB webcams are still ~4-6.
IR_CHANNEL_DIFF_MAX = 0.5

# When the best distance is this close to the tolerance, recognition becomes
# unreliable (frames flip between match and no match) - suggest re-enrolling.
DRIFT_MARGIN = 0.08

DEFAULT_ENROLL_SAMPLES = 5


def user_home(username):
    return Path(pwd.getpwnam(username).pw_dir)


def faceauth_dir(username):
    return user_home(username) / ".faceauth"


def config_path(username):
    return faceauth_dir(username) / "config.json"


def face_image_path(username):
    return faceauth_dir(username) / "my_face.jpg"


def encodings_path(username):
    return faceauth_dir(username) / "encodings.json"


def load_config(username):
    """
    Return (config, error). Missing keys fall back to DEFAULT_CONFIG; a
    missing or unreadable file returns the defaults plus an error string so
    callers can report it instead of silently guessing.
    """
    config = dict(DEFAULT_CONFIG)
    path = config_path(username)

    if not path.exists():
        return config, f"{path} not found"

    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return config, f"{path} could not be read: {e}"

    if not isinstance(data, dict):
        return config, f"{path} does not contain a JSON object"

    config.update(data)
    return config, None


def save_config(username, config):
    path = config_path(username)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2))
    tmp.chmod(0o600)
    os.replace(tmp, path)


def save_encodings(username, encodings):
    path = encodings_path(username)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([[float(x) for x in e] for e in encodings]))
    tmp.chmod(0o600)
    os.replace(tmp, path)


def load_encodings(username):
    """
    Return the enrolled face encodings as a list of numpy arrays.

    Prefers encodings.json (multi-sample enrollment). Falls back to
    computing one encoding from my_face.jpg so installs enrolled before
    multi-sample support keep working.
    """
    import numpy as np

    path = encodings_path(username)
    if path.exists():
        data = json.loads(path.read_text())
        encodings = [np.array(e, dtype=np.float64) for e in data]
        if encodings:
            return encodings

    image_path = face_image_path(username)
    if not image_path.exists():
        return []

    import face_recognition

    image = face_recognition.load_image_file(str(image_path))
    return face_recognition.face_encodings(image)[:1]


@contextmanager
def suppress_native_stderr():
    """
    Suppress noisy native OpenCV/V4L stderr messages during camera open/read/release.
    FaceAuth still prints its own useful status logs.
    """
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_stderr_fd = os.dup(2)

    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(old_stderr_fd, 2)
        os.close(old_stderr_fd)
        os.close(devnull_fd)


def is_usable_frame(frame):
    return frame is not None and frame.size > 0 and float(frame.mean()) >= DARK_FRAME_THRESHOLD


def channel_diff(frame):
    """Mean absolute difference between color channels (0 for grayscale)."""
    import numpy as np

    if len(frame.shape) == 2 or frame.shape[2] == 1:
        return 0.0

    b, g, r = (frame[:, :, i].astype(np.int16) for i in range(3))
    return float(max(np.abs(b - g).mean(), np.abs(g - r).mean()))


def classify_frame(frame):
    """Return ("ir" or "rgb", channel_diff, brightness)."""
    diff = channel_diff(frame)
    kind = "ir" if diff <= IR_CHANNEL_DIFF_MAX else "rgb"
    return kind, diff, float(frame.mean())


def find_face_encodings(frame):
    """
    Detect faces in a BGR (or grayscale) frame and return their encodings.

    Detection runs without upsampling: that is ~5x faster than the
    face_recognition default and still finds a face at normal laptop
    distance, which lets each scan check many more frames.
    """
    import cv2
    import face_recognition

    if len(frame.shape) == 2:
        rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    locations = face_recognition.face_locations(rgb, number_of_times_to_upsample=0)
    if not locations:
        return []
    return face_recognition.face_encodings(rgb, locations)


def best_distance(known_encodings, encoding):
    """Smallest distance between `encoding` and any enrolled encoding."""
    import numpy as np

    if not known_encodings:
        return float("inf")
    known = np.asarray(known_encodings)
    return float(np.linalg.norm(known - encoding, axis=1).min())


def open_camera(index):
    import cv2

    with suppress_native_stderr():
        cap = cv2.VideoCapture(index)

    if not cap.isOpened():
        with suppress_native_stderr():
            cap.release()
        return None
    return cap


def read_frame(cap):
    with suppress_native_stderr():
        ret, frame = cap.read()
    return frame if ret else None


def release_camera(cap):
    with suppress_native_stderr():
        cap.release()


def probe_cameras(max_index=10):
    """
    Return a list of dicts describing every camera that yields a usable frame:
    {"index", "kind", "diff", "brightness"}.

    Indexes are OpenCV indexes, which do not always line up with the
    /dev/videoN names, so the kernel device name is not reported.
    """
    cameras = []

    for i in range(max_index):
        cap = open_camera(i)
        if cap is None:
            continue

        frame = None
        for _ in range(12):
            f = read_frame(cap)
            if is_usable_frame(f):
                frame = f
                break

        release_camera(cap)

        if frame is None:
            cameras.append({"index": i, "kind": None, "diff": None, "brightness": None})
            continue

        kind, diff, brightness = classify_frame(frame)
        cameras.append({"index": i, "kind": kind, "diff": diff, "brightness": brightness})

    return cameras


def camera_sees_face(index, seconds=3):
    """True if a face is detected on camera `index` within `seconds`."""
    import time

    cap = open_camera(index)
    if cap is None:
        return False

    try:
        for _ in range(10):
            read_frame(cap)

        started = time.time()
        while time.time() - started < seconds:
            frame = read_frame(cap)
            if is_usable_frame(frame) and find_face_encodings(frame):
                return True
        return False
    finally:
        release_camera(cap)


def pick_camera(cameras, sees_face=None):
    """
    Prefer a real IR camera; otherwise the first working RGB camera.

    Some laptops expose several IR-looking streams and only one of them is
    useful, so when `sees_face` is given (a callable taking an index) the
    first camera of each kind that actually sees a face wins.
    """
    usable = [c for c in cameras if c["kind"]]
    ir = [c for c in usable if c["kind"] == "ir"]
    rgb = [c for c in usable if c["kind"] == "rgb"]

    if sees_face is not None:
        for c in ir + rgb:
            if sees_face(c["index"]):
                return c

    if ir:
        return ir[0]
    return rgb[0] if rgb else None
