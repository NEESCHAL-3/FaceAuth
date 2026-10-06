#!/usr/bin/env python3
import os
import platform
import pwd
import subprocess
import sys
import time
from pathlib import Path

# Shared helpers live next to this script in the repo, and in
# /usr/local/lib/faceauth once installed.
sys.path.insert(0, "/usr/local/lib/faceauth")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import faceauth_common as common

FACEAUTH_LINE = "auth sufficient pam_exec.so quiet /usr/local/bin/faceauth"
GNOME_EXT_UUID = "faceauth-lockscreen@faceauth.local"
KDE_REPAIR_UNIT = "faceauth-kde-repair.path"
KDE_REPAIR_UNITS = ("faceauth-kde-repair.path", "faceauth-kde-repair.service")
KDE_PATCH_BIN = "/usr/local/bin/faceauth-kde-patch"
KDE_UNIT_SRC_DIR = Path("/usr/local/share/faceauth/systemd")

def run(cmd):
    try:
        p = subprocess.run(cmd, text=True, capture_output=True)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def current_user():
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name

def user_home(username):
    return Path(pwd.getpwnam(username).pw_dir)

def header(title):
    print("")
    print("=" * 48)
    print(title)
    print("=" * 48)

def service_value(args):
    code, out, err = run(["systemctl"] + args + ["faceauth"])
    return out if out else err if err else "unknown"

def cmd_status():
    username = current_user()
    cfg = common.config_path(username)
    face = common.face_image_path(username)
    encodings_file = common.encodings_path(username)

    header("FaceAuth Status")
    print(f"Version: {common.VERSION}")
    print(f"User: {username}")
    print(f"Service active: {service_value(['is-active'])}")
    print(f"Service enabled: {service_value(['is-enabled'])}")
    print(f"Config: {'OK' if cfg.exists() else 'missing'} ({cfg})")
    print(f"Registered face: {'OK' if face.exists() else 'missing'} ({face})")

    if encodings_file.exists():
        try:
            print(f"Enrolled samples: {len(common.load_encodings(username))}")
        except (OSError, ValueError) as e:
            print(f"Enrolled samples: unreadable ({e})")
    else:
        print("Enrolled samples: 1 (legacy single photo - run `faceauthctl enroll` for multi-sample)")

    data, error = common.load_config(username)
    if error:
        print(f"Config read error: {error}")
        return

    print(f"Camera index: {data.get('ir_camera')}")
    print(f"Enrolled on camera: {data.get('enrolled_camera', 'unknown')}")
    print(f"Tolerance: {data.get('tolerance')}")
    print(f"Max scan seconds: {data.get('max_scan_seconds')}")
    print(f"Scan retry cooldown seconds: {data.get('scan_retry_cooldown_seconds')}")
    print(f"Desktop: {data.get('desktop')}")
    print(f"Display manager: {data.get('display_manager', 'unknown')}")

def cmd_logs():
    since = "20 minutes ago"
    args = sys.argv[2:]
    if args:
        since = " ".join(args)

    subprocess.run([
        "journalctl",
        "-u", "faceauth",
        "--since", since,
        "-l",
        "--no-pager"
    ])

def detect_package_manager():
    for pm in ["dnf", "apt", "pacman", "zypper"]:
        code, out, err = run(["bash", "-lc", f"command -v {pm}"])
        if code == 0:
            return pm
    return "unknown"

def read_os_release():
    path = Path("/etc/os-release")
    if not path.exists():
        return "unknown"

    data = {}
    for line in path.read_text(errors="ignore").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k] = v.strip('"')

    return data.get("PRETTY_NAME", "unknown")

def import_check(module):
    code, out, err = run(["python3", "-c", f"import {module}; print('OK')"])
    return "OK" if code == 0 else f"FAILED: {err or out}"

def pam_check():
    files = [
        "/etc/pam.d/gdm-password",
        "/etc/pam.d/sddm",
        "/etc/pam.d/kde",
        "/etc/pam.d/kscreenlocker",
        "/etc/pam.d/lightdm",
    ]

    for f in files:
        path = Path(f)
        if not path.exists():
            continue

        try:
            text = path.read_text(errors="ignore")
            print(f"{f}: {'FaceAuth configured' if FACEAUTH_LINE in text else 'no FaceAuth line'}")
        except Exception as e:
            print(f"{f}: cannot read ({e})")

def format_camera(cam):
    if cam["kind"] is None:
        return f"Camera {cam['index']}: opens but no usable frame"
    return (f"Camera {cam['index']}: OK | type={cam['kind']} | color-diff={cam['diff']:.2f} | "
            f"brightness={cam['brightness']:.2f}")

def cmd_list_cameras():
    header("FaceAuth Camera List")

    try:
        cameras = common.probe_cameras()
    except ImportError as e:
        print(f"OpenCV import failed: {e}")
        return 1

    if not cameras:
        print("No usable cameras found.")
        return 1

    for cam in cameras:
        print(format_camera(cam))

    picked = common.pick_camera(cameras)
    if picked:
        print("")
        print(f"Preferred camera: {picked['index']} ({picked['kind']})")
        if picked["kind"] == "rgb":
            print("No IR camera found. A regular webcam can be fooled by a photo of your face.")
    return 0

def cmd_detect_camera():
    """
    Machine-readable camera pick for install.sh: prints `<index>:<kind>`.
    The user is expected to be in front of the camera, so cameras that
    actually see a face are preferred.
    """
    cameras = common.probe_cameras()
    picked = common.pick_camera(cameras, sees_face=common.camera_sees_face)
    if not picked:
        print("NO_CAMERA")
        return 2
    print(f"{picked['index']}:{picked['kind']}")
    return 0

def detect_display_manager():
    """Return the active display manager service name (sddm/gdm/lightdm/...) or ''."""
    for candidate in ("sddm", "gdm", "gdm3", "lightdm", "lxdm", "plasmalogin"):
        code, _, _ = run(["systemctl", "is-active", "--quiet", candidate])
        if code == 0:
            return candidate
    code, out, err = run(["systemctl", "status", "display-manager", "--no-pager"])
    first = out.splitlines()[0] if out else err.splitlines()[0] if err else ""
    return first or "unknown"

def lock_screen_ui_status(desktop):
    """Report GNOME extension / KDE overlay state for `doctor`. Read-only."""
    if "kde" in desktop or "plasma" in desktop:
        code, out, err = run(["/usr/local/bin/faceauth-kde-patch", "status"])
        if code != 0 and not out:
            return f"KDE overlay: could not check ({err or 'faceauth-kde-patch missing'})"
        return f"KDE overlay: {out or err}"

    username = current_user()
    ext_dir = user_home(username) / ".local" / "share" / "gnome-shell" / "extensions" / GNOME_EXT_UUID
    if not ext_dir.exists():
        return "GNOME extension: not installed"

    code, out, _ = run(["gnome-extensions", "info", GNOME_EXT_UUID])
    if code != 0:
        return "GNOME extension: installed, state unknown (run `gnome-extensions info` in a graphical session)"

    enabled = "Enabled" in out or "State: ACTIVE" in out
    return f"GNOME extension: installed, {'enabled' if enabled else 'disabled - run: gnome-extensions enable ' + GNOME_EXT_UUID}"

def cmd_doctor():
    username = current_user()

    header("FaceAuth Doctor Report")
    print(f"Version: {common.VERSION}")
    print(f"User: {username}")
    print(f"UID: {pwd.getpwnam(username).pw_uid}")
    print(f"OS: {read_os_release()}")
    print(f"Kernel: {platform.release()}")
    print(f"Package manager: {detect_package_manager()}")
    print(f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')}")
    print(f"Session type: {os.environ.get('XDG_SESSION_TYPE', 'unknown')}")
    print(f"Display manager: {detect_display_manager()}")

    print("")
    print("Service:")
    print(f"  active: {service_value(['is-active'])}")
    print(f"  enabled: {service_value(['is-enabled'])}")

    print("")
    print("Python imports:")
    checks = [
        ("setuptools compatibility", "pkg_resources"),
        ("OpenCV", "cv2"),
        ("dlib", "dlib"),
        ("face recognition models", "face_recognition_models"),
        ("face recognition", "face_recognition"),
    ]

    for label, module in checks:
        print(f"  {label}: {import_check(module)}")

    print("")
    print("PAM:")
    pam_check()

    print("")
    print("Installed files:")
    for f in ["/usr/local/bin/faceauth", "/usr/local/bin/faceauth_daemon", "/usr/local/bin/faceauthctl",
              "/usr/local/lib/faceauth/faceauth_common.py", "/etc/systemd/system/faceauth.service"]:
        print(f"  {f}: {'OK' if Path(f).exists() else 'missing'}")

    desktop = os.environ.get('XDG_CURRENT_DESKTOP', 'unknown').lower()
    print("")
    print("Lock screen UI:")
    print(f"  {lock_screen_ui_status(desktop)}")
    if "kde" in desktop or "plasma" in desktop:
        code, out, _ = run(["systemctl", "is-enabled", KDE_REPAIR_UNIT])
        print(f"  Auto-repair after Plasma updates: {out if code == 0 else 'off (opt in with: faceauthctl enable-kde-ui)'}")

    print("")
    cmd_list_cameras()

    cfg, _ = common.load_config(username)
    if common.encodings_path(username).exists() or common.face_image_path(username).exists():
        print("")
        match_check(username, int(cfg["ir_camera"]), float(cfg["tolerance"]))

def require_kde_patch_bin():
    if not Path(KDE_PATCH_BIN).exists():
        print(f"FAILED: {KDE_PATCH_BIN} not found. Was FaceAuth installed on a KDE Plasma 6 system?")
        return False
    return True

def run_patch(action):
    code, out, err = run(["sudo", KDE_PATCH_BIN, action])
    print(out or err)
    return code == 0

def cmd_repair_kde_ui():
    header("FaceAuth KDE Lock Screen Overlay Repair")

    if not require_kde_patch_bin():
        return 1

    if not run_patch("apply"):
        print("Repair failed - see messages above.")
        return 1

    print("Done.")
    return 0

def cmd_enable_kde_ui():
    """
    Opt in to the KDE lock screen overlay: patch the package-owned lock
    screen QML and install the unit that reapplies it after Plasma updates.
    """
    header("FaceAuth KDE Lock Screen Overlay")
    print("This patches the system file LockScreenUi.qml (owned by plasma-desktop) to add")
    print("the scan indicator. Plasma 6.1+ only loads the lock screen from that shell package,")
    print("so there is no theme-based alternative. Undo any time with: faceauthctl disable-kde-ui")
    print("")

    if not require_kde_patch_bin():
        return 1

    if not run_patch("apply"):
        print("Could not apply the overlay - nothing else was changed.")
        return 1

    for unit in KDE_REPAIR_UNITS:
        src = KDE_UNIT_SRC_DIR / unit
        if not src.exists():
            print(f"WARNING: {src} not found - automatic repair after Plasma updates is not enabled.")
            print("After Plasma updates, run: faceauthctl repair-kde-ui")
            return 0
        code, out, err = run(["sudo", "install", "-m", "644", str(src), f"/etc/systemd/system/{unit}"])
        if code != 0:
            print(f"WARNING: could not install {unit}: {err or out}")
            return 0

    run(["sudo", "systemctl", "daemon-reload"])
    code_path, _, err_path = run(["sudo", "systemctl", "enable", "--now", KDE_REPAIR_UNITS[0]])
    code_svc, _, err_svc = run(["sudo", "systemctl", "enable", KDE_REPAIR_UNITS[1]])

    if code_path == 0 and code_svc == 0:
        print("Automatic overlay repair after Plasma updates enabled.")
    else:
        print(f"WARNING: could not enable automatic repair: {err_path or err_svc}")
        print("After Plasma updates, run: faceauthctl repair-kde-ui")
    return 0

def cmd_disable_kde_ui():
    """Remove the KDE overlay and its auto-repair units, restoring the stock lock screen."""
    header("FaceAuth KDE Lock Screen Overlay Removal")

    # Stop the watcher first so it can't re-patch the file we clean up.
    for unit in KDE_REPAIR_UNITS:
        run(["sudo", "systemctl", "disable", "--now", unit])
        run(["sudo", "rm", "-f", f"/etc/systemd/system/{unit}"])
    run(["sudo", "systemctl", "daemon-reload"])
    print("Automatic overlay repair disabled.")

    if not Path(KDE_PATCH_BIN).exists():
        return 0

    if not run_patch("remove"):
        print("Could not remove the overlay - see messages above.")
        return 1
    return 0

def load_config(username):
    data, error = common.load_config(username)
    if error and common.config_path(username).exists():
        print(f"Config read warning: {error}")
    return data

def save_config(username, data):
    common.save_config(username, data)

def restart_service():
    print("Restarting FaceAuth service...")
    code, out, err = run(["sudo", "systemctl", "restart", "faceauth"])

    if code == 0:
        print("FaceAuth service restarted.")
        return 0

    print(f"Could not restart service: {err or out}")
    print("Run manually: sudo systemctl restart faceauth")
    return 1

def capture_frame(camera_index, warmup=10, attempts=30):
    cap = common.open_camera(camera_index)

    if cap is None:
        return None, f"Camera {camera_index} could not be opened."

    for _ in range(warmup):
        common.read_frame(cap)

    frame = None

    for _ in range(attempts):
        f = common.read_frame(cap)

        if common.is_usable_frame(f):
            frame = f
            break

        time.sleep(0.05)

    common.release_camera(cap)

    if frame is None:
        return None, f"Camera {camera_index} opened but did not return a usable frame."

    return frame, None

def cmd_test_camera():
    username = current_user()
    cfg = load_config(username)

    if len(sys.argv) >= 3:
        try:
            camera_index = int(sys.argv[2])
        except ValueError:
            print("Camera index must be a number.")
            return 1
    else:
        camera_index = int(cfg.get("ir_camera", 0))

    header("FaceAuth Camera Test")
    print(f"Testing camera index: {camera_index}")

    frame, err = capture_frame(camera_index)

    if err:
        print(f"FAILED: {err}")
        return 1

    kind, diff, brightness = common.classify_frame(frame)
    print(f"OK: Camera {camera_index}")
    print(f"Type: {kind}")
    print(f"Color diff: {diff:.2f}")
    print(f"Brightness: {brightness:.2f}")
    return 0

def cmd_set_camera():
    if len(sys.argv) < 3:
        print("Usage: faceauthctl set-camera <index>")
        return 1

    try:
        camera_index = int(sys.argv[2])
    except ValueError:
        print("Camera index must be a number.")
        return 1

    username = current_user()

    header("FaceAuth Set Camera")
    print(f"Checking camera {camera_index} before saving...")

    frame, err = capture_frame(camera_index)

    if err:
        print(f"FAILED: {err}")
        return 1

    kind, diff, brightness = common.classify_frame(frame)

    cfg = load_config(username)
    cfg["ir_camera"] = camera_index
    save_config(username, cfg)

    print(f"Saved camera index: {camera_index}")
    print(f"Type: {kind}")
    print(f"Color diff: {diff:.2f}")
    print(f"Brightness: {brightness:.2f}")

    enrolled_camera = cfg.get("enrolled_camera")
    if enrolled_camera is not None and int(enrolled_camera) != camera_index:
        print("")
        print(f"WARNING: your face was enrolled on camera {enrolled_camera}.")
        print(f"Faces from different cameras (especially IR vs RGB) do not match well. Re-enroll with:")
        print(f"  faceauthctl enroll {camera_index}")

    restart_service()
    return 0

def parse_enroll_args(args, cfg):
    camera_index = int(cfg.get("ir_camera", 0))
    samples = common.DEFAULT_ENROLL_SAMPLES
    restart = True

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--samples" and i + 1 < len(args):
            samples = int(args[i + 1])
            i += 2
            continue
        if arg == "--no-restart":
            restart = False
        else:
            camera_index = int(arg)
        i += 1

    if samples < 1:
        raise ValueError("--samples must be at least 1")

    return camera_index, samples, restart

def collect_samples(camera_index, samples, timeout=30, spacing=0.7):
    """
    Capture `samples` face encodings from the camera, at least `spacing`
    seconds apart so the user's small head movements give some variety.
    Frames with zero or several faces are skipped.
    """
    cap = common.open_camera(camera_index)
    if cap is None:
        return None, [], f"Camera {camera_index} could not be opened."

    for _ in range(10):
        common.read_frame(cap)

    encodings = []
    first_frame = None
    last_sample = 0
    started = time.time()

    try:
        while len(encodings) < samples and time.time() - started < timeout:
            frame = common.read_frame(cap)
            if not common.is_usable_frame(frame) or time.time() - last_sample < spacing:
                time.sleep(0.02)
                continue

            found = common.find_face_encodings(frame)
            if len(found) != 1:
                continue

            encodings.append(found[0])
            if first_frame is None:
                first_frame = frame
            last_sample = time.time()
            print(f"  sample {len(encodings)}/{samples} captured")
    finally:
        common.release_camera(cap)

    if not encodings:
        return None, [], "No single face detected. Try better lighting and keep only your face visible."

    return first_frame, encodings, None

def drop_outliers(encodings, tolerance):
    """Remove samples that don't look like the others (e.g. someone walked by)."""
    if len(encodings) < 3:
        return encodings, 0

    kept = []
    for i, e in enumerate(encodings):
        others = [o for j, o in enumerate(encodings) if j != i]
        if common.best_distance(others, e) <= tolerance:
            kept.append(e)
    return kept, len(encodings) - len(kept)

def cmd_enroll():
    import cv2

    username = current_user()
    cfg = load_config(username)

    try:
        camera_index, samples, restart = parse_enroll_args(sys.argv[2:], cfg)
    except ValueError as e:
        print(f"Invalid arguments: {e}")
        print("Usage: faceauthctl enroll [index] [--samples N] [--no-restart]")
        return 1

    tolerance = float(cfg["tolerance"])
    face_path = common.face_image_path(username)

    header("FaceAuth Enrollment")
    print(f"Using camera index: {camera_index}")
    print(f"Capturing {samples} samples. Look at the camera and move your head slightly")
    print("between samples. Use the lighting you usually have when the screen locks.")
    print("Starting in 3 seconds...")
    time.sleep(3)

    frame, encodings, err = collect_samples(camera_index, samples)

    if err:
        print(f"FAILED: {err}")
        return 1

    encodings, dropped = drop_outliers(encodings, tolerance)
    if dropped:
        print(f"Dropped {dropped} sample(s) that did not match the others.")
    if not encodings:
        print("FAILED: samples were inconsistent. Make sure only you are in front of the camera.")
        return 1
    if len(encodings) < samples:
        print(f"WARNING: only {len(encodings)} of {samples} samples captured. Recognition may be less reliable.")

    face_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not cv2.imwrite(str(face_path), frame):
        print(f"FAILED: Could not save face image to {face_path}")
        return 1
    face_path.chmod(0o600)

    common.save_encodings(username, encodings)

    kind, _, _ = common.classify_frame(frame)
    cfg["ir_camera"] = camera_index
    cfg["enrolled_camera"] = camera_index
    save_config(username, cfg)

    print(f"Face enrolled successfully: {len(encodings)} samples from {kind} camera {camera_index}")

    if kind == "rgb":
        print("")
        print("NOTE: this is a regular (RGB) camera, which a photo of your face can fool.")
        print("If `faceauthctl list-cameras` shows an IR camera, enrolling on it is safer.")

    if restart:
        restart_service()
    return 0

def match_check(username, camera_index, tolerance, duration=6):
    """Measure how well the live camera matches the enrolled face."""
    header("FaceAuth Match Check")

    try:
        known = common.load_encodings(username)
    except (OSError, ValueError) as e:
        print(f"FAILED: could not load enrolled face: {e}")
        return 1

    if not known:
        print("FAILED: no enrolled face. Run: faceauthctl enroll")
        return 1

    cap = common.open_camera(camera_index)
    if cap is None:
        print(f"FAILED: camera {camera_index} could not be opened (is the screen locked or the camera busy?)")
        return 1

    print(f"Look at camera {camera_index} for {duration} seconds...")
    for _ in range(10):
        common.read_frame(cap)

    distances = []
    started = time.time()
    try:
        while time.time() - started < duration:
            frame = common.read_frame(cap)
            if not common.is_usable_frame(frame):
                continue
            found = common.find_face_encodings(frame)
            if found:
                distances.append(min(common.best_distance(known, e) for e in found))
    finally:
        common.release_camera(cap)

    if not distances:
        print("No face detected. Make sure you are in front of the camera.")
        return 1

    distances.sort()
    best = distances[0]
    median = distances[len(distances) // 2]
    print(f"Enrolled samples: {len(known)}")
    print(f"Frames with a face: {len(distances)}")
    print(f"Best distance: {best:.3f}  Median: {median:.3f}  Tolerance: {tolerance}")

    if median <= tolerance - common.DRIFT_MARGIN:
        print("Result: GOOD - face unlock should be reliable.")
        return 0
    if median <= tolerance:
        print("Result: BORDERLINE - unlock may be slow or fail. Re-enroll: faceauthctl enroll")
        return 0
    print("Result: NO MATCH - unlock will fail. Re-enroll: faceauthctl enroll")
    return 1

def cmd_check_match():
    username = current_user()
    cfg = load_config(username)

    camera_index = int(cfg.get("ir_camera", 0))
    if len(sys.argv) >= 3:
        try:
            camera_index = int(sys.argv[2])
        except ValueError:
            print("Camera index must be a number.")
            return 1

    return match_check(username, camera_index, float(cfg["tolerance"]))

def usage():
    print("FaceAuth control tool")
    print("")
    print("Usage:")
    print("  faceauthctl status")
    print("  faceauthctl logs [since]")
    print("  faceauthctl doctor")
    print("  faceauthctl list-cameras")
    print("  faceauthctl test-camera [index]")
    print("  faceauthctl set-camera <index>")
    print("  faceauthctl enroll [index] [--samples N] [--no-restart]")
    print("  faceauthctl check-match [index]")
    print("  faceauthctl enable-kde-ui")
    print("  faceauthctl disable-kde-ui")
    print("  faceauthctl repair-kde-ui")
    print("  faceauthctl version")
    print("")
    print("Examples:")
    print("  faceauthctl logs '10 minutes ago'")
    print("  faceauthctl test-camera 1")
    print("  faceauthctl set-camera 2")
    print("  faceauthctl enroll 2")

def main():
    if len(sys.argv) < 2:
        usage()
        return 0

    cmd = sys.argv[1]

    if cmd == "status":
        cmd_status()
    elif cmd == "logs":
        cmd_logs()
    elif cmd == "doctor":
        cmd_doctor()
    elif cmd in ("list-cameras", "cameras"):
        return cmd_list_cameras()
    elif cmd == "test-camera":
        return cmd_test_camera()
    elif cmd == "set-camera":
        return cmd_set_camera()
    elif cmd == "enroll":
        return cmd_enroll()
    elif cmd == "check-match":
        return cmd_check_match()
    elif cmd == "detect-camera":
        return cmd_detect_camera()
    elif cmd in ("version", "--version"):
        print(common.VERSION)
    elif cmd == "repair-kde-ui":
        return cmd_repair_kde_ui()
    elif cmd == "enable-kde-ui":
        return cmd_enable_kde_ui()
    elif cmd == "disable-kde-ui":
        return cmd_disable_kde_ui()
    elif cmd in ("help", "-h", "--help"):
        usage()
    else:
        print(f"Unknown command: {cmd}")
        usage()
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())