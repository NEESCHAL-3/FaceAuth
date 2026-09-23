# FaceAuth

Open source face authentication for Linux. Unlock your screen with your face automatically, while keeping your password as the safe fallback.

## Features

- IR sensor support with automatic detection (IR is preferred: a photo on a phone screen can't fool it the way it can fool a regular webcam)
- Falls back to regular webcam if no IR sensor is available
- Multi-sample face enrollment for reliable matching across lighting and head position
- Camera activates only when the lockscreen wakes
- Automatic screen unlock on face recognition
- Per-user face registration
- `faceauthctl` command for status, diagnostics, camera testing, and re-enrollment
- Animated scan indicator on the GNOME and KDE Plasma lock screens, automatically restored after Plasma updates
- Match-quality check (`faceauthctl check-match`) and logged match distances to diagnose recognition drift
- Safer runtime token storage under `/run/user/<uid>/faceauth`
- Camera scan timeout and retry cooldown
- Sleep/resume recovery hook
- Safe uninstall script
- Best-effort support across major Linux distributions

## Installation

~~~bash
git clone https://github.com/NEESCHAL-3/FaceAuth.git
cd FaceAuth
bash install.sh
~~~

A reboot is usually not required. After installation, lock your screen and test FaceAuth.

## Supported Systems

- Fedora
- Ubuntu  
- Arch Linux
- Any GNOME based Linux distribution
- KDE Plasma (with SDDM or plasma-login)
- Any Linux distribution using GDM or SDDM as the display manager

## How It Works

FaceAuth runs a lightweight background daemon that monitors for lockscreen events. When the lockscreen wakes, the selected camera activates and scans for your registered face.

Once your face is recognized, FaceAuth writes a short-lived runtime token and sends an unlock signal. The unlock signal targets the screensaver D-Bus interface matching your desktop (`org.gnome.ScreenSaver` on GNOME, `org.freedesktop.ScreenSaver` / `org.kde.screensaver` on KDE Plasma), with `loginctl unlock-sessions` as a fallback. The PAM helper verifies the token and allows the unlock.

If no face is recognized within the configured scan timeout, the camera turns off automatically. If the lockscreen is still awake, FaceAuth waits for a cooldown period and then retries scanning.

For security reasons, FaceAuth may require your password once after a full restart, shutdown, or first boot. After that first password unlock, FaceAuth works normally for lockscreen unlocks and sleep/resume unlocks.

## FaceAuth Control Tool

FaceAuth installs a control command:

~~~bash
faceauthctl
~~~

Useful commands:

~~~bash
faceauthctl status
faceauthctl doctor
faceauthctl logs
faceauthctl list-cameras
faceauthctl test-camera
faceauthctl set-camera <index>
faceauthctl enroll
faceauthctl check-match
faceauthctl repair-kde-ui
~~~

`faceauthctl enroll [index]` captures 5 face samples (change with `--samples N`). Move your head slightly between samples and use the lighting you usually have when the screen locks. Enroll on the same camera FaceAuth scans with - a face enrolled on the RGB camera does not match well on the IR camera, and vice versa.

`faceauthctl check-match` looks at the camera for a few seconds and reports how close your face is to the enrollment. `GOOD` means unlock should be reliable; `BORDERLINE` or `NO MATCH` means you should re-enroll. `faceauthctl doctor` runs the same check.

## Lock Screen UI

While a scan is running, FaceAuth shows a live indicator (scanning ring, recognized/retry/error states) directly on the lock screen:

- **GNOME**: installed as a GNOME Shell extension (`faceauth-lockscreen@faceauth.local`). Requires GNOME Shell 45+. If it isn't auto-enabled during install (no active graphical session yet), enable it after logging in with:
  ~~~bash
  gnome-extensions enable faceauth-lockscreen@faceauth.local
  ~~~
- **KDE Plasma**: Plasma 6 does not support swappable lock-screen themes, so the installer applies a small, reversible patch to the system lock screen QML to add the overlay (see `kde-overlay/faceauth_kde_patch.py` for exactly what it changes and how it's guarded). It only runs on Plasma 6. `plasma-desktop` package updates replace that file and drop the patch, so the installer also enables `faceauth-kde-repair.path`, which reapplies it whenever the file changes (and once at boot). If the animated overlay still disappears, reapply it with:
  ~~~bash
  faceauthctl repair-kde-ui
  ~~~
  This UI layer is purely cosmetic — if it's missing, disabled, or fails to load for any reason, face unlock and the password fallback both keep working normally.

Both integrations read `~/.faceauth`-independent runtime state from `$XDG_RUNTIME_DIR/faceauth/status.json`, written by the daemon; nothing sensitive (no face data, no token) is stored there.

## Runtime Behavior

Default behavior:

- Maximum face scan time: 30 seconds
- Retry cooldown while lockscreen is still awake: 20 seconds
- Password remains available as the fallback unlock method
- Reboot, shutdown, or first boot may require password once for security

User configuration and enrollment are stored in `~/.faceauth/`:

- `config.json`: settings (below)
- `encodings.json`: enrolled face samples
- `my_face.jpg`: first enrollment photo (installs from before multi-sample enrollment use this as their only sample)

Re-running `install.sh` keeps your existing camera choice, tuned settings, and enrollment. Use `bash install.sh --force-enroll` to re-detect the camera and enroll again.

Important config options:

- `ir_camera`: camera index used for face unlock
- `enrolled_camera`: camera index the face was enrolled on (the daemon warns if it differs from `ir_camera`)
- `tolerance`: face matching strictness (maximum face distance; lower is stricter, default `0.6`)
- `max_scan_seconds`: how long the camera scans before stopping
- `scan_retry_cooldown_seconds`: how long FaceAuth waits before retrying while the lockscreen is still awake
- `desktop`: detected desktop (`gnome` or `kde`), used to pick the right unlock signal
- `display_manager`: detected display manager (`gdm`, `sddm`, ...), used for PAM setup

## Requirements

- Linux with GNOME or KDE Plasma desktop
- GDM (GNOME) or SDDM or plasma-login (KDE Plasma) recommended for automatic PAM setup
- Webcam or IR sensor
- Python 3.8 or higher
- sudo access for installation

## Uninstall

~~~bash
./uninstall.sh
~~~

To remove FaceAuth and delete local FaceAuth user data:

~~~bash
./uninstall.sh --purge
~~~

## Troubleshooting

Check service status:

~~~bash
systemctl status faceauth --no-pager
~~~

Check logs:

~~~bash
journalctl -u faceauth -e --no-pager
~~~

Run diagnostics:

~~~bash
faceauthctl doctor
~~~

If FaceAuth does not unlock immediately:

1. Make sure the selected camera works with `faceauthctl test-camera`
2. Run `faceauthctl check-match` to see how well your face matches the enrollment
3. Re-register your face with `faceauthctl enroll` (in the lighting you use most)
4. Check logs with `faceauthctl logs` - each failed scan logs its best match distance
5. Log out and log back in
6. Reboot only as a last fallback

## Security Notes

FaceAuth does not remove your password login. Your password remains the fallback method.

FaceAuth uses a short-lived runtime token stored under `/run/user/<uid>/faceauth`. The token is permission-restricted and consumed after successful use.

Prefer an IR camera. A regular (RGB) webcam can be fooled by a photo of your face; `faceauthctl list-cameras` and `enroll` warn when you're using one.

FaceAuth protects against someone at your keyboard, not against software already running as your user. Any program running as you can write the token, and it can typically also unlock your session directly through logind (`loginctl unlock-session`), with or without FaceAuth installed.

For security reasons, password unlock may be required once after reboot, shutdown, or first boot before FaceAuth becomes active for normal lockscreen use.

## Development

Run the tests (no camera needed):

~~~bash
python3 -m unittest discover -s tests
~~~

`install.sh` copies `faceauth_common.py`, `faceauth_daemon.py`, `faceauth_pam.py`, and `faceauthctl.py` from the checkout as-is, so edit those files directly and re-run the installer to deploy.

## Contributing

Pull requests are welcome. For major changes please open an issue first.

## License

MIT
