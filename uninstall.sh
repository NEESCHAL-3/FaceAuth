#!/bin/bash

echo "========================================"
echo "   FaceAuth Uninstaller"
echo "========================================"

USERNAME=$(whoami)
FACEAUTH_HOME="$HOME/.faceauth"
PAM_LINE="auth sufficient pam_exec.so quiet /usr/local/bin/faceauth"

log() {
    echo ""
    echo "==> $1"
}

warn() {
    echo "WARN: $1"
}

log "Stopping FaceAuth service"
sudo systemctl stop faceauth 2>/dev/null || true
sudo systemctl disable faceauth 2>/dev/null || true

log "Removing systemd service"
sudo rm -f /etc/systemd/system/faceauth.service
sudo systemctl daemon-reload
sudo systemctl reset-failed faceauth 2>/dev/null || true

log "Removing FaceAuth PAM line"

PAM_FILES=(
    "/etc/pam.d/gdm-password"
    "/etc/pam.d/sddm"
    "/etc/pam.d/kde"
    "/etc/pam.d/kscreenlocker"
    "/etc/pam.d/lightdm"
)

for pam_file in "${PAM_FILES[@]}"; do
    if [ -f "$pam_file" ]; then
        if sudo grep -Fxq "$PAM_LINE" "$pam_file"; then
            backup="${pam_file}.faceauth-uninstall-backup-$(date +%Y%m%d-%H%M%S)"
            sudo cp "$pam_file" "$backup"
            sudo sed -i "\|$PAM_LINE|d" "$pam_file"
            echo "Removed FaceAuth PAM line from $pam_file"
            echo "Backup created: $backup"
        fi
    fi
done

log "Removing sleep/resume hook"
sudo rm -f /etc/systemd/system-sleep/faceauth

log "Removing lock screen UI integration"

GNOME_EXT_UUID="faceauth-lockscreen@faceauth.local"
GNOME_EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$GNOME_EXT_UUID"

if [ -d "$GNOME_EXT_DIR" ]; then
    if [ -S "/run/user/$(id -u)/bus" ]; then
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" gnome-extensions disable "$GNOME_EXT_UUID" 2>/dev/null || true
    fi
    rm -rf "$GNOME_EXT_DIR"
    echo "Removed GNOME lock screen extension."
fi

# Same removal path as `faceauthctl disable-kde-ui` (covered by the KDE
# integration test): stop the auto-repair watcher, then strip the patch.
if [ -x /usr/local/bin/faceauthctl ] && /usr/local/bin/faceauthctl help 2>/dev/null | grep -q disable-kde-ui; then
    /usr/local/bin/faceauthctl disable-kde-ui || warn "Could not cleanly remove the KDE lock screen overlay - check /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/ manually."
else
    sudo systemctl disable --now faceauth-kde-repair.path 2>/dev/null || true
    sudo systemctl disable faceauth-kde-repair.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/faceauth-kde-repair.path /etc/systemd/system/faceauth-kde-repair.service
    sudo systemctl daemon-reload

    if [ -x /usr/local/bin/faceauth-kde-patch ]; then
        sudo /usr/local/bin/faceauth-kde-patch remove || warn "Could not cleanly remove the KDE lock screen overlay - check /usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/ manually."
    fi
fi
sudo rm -f /usr/local/bin/faceauth-kde-patch
sudo rm -rf /usr/local/share/faceauth

log "Removing FaceAuth binaries"
sudo rm -f /usr/local/bin/faceauth
sudo rm -f /usr/local/bin/faceauth_daemon
sudo rm -f /usr/local/bin/faceauthctl
sudo rm -rf /usr/local/lib/faceauth

log "Removing runtime token"
rm -rf "/run/user/$(id -u)/faceauth" 2>/dev/null || true

if [ "$1" = "--purge" ]; then
    log "Purging user FaceAuth data"
    rm -rf "$FACEAUTH_HOME"
else
    echo ""
    echo "User FaceAuth data kept at: $FACEAUTH_HOME"
    echo "Run './uninstall.sh --purge' to remove registered face/config too."
fi

echo ""
echo "========================================"
echo "FaceAuth uninstalled successfully."
echo "========================================"
