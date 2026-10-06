#!/bin/bash
#
# Integration test for the opt-in KDE Plasma 6 lock screen overlay, run on a
# real Plasma 6 session. It covers what the unit tests can't:
#
#   1. Baseline       overlay off, lock screen file matches its package
#   2. Enable         `faceauthctl enable-kde-ui`, greeter loads the patched QML
#   3. Password       lock, unlock with the password (FaceAuth stopped)
#   4. Face           lock, unlock with the face (no password typed)
#   5. Update         reinstall the package that owns the file; the repair
#                     unit reapplies the overlay; face unlock still works
#   6. Remove         `faceauthctl disable-kde-ui` (the path uninstall.sh
#                     uses), file matches its package again, password unlock
#
# Steps 3-6 lock your screen and ask you to unlock it. The greeter load
# checks briefly show the lock screen in a test window (it does not lock).
#
# Usage: bash tests/integration/kde_lockscreen.sh [report-dir]
#
# Requires FaceAuth installed from this checkout with an enrolled face, and
# sudo. Writes report.md, greeter logs and screenshots to the report dir.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPORT_DIR="${1:-$PWD/kde-lockscreen-test-$(date +%Y%m%d-%H%M%S)}"
LOCKSCREEN_DIR="/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen"
LOCKSCREEN_QML="$LOCKSCREEN_DIR/LockScreenUi.qml"
STATUS_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/faceauth/status.json"
UNLOCK_TIMEOUT=120

mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/report.md"
PASSED=0
FAILED=0

# ---------------------------------------------------------------- helpers

say() { echo "$*" | tee -a "$REPORT"; }

pass() {
    PASSED=$((PASSED + 1))
    say "- PASS: $1"
}

fail() {
    FAILED=$((FAILED + 1))
    say "- **FAIL**: $1"
}

check() {
    local description="$1"
    shift
    if "$@" >/dev/null 2>&1; then pass "$description"; else fail "$description"; fi
}

section() {
    say ""
    say "## $1"
    say ""
}

pause() {
    echo ""
    read -r -p ">>> $1 Press Enter when ready... " _
}

die() {
    echo "ERROR: $1" >&2
    exit 2
}

patch_state() {
    /usr/local/bin/faceauth-kde-patch status 2>/dev/null | cut -d' ' -f1
}

find_greeter() {
    local candidate
    for candidate in /usr/libexec/kscreenlocker_greet /usr/lib/kscreenlocker_greet \
                     /usr/lib/*/libexec/kscreenlocker_greet /usr/lib/*/kscreenlocker_greet; do
        [ -x "$candidate" ] && { echo "$candidate"; return; }
    done
}

# Owning package, and a verify command that fails when the file's size or
# checksum differs from the packaged version (mtime is ignored on purpose:
# removing the patch writes a new file).
detect_package() {
    if command -v rpm >/dev/null && rpm -qf "$LOCKSCREEN_QML" >/dev/null 2>&1; then
        PKG_MANAGER=rpm
        PKG_NAME=$(rpm -qf --qf '%{NAME}' "$LOCKSCREEN_QML")
    elif command -v dpkg >/dev/null && dpkg -S "$LOCKSCREEN_QML" >/dev/null 2>&1; then
        PKG_MANAGER=dpkg
        PKG_NAME=$(dpkg -S "$LOCKSCREEN_QML" | cut -d: -f1)
    elif command -v pacman >/dev/null && pacman -Qoq "$LOCKSCREEN_QML" >/dev/null 2>&1; then
        PKG_MANAGER=pacman
        PKG_NAME=$(pacman -Qoq "$LOCKSCREEN_QML")
    else
        PKG_MANAGER=""
        PKG_NAME=""
    fi
}

file_matches_package() {
    case "$PKG_MANAGER" in
        rpm)
            ! rpm -Vf "$LOCKSCREEN_QML" 2>/dev/null | grep -F "$LOCKSCREEN_QML" | grep -qE '^(S|..5)'
            ;;
        dpkg)
            ! dpkg --verify "$PKG_NAME" 2>/dev/null | grep -F "$LOCKSCREEN_QML" | grep -q '5'
            ;;
        pacman)
            ! pacman -Qkk "$PKG_NAME" 2>/dev/null | grep -F "$LOCKSCREEN_QML" | grep -qiE 'checksum|size'
            ;;
        *)
            return 1
            ;;
    esac
}

reinstall_package() {
    case "$PKG_MANAGER" in
        rpm) sudo dnf reinstall -y "$PKG_NAME" ;;
        dpkg) sudo apt-get install --reinstall -y "$PKG_NAME" ;;
        pacman) sudo pacman -S --noconfirm "$PKG_NAME" ;;
    esac
}

graphical_session() {
    loginctl show-user "$(id -un)" -p Display --value 2>/dev/null
}

locked_hint() {
    loginctl show-session "$SESSION_ID" -p LockedHint --value 2>/dev/null
}

# Lock the session and wait until the user unlocks it. Prints the unlock time.
lock_and_wait_for_unlock() {
    loginctl lock-session "$SESSION_ID" || return 1

    local waited=0
    while [ "$(locked_hint)" != "yes" ] && [ $waited -lt 10 ]; do
        sleep 0.5
        waited=$((waited + 1))
    done
    [ "$(locked_hint)" = "yes" ] || return 1

    waited=0
    while [ "$(locked_hint)" = "yes" ]; do
        sleep 1
        waited=$((waited + 1))
        [ $waited -ge $UNLOCK_TIMEOUT ] && return 1
    done
    date +%s
}

greeter_errors_since() {
    # _COMM is truncated to 15 characters, so match the executable path.
    journalctl -q --no-pager --since "@$1" _EXE="$GREETER" 2>/dev/null \
        | grep -iE 'Failed to load|outdated|falling back|FaceAuthOsd|ReferenceError|TypeError|is not a type'
}

# Keep the overlay visible during a greeter test load: it hides when idle.
fake_scanning_status() {
    mkdir -p "$(dirname "$STATUS_FILE")"
    while :; do
        printf '{"state": "scanning", "ts": %s, "elapsed": 3, "max": 30}' "$(date +%s)" > "$STATUS_FILE.tmp"
        mv "$STATUS_FILE.tmp" "$STATUS_FILE"
        sleep 1
    done
}

# Load the real lock screen QML in kscreenlocker's testing mode (it shows the
# lock screen in a window and does not lock the session). Saves the output and
# a screenshot. Line numbers are stripped so the patched and stock runs can be
# compared (the patch shifts lines in LockScreenUi.qml).
greeter_load() {
    local name="$1"
    local log="$REPORT_DIR/greeter-$name.log"
    local faker=""

    if [ "$2" = "scanning" ]; then
        fake_scanning_status &
        faker=$!
    fi

    "$GREETER" --testing --shell org.kde.plasma.desktop >"$log" 2>&1 &
    local greeter_pid=$!
    sleep 5
    if command -v spectacle >/dev/null; then
        spectacle --background --nonotify --fullscreen --output "$REPORT_DIR/greeter-$name.png" >/dev/null 2>&1
    fi
    sleep 1
    kill "$greeter_pid" 2>/dev/null
    wait "$greeter_pid" 2>/dev/null

    if [ -n "$faker" ]; then
        kill "$faker" 2>/dev/null
        wait "$faker" 2>/dev/null
        printf '{"state": "idle", "ts": %s}' "$(date +%s)" > "$STATUS_FILE"
    fi

    sed -E 's/:[0-9]+(:[0-9]+)?//g' "$log" | grep -iE 'qml|error|warn|fail' | sort -u > "$REPORT_DIR/greeter-$name.issues"
}

greeter_loads_cleanly() {
    ! grep -qiE 'Failed to load lockscreen QML|Lockscreen QML outdated|fallback' "$REPORT_DIR/greeter-$1.log"
}

# Issues in the patched run that the stock lock screen doesn't have.
greeter_new_issues() {
    comm -13 "$REPORT_DIR/greeter-baseline.issues" "$REPORT_DIR/greeter-$1.issues"
}

no_new_greeter_issues() {
    [ -z "$(greeter_new_issues "$1")" ]
}

record_new_issues() {
    local issues
    issues=$(greeter_new_issues "$1")
    if [ -n "$issues" ]; then
        say ""
        say "New greeter messages compared to the stock lock screen:"
        say '```'
        say "$issues"
        say '```'
    fi
}

face_recognized_since() {
    journalctl -q --no-pager -u faceauth --since "@$1" 2>/dev/null | grep -q "Face recognized"
}

# ---------------------------------------------------------------- preflight

PLASMA_VERSION=$(plasmashell --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)
GREETER=$(find_greeter)
SESSION_ID=$(graphical_session)
detect_package

[ "${PLASMA_VERSION%%.*}" = "6" ] || die "KDE Plasma 6 is required (found: ${PLASMA_VERSION:-none})."
[ -n "$GREETER" ] || die "kscreenlocker_greet not found."
[ -n "$SESSION_ID" ] || die "No graphical session found for $(id -un)."
[ -f "$LOCKSCREEN_QML" ] || die "$LOCKSCREEN_QML not found."
[ -n "$PKG_MANAGER" ] || die "Could not find the package that owns $LOCKSCREEN_QML."
[ -x /usr/local/bin/faceauthctl ] && [ -x /usr/local/bin/faceauth-kde-patch ] || die "Install FaceAuth from this checkout first: bash install.sh"
systemctl is-active --quiet faceauth || die "The faceauth service is not running."

for pair in "faceauthctl.py:/usr/local/bin/faceauthctl" \
            "faceauth_daemon.py:/usr/local/bin/faceauth_daemon" \
            "kde-overlay/faceauth_kde_patch.py:/usr/local/bin/faceauth-kde-patch" \
            "kde-overlay/FaceAuthOsd.qml:/usr/local/share/faceauth/kde-overlay/FaceAuthOsd.qml"; do
    cmp -s "$REPO_DIR/${pair%%:*}" "${pair#*:}" || die "${pair#*:} differs from this checkout - run: bash install.sh"
done

cat > "$REPORT" <<EOF
# FaceAuth KDE lock screen integration test

- Date: $(date -Iseconds)
- FaceAuth commit: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null)$(git -C "$REPO_DIR" diff --quiet 2>/dev/null || echo " (with local changes)")
- OS: $(. /etc/os-release && echo "$PRETTY_NAME")
- Plasma: $PLASMA_VERSION, kscreenlocker greeter: $GREETER
- Session: $(loginctl show-session "$SESSION_ID" -p Type --value 2>/dev/null), lock screen file owned by: $PKG_NAME ($PKG_MANAGER)
EOF

echo ""
echo "This test locks your screen 4 times and briefly shows the lock screen in a"
echo "test window. Report: $REPORT"
sudo -v || die "sudo is required."

# ---------------------------------------------------------------- 1. baseline

section "1. Baseline (overlay off)"
/usr/local/bin/faceauthctl disable-kde-ui >"$REPORT_DIR/disable-baseline.log" 2>&1
check "overlay is not applied" test "$(patch_state)" = "not-patched"
check "auto-repair units are not installed" test ! -e /etc/systemd/system/faceauth-kde-repair.path
check "lock screen file matches the $PKG_NAME package" file_matches_package
greeter_load baseline idle
check "stock lock screen loads in the greeter" greeter_loads_cleanly baseline

# ---------------------------------------------------------------- 2. enable

section "2. Opt in: faceauthctl enable-kde-ui"
/usr/local/bin/faceauthctl enable-kde-ui >"$REPORT_DIR/enable.log" 2>&1
check "enable-kde-ui succeeds" grep -q "overlay applied" "$REPORT_DIR/enable.log"
check "overlay is applied" test "$(patch_state)" = "patched"
check "auto-repair path unit is active" systemctl is-active --quiet faceauth-kde-repair.path
greeter_load patched scanning
check "patched lock screen loads in the greeter (no fallback)" greeter_loads_cleanly patched
check "no new QML errors or warnings compared to the stock lock screen" no_new_greeter_issues patched
record_new_issues patched

# ---------------------------------------------------------------- 3. password

section "3. Lock and unlock with the password"
sudo systemctl stop faceauth
pause "Your screen will lock. FaceAuth is stopped, so unlock with your PASSWORD."
started=$(date +%s)
if unlocked=$(lock_and_wait_for_unlock); then
    pass "locked, then unlocked with the password after $((unlocked - started))s"
else
    fail "lock or password unlock did not complete within ${UNLOCK_TIMEOUT}s"
fi
check "greeter reported no QML load errors" test -z "$(greeter_errors_since "$started")"
sudo systemctl start faceauth
sleep 8

# ---------------------------------------------------------------- 4. face

section "4. Lock and unlock with the face"
pause "Your screen will lock. Look at the camera and DO NOT type anything."
started=$(date +%s)
if unlocked=$(lock_and_wait_for_unlock); then
    pass "locked, then unlocked after $((unlocked - started))s"
else
    fail "lock or face unlock did not complete within ${UNLOCK_TIMEOUT}s"
fi
check "daemon logged a face recognition during this lock" face_recognized_since "$started"
check "greeter reported no QML load errors" test -z "$(greeter_errors_since "$started")"

# ---------------------------------------------------------------- 5. update

section "5. Package update: reinstall $PKG_NAME"
reinstall_started=$(date +%s)
if reinstall_package >"$REPORT_DIR/reinstall.log" 2>&1; then
    pass "$PKG_NAME reinstalled"
else
    fail "$PKG_NAME reinstall failed (see reinstall.log)"
fi

for _ in $(seq 1 30); do
    [ "$(patch_state)" = "patched" ] && break
    sleep 1
done
check "auto-repair reapplied the overlay after the update" test "$(patch_state)" = "patched"
check "faceauth-kde-repair.service ran after the update" \
    bash -c "journalctl -q --no-pager -u faceauth-kde-repair.service --since @$reinstall_started | grep -q 'overlay applied'"
greeter_load updated scanning
check "lock screen loads in the greeter after the update" greeter_loads_cleanly updated
check "no new QML errors or warnings after the update" no_new_greeter_issues updated
record_new_issues updated

pause "Your screen will lock again. Look at the camera and DO NOT type anything."
started=$(date +%s)
if unlocked=$(lock_and_wait_for_unlock); then
    pass "face unlock after the update: unlocked after $((unlocked - started))s"
else
    fail "face unlock after the update did not complete within ${UNLOCK_TIMEOUT}s"
fi
check "daemon logged a face recognition after the update" face_recognized_since "$started"

# ---------------------------------------------------------------- 6. remove

section "6. Opt out / uninstall path: faceauthctl disable-kde-ui"
/usr/local/bin/faceauthctl disable-kde-ui >"$REPORT_DIR/disable.log" 2>&1
check "overlay is removed" test "$(patch_state)" = "not-patched"
check "lock screen file matches the $PKG_NAME package again" file_matches_package
check "FaceAuthOsd.qml is removed from the lock screen directory" test ! -e "$LOCKSCREEN_DIR/FaceAuthOsd.qml"
check "auto-repair units are removed" test ! -e /etc/systemd/system/faceauth-kde-repair.path
greeter_load removed idle
check "stock lock screen loads in the greeter after removal" greeter_loads_cleanly removed

sudo systemctl stop faceauth
pause "Your screen will lock. FaceAuth is stopped, so unlock with your PASSWORD."
started=$(date +%s)
if unlocked=$(lock_and_wait_for_unlock); then
    pass "password unlock after removal: unlocked after $((unlocked - started))s"
else
    fail "password unlock after removal did not complete within ${UNLOCK_TIMEOUT}s"
fi
sudo systemctl start faceauth

# ---------------------------------------------------------------- summary

section "Result"
say "$PASSED passed, $FAILED failed."
say ""
say "Screenshots of each greeter load are in this directory (greeter-*.png)."
echo ""
echo "Report written to $REPORT"
echo "The overlay is now OFF. Re-enable it with: faceauthctl enable-kde-ui"

[ "$FAILED" -eq 0 ]
