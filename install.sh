#!/bin/bash

echo "========================================"
echo "   FaceAuth Universal Installer"
echo "========================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USERNAME=$(whoami)
USER_ID=$(id -u)
FORCE_ENROLL=0
KDE_LOCKSCREEN_OVERLAY=0

for arg in "$@"; do
    case "$arg" in
        --force-enroll|--re-enroll)
            FORCE_ENROLL=1
            ;;
        --kde-lockscreen-overlay)
            KDE_LOCKSCREEN_OVERLAY=1
            ;;
        -h|--help)
            echo "Usage: bash install.sh [--force-enroll] [--kde-lockscreen-overlay]"
            echo ""
            echo "  --force-enroll           re-detect the camera and enroll your face again"
            echo "  --kde-lockscreen-overlay opt in to patching the system KDE Plasma 6 lock screen"
            echo "                           to show the animated scan indicator (see README)"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (see: bash install.sh --help)"
            exit 1
            ;;
    esac
done

if [ "$USER_ID" -eq 0 ]; then
    echo "ERROR: run install.sh as your normal user, not with sudo."
    echo "It asks for sudo itself when needed, and enrolls the face of the user running it."
    exit 1
fi

echo "Installing for user: $USERNAME (UID: $USER_ID)"

# Helper functions
log() {
    echo ""
    echo "==> $1"
}

warn() {
    echo "WARN: $1"
}

die() {
    echo ""
    echo "ERROR: $1"
    echo "FaceAuth install stopped safely before making unsafe changes."
    exit 1
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

TOTAL_STEPS=12
CURRENT_STEP=0

step() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    echo ""
    echo "[$CURRENT_STEP/$TOTAL_STEPS] $1"
}

RUN_LOG_DIR="/tmp/faceauth-install-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_LOG_DIR"

note() {
    echo "NOTE: $1"
}

run_capture() {
    local label="$1"
    shift

    local safe_label
    safe_label=$(echo "$label" | tr -c 'A-Za-z0-9_.-' '_')
    local logfile="$RUN_LOG_DIR/${safe_label}.log"

    if [ "${FACEAUTH_VERBOSE:-0}" = "1" ]; then
        "$@"
    else
        "$@" >"$logfile" 2>&1
    fi

    return $?
}

run_required() {
    local label="$1"
    shift

    local safe_label
    safe_label=$(echo "$label" | tr -c 'A-Za-z0-9_.-' '_')
    local logfile="$RUN_LOG_DIR/${safe_label}.log"

    if run_capture "$label" "$@"; then
        return 0
    fi

    echo ""
    echo "Command failed: $*"
    echo "Log file: $logfile"

    if [ -s "$logfile" ]; then
        echo ""
        echo "Last lines from log:"
        tail -80 "$logfile"
    fi

    return 1
}

run_optional() {
    local label="$1"
    local message="$2"
    shift 2

    local safe_label
    safe_label=$(echo "$label" | tr -c 'A-Za-z0-9_.-' '_')
    local logfile="$RUN_LOG_DIR/${safe_label}.log"

    if run_capture "$label" "$@"; then
        return 0
    fi

    note "$message"
    note "Details saved to: $logfile"
    return 0
}

# Install dependencies
step "Installing system dependencies"

PIP_ARGS=()

install_python_packages() {
    step "Installing Python face recognition packages"

    PIP_ARGS=()

    if python3 -m pip install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
        PIP_ARGS+=(--break-system-packages)
    fi

    if python3 -m pip install --help 2>/dev/null | grep -q -- "--root-user-action"; then
        PIP_ARGS+=(--root-user-action=ignore)
    fi

    note "Preparing Python packaging tools"
    run_optional \
        "pip-tools" \
        "Could not upgrade pip/setuptools/wheel. Continuing with existing versions." \
        sudo python3 -m pip install --upgrade "pip" "setuptools<81" "wheel" "${PIP_ARGS[@]}"

    note "Installing FaceAuth Python packages"
    note "Full pip logs are saved in: $RUN_LOG_DIR"

    if ! run_capture "pip-faceauth-full" \
        sudo python3 -m pip install \
            "setuptools<81" \
            face_recognition \
            git+https://github.com/ageitgey/face_recognition_models \
            opencv-python \
            "${PIP_ARGS[@]}"; then

        note "Full pip install failed. Trying fallback without opencv-python because distro OpenCV may already be installed."

        run_required \
            "pip-faceauth-fallback" \
            sudo python3 -m pip install \
                "setuptools<81" \
                face_recognition \
                git+https://github.com/ageitgey/face_recognition_models \
                "${PIP_ARGS[@]}" || die "Python face recognition packages could not be installed."
    fi
}


install_fedora_deps() {
    echo "Fedora/RHEL-based system detected"
    note "Using direct package install. Skipping optional development groups to avoid dnf/dnf5 group-name differences."

    run_required \
        "dnf-required" \
        sudo dnf install -y \
            python3 python3-pip python3-devel \
            gcc gcc-c++ make cmake git \
            pam-devel \
            python3-opencv \
            blas-devel lapack-devel \
            libX11-devel || die "Required Fedora dependencies could not be installed."

    note "Skipping optional Fedora dlib RPM packages. Pip/face_recognition will provide dlib if needed."
}


install_debian_deps() {
    echo "Debian/Ubuntu-based system detected"

    run_required "apt-update" sudo apt update || die "apt update failed."

    if ! run_capture \
        "apt-full" \
        sudo apt install -y \
            python3 python3-pip python3-dev \
            build-essential cmake git \
            libpam0g-dev \
            libdlib-dev \
            libopencv-dev python3-opencv \
            libblas-dev liblapack-dev \
            libx11-dev; then

        note "Some Debian/Ubuntu package names were unavailable. Trying smaller fallback package set."

        run_required \
            "apt-fallback" \
            sudo apt install -y \
                python3 python3-pip python3-dev \
                build-essential cmake git \
                libpam0g-dev \
                python3-opencv \
                libblas-dev liblapack-dev \
                libx11-dev || die "Required Debian/Ubuntu dependencies could not be installed."
    fi
}


install_arch_deps() {
    echo "Arch-based system detected"

    if ! run_capture \
        "pacman-full" \
        sudo pacman -S --noconfirm --needed \
            python python-pip \
            base-devel cmake git \
            pam \
            dlib opencv \
            blas lapack \
            libx11; then

        note "Some Arch packages were unavailable. Trying minimum fallback package set."

        run_required \
            "pacman-fallback" \
            sudo pacman -S --noconfirm --needed \
                python python-pip \
                base-devel cmake git \
                pam \
                opencv \
                blas lapack \
                libx11 || die "Required Arch dependencies could not be installed."
    fi
}


if command_exists dnf; then
    install_fedora_deps
elif command_exists apt; then
    install_debian_deps
elif command_exists pacman; then
    install_arch_deps
else
    die "Unsupported distro. Supported package managers for now: dnf, apt, pacman."
fi

install_python_packages

step "Checking FaceAuth Python dependencies"
python3 - <<'PYDEP'
import sys
import warnings

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)

checks = [
    ("setuptools compatibility", "pkg_resources"),
    ("OpenCV", "cv2"),
    ("dlib", "dlib"),
    ("face recognition models", "face_recognition_models"),
    ("face recognition", "face_recognition"),
]

failed = []

for label, module in checks:
    try:
        __import__(module)
        print(f"OK: {label}")
    except BaseException as e:
        print(f"FAILED: {label}: {e}")
        failed.append(label)

if failed:
    print("")
    print("Missing or broken Python modules:", ", ".join(failed))
    sys.exit(1)

print("All Python dependencies are ready.")
PYDEP

if [ $? -ne 0 ]; then
    die "Dependency check failed. Camera, systemd, and PAM were not touched."
fi

echo "Dependencies ready!"

# Detect desktop environment and display manager
step "Detecting desktop environment and display manager"

# XDG_CURRENT_DESKTOP may be a semicolon-separated list (e.g. "KDE" or "KDE;GNOME").
# Lowercase it and look for known desktop names.
XDG_DESKTOP_LC=$(printf '%s' "${XDG_CURRENT_DESKTOP:-}" | tr '[:upper:]' '[:lower:]')
XDG_SESSION_DESKTOP_LC=$(printf '%s' "${XDG_SESSION_DESKTOP:-}" | tr '[:upper:]' '[:lower:]')

DE="gnome"
for candidate in "$XDG_DESKTOP_LC" "$XDG_SESSION_DESKTOP_LC"; do
    [ -z "$candidate" ] && continue
    if printf '%s' "$candidate" | grep -qE '(^|;)kde($|;)' || printf '%s' "$candidate" | grep -q 'plasma'; then
        DE="kde"
        echo "KDE Plasma detected"
        break
    fi
    if printf '%s' "$candidate" | grep -qE '(^|;)gnome($|;)'; then
        DE="gnome"
        echo "GNOME detected"
        break
    fi
done

if [ "$DE" = "gnome" ] && [ -n "$XDG_DESKTOP_LC" ] && [ "$XDG_DESKTOP_LC" != "gnome" ]; then
    echo "Unknown desktop ($XDG_CURRENT_DESKTOP) - defaulting to GNOME-compatible behavior"
fi

# Display manager detection: check running service, then enabled service,
# then the display-manager.service symlink target. Fall back to DE hint.
DM=""

detect_dm() {
    local candidate base dm_link
    for candidate in sddm gdm gdm3 lightdm lxdm plasmalogin; do
        if systemctl is-active --quiet "$candidate" 2>/dev/null; then
            echo "$candidate"
            return
        fi
    done
    for candidate in sddm gdm gdm3 lightdm lxdm plasmalogin; do
        if systemctl is-enabled --quiet "$candidate" 2>/dev/null; then
            echo "$candidate"
            return
        fi
    done
    dm_link=$(readlink -f /etc/systemd/system/display-manager.service 2>/dev/null || true)
    if [ -n "$dm_link" ]; then
        base=$(basename "$dm_link")
        case "$base" in
            sddm.service) echo "sddm"; return ;;
            gdm.service|gdm3.service) echo "gdm"; return ;;
            lightdm.service) echo "lightdm"; return ;;
            lxdm.service) echo "lxdm"; return ;;
            plasmalogin.service|plasma-login.service) echo "plasmalogin"; return ;;
        esac
    fi
    echo ""
}

DM=$(detect_dm)
if [ -z "$DM" ]; then
    if [ "$DE" = "kde" ]; then
        DM="sddm"
        warn "Could not detect display manager. Assuming SDDM for KDE Plasma."
    else
        DM="gdm"
        warn "Could not detect display manager. Assuming GDM for GNOME."
    fi
fi

echo "Desktop environment: $DE"
echo "Display manager: $DM"

# Install program files. Everything is copied from this repository so the
# installed files can never drift from the source (they used to be embedded
# here as separate copies and fell out of sync).
step "Installing FaceAuth program files"

for src in faceauth_common.py faceauth_daemon.py faceauth_pam.py faceauthctl.py; do
    [ -f "$SCRIPT_DIR/$src" ] || die "$src not found next to install.sh. Run the installer from a full FaceAuth checkout."
done

sudo install -d -m 755 /usr/local/lib/faceauth || die "Could not create /usr/local/lib/faceauth."
sudo install -m 644 "$SCRIPT_DIR/faceauth_common.py" /usr/local/lib/faceauth/faceauth_common.py || die "Could not install faceauth_common.py."
sudo install -m 755 "$SCRIPT_DIR/faceauth_daemon.py" /usr/local/bin/faceauth_daemon || die "Could not install the FaceAuth daemon."
sudo install -m 755 "$SCRIPT_DIR/faceauth_pam.py" /usr/local/bin/faceauth || die "Could not install the FaceAuth PAM helper."
sudo install -m 755 "$SCRIPT_DIR/faceauthctl.py" /usr/local/bin/faceauthctl || die "Could not install faceauthctl."

echo "Installed FaceAuth $(/usr/local/bin/faceauthctl version)"

FACEAUTH_HOME="$HOME/.faceauth"
mkdir -p "$FACEAUTH_HOME"
chmod 700 "$FACEAUTH_HOME"

# Pick the camera. An existing choice (from a previous install or
# `faceauthctl set-camera`) is kept unless re-enrollment was requested.
step "Detecting cameras"

EXISTING_CAMERA=$(python3 - <<'PYCAM'
import sys
sys.path.insert(0, "/usr/local/lib/faceauth")
import faceauth_common as common, getpass
config, error = common.load_config(getpass.getuser())
print("" if error else config.get("ir_camera", ""))
PYCAM
)

if [ "$FORCE_ENROLL" != "1" ] && [[ "$EXISTING_CAMERA" =~ ^[0-9]+$ ]]; then
    IR_INDEX="$EXISTING_CAMERA"
    CAMERA_KIND="existing"
    echo "Keeping configured camera index: $IR_INDEX"
    note "To let the installer choose again, run: bash install.sh --force-enroll"
else
    echo "Look at your camera - FaceAuth prefers the IR camera that can see your face."
    CAMERA_INFO=$(/usr/local/bin/faceauthctl detect-camera 2>/dev/null)

    if [ -z "$CAMERA_INFO" ] || [ "$CAMERA_INFO" = "NO_CAMERA" ]; then
        die "No usable camera found. Connect/enable a webcam and run the installer again."
    fi

    IR_INDEX="${CAMERA_INFO%%:*}"
    CAMERA_KIND="${CAMERA_INFO#*:}"

    if ! [[ "$IR_INDEX" =~ ^[0-9]+$ ]]; then
        die "Camera detection returned an invalid camera index: $IR_INDEX"
    fi

    echo "Selected $CAMERA_KIND camera at index: $IR_INDEX"

    if [ "$CAMERA_KIND" = "rgb" ]; then
        warn "No IR camera found. A regular webcam can be fooled by a photo of your face."
    fi
fi

# Save config, keeping any values the user already tuned (tolerance, timeouts).
export IR_INDEX DE DM
python3 - <<'PYCFG' || die "Could not save FaceAuth config."
import getpass, os, sys
sys.path.insert(0, "/usr/local/lib/faceauth")
import faceauth_common as common

username = getpass.getuser()
config, _ = common.load_config(username)
config["ir_camera"] = int(os.environ["IR_INDEX"])
config["desktop"] = os.environ["DE"]
config["display_manager"] = os.environ.get("DM", "")
# Keys from older versions that nothing reads any more.
config.pop("rgb_camera", None)
config.pop("max_attempts", None)
common.save_config(username, config)
print("Config saved!")
PYCFG

# Enroll the face (several samples) unless a usable enrollment already exists.
step "Checking face enrollment"

HAS_ENROLLMENT=0
if [ "$FORCE_ENROLL" != "1" ]; then
    if python3 - <<'PYCHECK'
import getpass, sys
sys.path.insert(0, "/usr/local/lib/faceauth")
import faceauth_common as common
try:
    encodings = common.load_encodings(getpass.getuser())
except (OSError, ValueError) as e:
    print(f"Existing face enrollment could not be read: {e}")
    sys.exit(1)
if not encodings:
    sys.exit(1)
print(f"Keeping existing face enrollment ({len(encodings)} sample(s)).")
PYCHECK
    then
        HAS_ENROLLMENT=1
    fi
fi

if [ "$HAS_ENROLLMENT" != "1" ]; then
    echo "========================================"
    echo "FACE REGISTRATION"
    if [ "$FORCE_ENROLL" = "1" ]; then
        echo "Force re-enroll requested."
    fi
    echo "========================================"

    /usr/local/bin/faceauthctl enroll "$IR_INDEX" --no-restart || \
        die "Face enrollment failed. Try better lighting and make sure only your face is visible. Systemd and PAM were not touched."
fi

# Install systemd service
step "Installing systemd service"
sudo tee /etc/systemd/system/faceauth.service > /dev/null << EOF
[Unit]
Description=FaceAuth Face Recognition Daemon
After=display-manager.service graphical-session.target
Wants=display-manager.service

[Service]
Type=simple
User=$USERNAME
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$USER_ID/bus
Environment=XDG_RUNTIME_DIR=/run/user/$USER_ID
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/bin/bash -c 'until [ -S /run/user/$USER_ID/bus ]; do sleep 0.5; done; sleep 5'
ExecStart=/usr/bin/python3 -u /usr/local/bin/faceauth_daemon $USERNAME
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=graphical.target
EOF

# Install sleep/resume hook
step "Installing sleep/resume recovery hook"

SLEEP_HOOK_DIR="/etc/systemd/system-sleep"
SLEEP_HOOK_FILE="$SLEEP_HOOK_DIR/faceauth"

sudo mkdir -p "$SLEEP_HOOK_DIR" || die "Could not create systemd sleep hook directory: $SLEEP_HOOK_DIR"

sudo tee "$SLEEP_HOOK_FILE" > /dev/null << EOF
#!/bin/bash

case "\$1/\$2" in
    post/*)
        /usr/bin/systemctl restart faceauth.service >/dev/null 2>&1 || true
        ;;
esac

exit 0
EOF

sudo chmod +x "$SLEEP_HOOK_FILE" || die "Could not make sleep hook executable."

if [ ! -x "$SLEEP_HOOK_FILE" ]; then
    die "Sleep/resume hook was not installed correctly."
fi

echo "Sleep/resume recovery hook installed: $SLEEP_HOOK_FILE"

# Setup PAM
step "Setting up PAM"

PAM_LINE="auth sufficient pam_exec.so quiet /usr/local/bin/faceauth"

# Build list of PAM files to configure based on detected DE/DM.
# Each entry is "description:path". Multiple files may be configured
# (e.g. KDE Plasma uses both a screen-locker stack and SDDM for login).
PAM_TARGETS=()

if [ "$DE" = "kde" ]; then
    # KDE Plasma screen locker (kscreenlocker_greet) authenticates against the
    # "kde" PAM stack on most distros; some use a dedicated "kscreenlocker" stack.
    if [ -f /etc/pam.d/kscreenlocker ]; then
        PAM_TARGETS+=("KDE screen locker:/etc/pam.d/kscreenlocker")
    fi
    if [ -f /etc/pam.d/kde ]; then
        PAM_TARGETS+=("KDE screen locker:/etc/pam.d/kde")
    fi
    # SDDM login screen (boot/reboot login).
    if [ -f /etc/pam.d/sddm ]; then
        PAM_TARGETS+=("SDDM login:/etc/pam.d/sddm")
    fi
else
    # GNOME / GDM
    if [ -f /etc/pam.d/gdm-password ]; then
        PAM_TARGETS+=("GDM password login:/etc/pam.d/gdm-password")
    fi
fi

# LightDM fallback when GNOME DE was not positively detected.
if [ "$DM" = "lightdm" ] && [ "$DE" != "kde" ] && [ -f /etc/pam.d/lightdm ]; then
    PAM_TARGETS+=("LightDM login:/etc/pam.d/lightdm")
fi

if [ ${#PAM_TARGETS[@]} -eq 0 ]; then
    warn "No supported PAM file was found for DE=$DE DM=$DM."
    warn "Skipping automatic PAM modification."
    warn "Manually add this line near the top of your login/lockscreen PAM stack:"
    warn "  $PAM_LINE"
fi

# Track backups and changed files for safe rollback on later failures.
declare -a PAM_CHANGED_FILES
declare -a PAM_BACKUPS
PAM_CHANGED_COUNT=0

restore_all_pam_backups() {
    local i
    for i in "${!PAM_CHANGED_FILES[@]}"; do
        sudo cp "${PAM_BACKUPS[$i]}" "${PAM_CHANGED_FILES[$i]}" 2>/dev/null || true
    done
}

configure_pam_file() {
    local label="$1"
    local pam_file="$2"
    local backup

    if [ ! -f "$pam_file" ]; then
        warn "$label: PAM file not found at $pam_file - skipping."
        return 0
    fi

    backup="${pam_file}.faceauth-backup-$(date +%Y%m%d-%H%M%S)"
    sudo cp "$pam_file" "$backup"
    echo "$label: backup created: $backup"

    if sudo grep -Fxq "$PAM_LINE" "$pam_file"; then
        echo "$label: PAM already configured."
        return 0
    fi

    sudo sed -i "1i$PAM_LINE" "$pam_file"

    if sudo grep -Fxq "$PAM_LINE" "$pam_file"; then
        PAM_CHANGED_FILES+=("$pam_file")
        PAM_BACKUPS+=("$backup")
        PAM_CHANGED_COUNT=$((PAM_CHANGED_COUNT + 1))
        echo "$label: PAM configured successfully."
        return 0
    fi

    sudo cp "$backup" "$pam_file"
    warn "$label: PAM modification failed. Backup restored."
    return 1
}

FAILED_PAM=0
for target in "${PAM_TARGETS[@]}"; do
    label="${target%%:*}"
    path="${target#*:}"
    if ! configure_pam_file "$label" "$path"; then
        FAILED_PAM=1
    fi
done

if [ "$FAILED_PAM" = "1" ] && [ "$PAM_CHANGED_COUNT" -gt 0 ]; then
    warn "One or more PAM files could not be configured. Files that succeeded were left in place."
    warn "Backups are next to the original files with a .faceauth-backup-* suffix."
fi

# Enable and start service only after PAM setup has succeeded/skipped safely
step "Starting FaceAuth service"

if ! sudo systemctl daemon-reload; then
    restore_all_pam_backups
    die "systemd daemon-reload failed."
fi

if ! sudo systemctl enable faceauth; then
    restore_all_pam_backups
    die "Could not enable FaceAuth systemd service."
fi

if ! sudo systemctl restart faceauth; then
    restore_all_pam_backups
    die "Could not start FaceAuth systemd service. PAM backups restored if FaceAuth changed them."
fi

sleep 2

if sudo systemctl is-active --quiet faceauth; then
    echo "FaceAuth service is active."
else
    warn "FaceAuth service is not active yet."
    warn "Check logs with: journalctl -u faceauth -e --no-pager"
fi

# Lock screen UI integration is best-effort: the core service above is already
# installed and running, so a problem here is only cosmetic and never fatal.
step "Installing lock screen UI integration"

GNOME_EXT_UUID="faceauth-lockscreen@faceauth.local"
GNOME_EXT_SRC="$SCRIPT_DIR/gnome-extension/$GNOME_EXT_UUID"
KDE_OVERLAY_SRC="$SCRIPT_DIR/kde-overlay"

if [ "$DE" = "kde" ]; then
    PLASMA_VERSION=$(plasmashell --version 2>/dev/null | grep -oE '[0-9]+' | head -1)

    if [ "$PLASMA_VERSION" != "6" ]; then
        note "KDE Plasma 6 not detected (found: ${PLASMA_VERSION:-unknown}) - skipping animated lock screen overlay."
    elif [ ! -d "$KDE_OVERLAY_SRC" ]; then
        warn "kde-overlay assets not found next to install.sh - skipping lock screen overlay."
    else
        # Stage the overlay assets. This does not touch any system file - the
        # package-owned lock screen QML is only patched on explicit opt-in.
        sudo install -d -m 755 /usr/local/share/faceauth/kde-overlay /usr/local/share/faceauth/systemd
        sudo install -m 644 "$KDE_OVERLAY_SRC/FaceAuthOsd.qml" /usr/local/share/faceauth/kde-overlay/FaceAuthOsd.qml
        sudo install -m 644 "$SCRIPT_DIR/systemd/faceauth-kde-repair.path" /usr/local/share/faceauth/systemd/faceauth-kde-repair.path
        sudo install -m 644 "$SCRIPT_DIR/systemd/faceauth-kde-repair.service" /usr/local/share/faceauth/systemd/faceauth-kde-repair.service
        sudo install -m 755 "$KDE_OVERLAY_SRC/faceauth_kde_patch.py" /usr/local/bin/faceauth-kde-patch

        KDE_PATCH_STATE=$(/usr/local/bin/faceauth-kde-patch status 2>/dev/null | cut -d' ' -f1)

        if [ "$KDE_LOCKSCREEN_OVERLAY" = "1" ] || [ "$KDE_PATCH_STATE" = "patched" ]; then
            if [ "$KDE_LOCKSCREEN_OVERLAY" != "1" ]; then
                note "The KDE lock screen overlay was enabled by a previous install - keeping it."
                note "To remove it: faceauthctl disable-kde-ui"
            fi

            if /usr/local/bin/faceauthctl enable-kde-ui; then
                echo "KDE lock screen overlay enabled."
            else
                warn "KDE lock screen overlay could not be enabled. FaceAuth still works normally without it."
                warn "Check with: faceauthctl doctor"
            fi
        else
            note "Face unlock works on KDE without any lock screen changes."
            note "The animated scan indicator needs a patch to the system lock screen file (opt-in, see README):"
            note "  faceauthctl enable-kde-ui"
        fi
    fi
else
    GNOME_SHELL_VERSION=$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1)
    # Only install on Shell versions the extension declares (and has been
    # tested with) - GNOME refuses to load it on any other version anyway.
    GNOME_EXT_VERSIONS=$(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["shell-version"]))' \
        "$GNOME_EXT_SRC/metadata.json" 2>/dev/null)

    if [ -z "$GNOME_SHELL_VERSION" ]; then
        note "gnome-shell not detected - skipping lock screen overlay extension."
    elif [ ! -d "$GNOME_EXT_SRC" ] || [ -z "$GNOME_EXT_VERSIONS" ]; then
        warn "gnome-extension assets not found next to install.sh - skipping lock screen overlay."
    elif ! printf ' %s ' "$GNOME_EXT_VERSIONS" | grep -q " $GNOME_SHELL_VERSION "; then
        note "GNOME Shell $GNOME_SHELL_VERSION is not supported by the lock screen overlay extension (supports: $GNOME_EXT_VERSIONS). Skipping."
        note "Face unlock itself works normally without it."
    else
        EXT_DEST="$HOME/.local/share/gnome-shell/extensions/$GNOME_EXT_UUID"
        mkdir -p "$(dirname "$EXT_DEST")"
        rm -rf "$EXT_DEST"
        cp -r "$GNOME_EXT_SRC" "$EXT_DEST"
        echo "GNOME lock screen extension installed to $EXT_DEST"

        if [ -S "/run/user/$USER_ID/bus" ]; then
            if DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_ID/bus" gnome-extensions enable "$GNOME_EXT_UUID" 2>/dev/null; then
                echo "GNOME lock screen extension enabled."
            else
                note "Could not auto-enable the extension. Enable it manually after logging in:"
                note "  gnome-extensions enable $GNOME_EXT_UUID"
            fi
        else
            note "No active GNOME session found. After your next login, enable the extension with:"
            note "  gnome-extensions enable $GNOME_EXT_UUID"
        fi
    fi
fi

echo ""
echo "========================================"
echo "FaceAuth installed successfully!"
echo "FaceAuth service is started now."
echo "Installer logs saved at: $RUN_LOG_DIR"
echo "Lock your screen to test face unlock."
echo ""
echo "If it does not work immediately:"
echo "  1. Check logs: journalctl -u faceauth -e --no-pager"
echo "  2. Log out and log back in"
echo "  3. Reboot only as a last fallback"
echo ""
echo "To re-register your face later:"
echo "  faceauthctl enroll"
echo "  or: bash install.sh --force-enroll"
echo "========================================"
