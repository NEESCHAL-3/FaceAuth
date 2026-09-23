#!/usr/bin/env python3
"""
Applies or removes the FaceAuth lock-screen overlay on KDE Plasma.

Plasma 6 does not support a swappable lock-screen theme package - the lock
screen QML (LockScreenUi.qml) ships inside the plasma-desktop system
package. To show a FaceAuth status overlay there, this script inserts one
small, self-contained `Loader` block into that file, in the same place and
style KDE's own on-screen-display (LockOsd.qml) is already loaded.

Safety rules, deliberately conservative because this touches a live
system file used to unlock the session:
  - Never patches unless the exact known-good anchor text is found exactly
    once. Any mismatch (different Plasma build, distro patch, prior manual
    edit) aborts with no changes made.
  - Always backs up the original file before writing, timestamped.
  - The inserted block is wrapped in unique markers so it can be detected,
    verified, and cleanly removed.
  - The overlay itself (FaceAuthOsd.qml) is a separate file the patched
    Loader points to - if it fails to load for any reason, the Loader just
    renders nothing. It never touches the authentication/password code
    that lives in the rest of LockScreenUi.qml.
"""
import os
import shutil
import sys
import time

CANDIDATE_PATHS = [
    "/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/LockScreenUi.qml",
]

OVERLAY_FILENAME = "FaceAuthOsd.qml"
OVERLAY_SRC = "/usr/local/share/faceauth/kde-overlay/FaceAuthOsd.qml"

# Every apply after a Plasma update writes a timestamped backup; keep a few.
BACKUPS_TO_KEEP = 3

MARKER_BEGIN = "// FACEAUTH-OVERLAY-BEGIN (safe to delete this block - see faceauthctl repair-kde-ui)"
MARKER_END = "// FACEAUTH-OVERLAY-END"

ANCHOR = """        Loader {
            z: 2
            active: root.viewVisible
            source: "LockOsd.qml"
            anchors {
                horizontalCenter: parent.horizontalCenter
                bottom: parent.bottom
                bottomMargin: Kirigami.Units.gridUnit
            }
        }"""

PATCH_BLOCK = f"""
{MARKER_BEGIN}
        Loader {{
            z: 3
            active: true
            source: "{OVERLAY_FILENAME}"
            anchors {{
                horizontalCenter: parent.horizontalCenter
                top: parent.top
                topMargin: Kirigami.Units.gridUnit * 2
            }}
        }}
{MARKER_END}"""


def read_file(path):
    with open(path) as f:
        return f.read()


def find_target():
    for path in CANDIDATE_PATHS:
        if os.path.isfile(path):
            return path
    return None


def status():
    path = find_target()
    if not path:
        return "not-applicable", None

    content = read_file(path)

    if MARKER_BEGIN in content:
        return "patched", path
    if content.count(ANCHOR) == 1:
        return "not-patched", path
    return "unrecognized", path


def install_overlay(path):
    overlay_dst = os.path.join(os.path.dirname(path), OVERLAY_FILENAME)
    shutil.copy2(OVERLAY_SRC, overlay_dst)
    os.chmod(overlay_dst, 0o644)


def prune_backups(path):
    directory = os.path.dirname(path)
    prefix = os.path.basename(path) + ".faceauth-backup-"
    backups = sorted(p for p in os.listdir(directory) if p.startswith(prefix))
    for old in backups[:-BACKUPS_TO_KEEP]:
        os.remove(os.path.join(directory, old))


def apply():
    state, path = status()

    if state == "not-applicable":
        print("faceauth-kde-patch: no known KDE lockscreen file found, skipping.")
        return 1

    if state == "patched":
        # Refresh the overlay file so a reinstall picks up QML changes.
        if os.path.isfile(OVERLAY_SRC):
            install_overlay(path)
        print("faceauth-kde-patch: overlay already applied.")
        return 0

    if state == "unrecognized":
        print(f"faceauth-kde-patch: {path} does not match the verified layout - refusing to patch.")
        return 1

    if not os.path.isfile(OVERLAY_SRC):
        print(f"faceauth-kde-patch: {OVERLAY_SRC} not found, cannot install overlay.")
        return 1

    content = read_file(path)

    backup = f"{path}.faceauth-backup-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)

    patched = content.replace(ANCHOR, ANCHOR + "\n" + PATCH_BLOCK, 1)

    if patched.count(MARKER_BEGIN) != 1 or patched.count(MARKER_END) != 1:
        print("faceauth-kde-patch: patch verification failed, aborting without writing.")
        os.remove(backup)
        return 1

    install_overlay(path)

    tmp_path = f"{path}.faceauth-tmp"
    with open(tmp_path, "w") as f:
        f.write(patched)
    os.replace(tmp_path, path)
    os.chmod(path, 0o644)
    prune_backups(path)

    print(f"faceauth-kde-patch: overlay applied to {path}")
    print(f"faceauth-kde-patch: original backed up to {backup}")
    return 0


def remove():
    state, path = status()

    if state == "not-applicable":
        print("faceauth-kde-patch: no known KDE lockscreen file found, nothing to remove.")
        return 0

    if state == "not-patched":
        print("faceauth-kde-patch: overlay is not currently applied.")
        return 0

    if state == "patched":
        # Strip only our marked block instead of restoring a backup: a backup
        # may predate a Plasma update, and restoring it would downgrade the
        # rest of the lock screen QML.
        content = read_file(path)
        start = content.find(MARKER_BEGIN)
        end = content.find(MARKER_END)

        if start == -1 or end == -1 or end < start:
            print("faceauth-kde-patch: could not locate patch markers, leaving file untouched.")
            return 1

        end += len(MARKER_END)
        while start > 0 and content[start - 1] == "\n":
            start -= 1

        cleaned = content[:start] + content[end:]

        if cleaned.count(ANCHOR) != 1:
            print("faceauth-kde-patch: file does not look right after removing the block, leaving it untouched.")
            return 1

        tmp_path = f"{path}.faceauth-tmp"
        with open(tmp_path, "w") as f:
            f.write(cleaned)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
        print(f"faceauth-kde-patch: patch block removed from {path}")

        overlay_dst = os.path.join(os.path.dirname(path), OVERLAY_FILENAME)
        if os.path.isfile(overlay_dst):
            os.remove(overlay_dst)

        print("faceauth-kde-patch: overlay removed.")
        return 0

    print(f"faceauth-kde-patch: {path} does not match the verified layout - leaving it untouched.")
    return 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        state, path = status()
        print(f"{state} ({path or 'n/a'})")
        return 0

    if os.geteuid() != 0:
        print("faceauth-kde-patch: apply/remove must be run as root.")
        return 1

    if cmd == "apply":
        return apply()
    if cmd == "remove":
        return remove()

    print("Usage: faceauth-kde-patch [apply|remove|status]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
