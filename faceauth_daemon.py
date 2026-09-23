#!/usr/bin/env python3
import os
import sys

# Shared helpers live next to this script in the repo, and in
# /usr/local/lib/faceauth once installed.
sys.path.insert(0, "/usr/local/lib/faceauth")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import faceauth_common as common

import json
import pwd
import time
import signal
import subprocess
import threading
import select

# Consecutive matching frames needed to unlock. Each non-matching frame
# subtracts one, so a single lucky frame never unlocks the screen.
REQUIRED_MATCHES = 3

def get_faceauth_dir():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

    if runtime_dir:
        faceauth_dir = os.path.join(runtime_dir, "faceauth")
    else:
        faceauth_dir = f"/run/user/{os.getuid()}/faceauth"

    os.makedirs(faceauth_dir, mode=0o700, exist_ok=True)
    return faceauth_dir

def get_token_file():
    return os.path.join(get_faceauth_dir(), "token")

def get_status_file():
    return os.path.join(get_faceauth_dir(), "status.json")

def write_status(state, **extra):
    """
    Publish daemon state for lock-screen UI integrations (GNOME extension,
    KDE lock screen overlay) to read. Written atomically so readers never
    see a half-written file.
    """
    status_file = get_status_file()
    payload = {"state": state, "ts": time.time()}
    payload.update(extra)

    tmp_file = f"{status_file}.tmp"
    fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    os.replace(tmp_file, status_file)

def load_settings(username):
    config, error = common.load_config(username)
    if error:
        print(f"Config warning: {error} - using defaults")

    try:
        encodings = common.load_encodings(username)
    except (OSError, ValueError) as e:
        print(f"Could not load enrolled face: {e}")
        encodings = []

    if not encodings:
        print("No enrolled face found - run: faceauthctl enroll")
        sys.exit(1)

    camera = int(config["ir_camera"])
    enrolled_camera = config.get("enrolled_camera")
    if enrolled_camera is not None and int(enrolled_camera) != camera:
        print(f"WARNING: face was enrolled on camera {enrolled_camera} but scanning uses camera {camera}. "
              f"Re-enroll with: faceauthctl enroll {camera}")

    return {
        "camera": camera,
        "tolerance": float(config["tolerance"]),
        "max_scan_seconds": int(config["max_scan_seconds"]),
        "scan_retry_cooldown_seconds": int(config["scan_retry_cooldown_seconds"]),
        "desktop": str(config.get("desktop", "")).lower(),
        "encodings": encodings,
    }

def write_token(username):
    token_file = get_token_file()

    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    with os.fdopen(fd, "w") as f:
        f.write(f"{username}:{time.time()}")

    os.chmod(token_file, 0o600)

def _try_dbus_unlock(dest, obj_path, method):
    try:
        p = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", dest,
             "--object-path", obj_path,
             "--method", method,
             "false"],
            capture_output=True, text=True, timeout=5
        )
        return p.returncode == 0
    except Exception:
        return False

def unlock_screen(desktop=""):
    """
    Send an unlock signal. Tries the screensaver D-Bus interfaces that match the
    active desktop first, then falls back to the XDG standard, then to logind.

    Works with GNOME (gnome-shell), KDE Plasma (ksmserver/kscreenlocker), and
    any desktop that implements org.freedesktop.ScreenSaver.
    """
    desktop = (desktop or os.environ.get("XDG_CURRENT_DESKTOP", "")).lower()
    is_kde = "kde" in desktop or "plasma" in desktop

    if is_kde:
        methods = [
            ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive"),
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive"),
            ("org.kde.screensaver", "/ScreenSaver", "org.kde.screensaver.SetActive"),
            ("org.kde.screensaver", "/org/freedesktop/ScreenSaver", "org.kde.screensaver.SetActive"),
            ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.SetActive"),
        ]
    else:
        methods = [
            ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.SetActive"),
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive"),
            ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.SetActive"),
            ("org.kde.screensaver", "/ScreenSaver", "org.kde.screensaver.SetActive"),
        ]

    for dest, obj_path, method in methods:
        if _try_dbus_unlock(dest, obj_path, method):
            print(f"Unlock signal sent via {dest}!")
            return

    try:
        subprocess.run(["loginctl", "unlock-sessions"],
                       capture_output=True, text=True, timeout=5)
        print("Unlock signal sent via loginctl!")
    except Exception as e:
        print(f"Unlock error: {e}")

def face_recognition_loop(username, settings, stop_event):
    camera = settings["camera"]
    tolerance = settings["tolerance"]
    max_scan_seconds = settings["max_scan_seconds"]
    known_encodings = settings["encodings"]

    print(f"Camera activated - looking for face on index {camera}")
    scan_started_at = time.time()

    video = common.open_camera(camera)

    if video is None:
        print(f"Camera open failed for index {camera}")
        write_status("error", reason="camera_open_failed")
        return

    for _ in range(10):
        if stop_event.is_set():
            common.release_camera(video)
            print("Camera deactivated")
            return

        common.read_frame(video)

    write_status("scanning", elapsed=0, max=max_scan_seconds)
    last_status_write = time.time()
    match_count = 0
    best_seen = float("inf")
    frames_with_face = 0

    while not stop_event.is_set():
        elapsed = time.time() - scan_started_at

        if elapsed >= max_scan_seconds:
            print(f"Face scan timed out after {max_scan_seconds}s - stopping camera")
            write_status("timeout")
            stop_event.set()
            break

        frame = common.read_frame(video)

        if stop_event.is_set():
            break

        # Skip failed reads and the dark frames IR cameras produce while
        # their emitter is off.
        if not common.is_usable_frame(frame):
            time.sleep(0.02)
            continue

        if time.time() - last_status_write >= 1:
            write_status("scanning", elapsed=int(elapsed), max=max_scan_seconds)
            last_status_write = time.time()

        encodings = common.find_face_encodings(frame)
        if encodings:
            frames_with_face += 1
            distance = min(common.best_distance(known_encodings, e) for e in encodings)
            best_seen = min(best_seen, distance)

            if distance <= tolerance:
                match_count += 1
                if match_count >= REQUIRED_MATCHES:
                    print(f"Face recognized (distance {distance:.3f}, tolerance {tolerance})! Unlocking...")
                    write_status("success")
                    write_token(username)
                    unlock_screen(settings["desktop"])
                    stop_event.set()
                    break
            else:
                match_count = max(0, match_count - 1)

        time.sleep(0.05)

    common.release_camera(video)

    if frames_with_face and match_count < REQUIRED_MATCHES:
        print(f"Best match distance this scan: {best_seen:.3f} (tolerance {tolerance}, "
              f"{frames_with_face} frames with a face)")
        if best_seen > tolerance - common.DRIFT_MARGIN:
            print("Match is close to or above the tolerance - consider re-enrolling: faceauthctl enroll")
    elif not frames_with_face and match_count < REQUIRED_MATCHES:
        print("No face detected during this scan")

    print("Camera deactivated")

def run_daemon(username):
    print(f"FaceAuth daemon starting for {username}")
    settings = load_settings(username)
    desktop = settings["desktop"]
    scan_retry_cooldown_seconds = settings["scan_retry_cooldown_seconds"]
    print(f"Loaded {len(settings['encodings'])} enrolled face sample(s), camera {settings['camera']}, "
          f"tolerance {settings['tolerance']}")
    write_status("idle")

    is_kde = "kde" in desktop or "plasma" in desktop
    if is_kde:
        print("KDE Plasma detected - lock events will trigger scan immediately")

    stop_event = None
    face_thread = None
    lock_time = None
    is_locked_state = False
    session_awake = False
    last_scan_end = 0

    proc = subprocess.Popen(
        ["gdbus", "monitor", "--system",
         "--dest", "org.freedesktop.login1"],
        stdout=subprocess.PIPE,
        text=True
    )

    print("Watching for real lockscreen events...")

    def cleanup_finished_scan():
        nonlocal stop_event, face_thread, last_scan_end

        if stop_event and face_thread and not face_thread.is_alive():
            stop_event = None
            face_thread = None
            last_scan_end = time.time()
            print("Camera scan session ended")

    def start_scan(reason):
        nonlocal stop_event, face_thread

        cleanup_finished_scan()

        if stop_event is not None:
            return

        print(reason)
        stop_event = threading.Event()
        face_thread = threading.Thread(
            target=face_recognition_loop,
            args=(username, settings, stop_event)
        )
        face_thread.daemon = True
        face_thread.start()

    def stop_scan():
        nonlocal stop_event, face_thread, last_scan_end

        if stop_event:
            stop_event.set()

            if face_thread and face_thread.is_alive():
                face_thread.join(timeout=2)

            stop_event = None
            face_thread = None
            last_scan_end = time.time()

    def handle_exit(sig, frame):
        stop_scan()
        write_status("idle")
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    while True:
        cleanup_finished_scan()

        # If a scan timed out but the lockscreen is still awake, retry after cooldown.
        if (
            is_locked_state
            and session_awake
            and stop_event is None
            and face_thread is None
            and last_scan_end
            and (time.time() - last_scan_end) >= scan_retry_cooldown_seconds
        ):
            start_scan(f"Locked session still awake - retrying face scan after {scan_retry_cooldown_seconds}s cooldown")

        ready, _, _ = select.select([proc.stdout], [], [], 0.5)

        if not ready:
            continue

        line = proc.stdout.readline()

        if not line:
            break

        line = line.strip()

        if "'IdleHint': <true>" in line:
            session_awake = False

        if "'LockedHint': <true>" in line:
            is_locked_state = True
            lock_time = time.time()
            last_scan_end = 0

            if is_kde:
                # KDE's screen locker (kscreenlocker) is immediately interactive
                # when LockedHint is set, and does not toggle IdleHint the way
                # GNOME does. Start scanning right away instead of waiting for a
                # wake event that never arrives.
                session_awake = True
                print("Lock signal received - starting camera (KDE)...")
                if stop_event is None:
                    start_scan("KDE lockscreen - starting camera!")
                else:
                    print("Scan already running - monitoring...")
            else:
                session_awake = False
                print("Lock signal received - monitoring...")

        elif "'IdleHint': <false>" in line and is_locked_state:
            session_awake = True
            elapsed = time.time() - lock_time if lock_time else 0

            if stop_event is not None:
                continue

            print(f"Wake signal after {elapsed:.1f}s")

            if elapsed < 1:
                print("Quick wake - just screen dim, ignoring")
                is_locked_state = False
                session_awake = False
                lock_time = None
                last_scan_end = 0
            else:
                start_scan("Real lockscreen - starting camera!")

        elif "'LockedHint': <false>" in line or "Session.Unlock" in line:
            if not is_locked_state and stop_event is None:
                continue

            print("Screen UNLOCKED - stopping camera")
            is_locked_state = False
            session_awake = False
            lock_time = None
            last_scan_end = 0
            stop_scan()
            write_status("idle")

if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else pwd.getpwuid(os.getuid()).pw_name
    run_daemon(username)
