import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const POLL_FALLBACK_SECONDS = 2;
const STALE_SECONDS = 10;

const STATE_LABELS = {
    idle: '',
    scanning: 'Scanning for your face…',
    success: 'Face recognized',
    timeout: 'Not recognized — retrying automatically',
    error: 'Camera unavailable',
};

const FaceAuthIndicator = GObject.registerClass(
class FaceAuthIndicator extends St.Widget {
    _init() {
        super._init({
            layout_manager: new Clutter.BinLayout(),
            x_expand: true,
            y_expand: true,
            reactive: false,
        });

        this._box = new St.BoxLayout({
            style_class: 'faceauth-indicator',
            vertical: true,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.END,
            y_expand: true,
            reactive: false,
        });
        this._box.set_translation(0, -96, 0);
        this.add_child(this._box);

        this._ringWrap = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            x_align: Clutter.ActorAlign.CENTER,
            width: 84,
            height: 84,
        });
        this._box.add_child(this._ringWrap);

        this._ring = new St.Widget({
            style_class: 'faceauth-ring',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            width: 84,
            height: 84,
            pivot_point: new Clutter.Point({x: 0.5, y: 0.5}),
        });
        this._ringWrap.add_child(this._ring);

        this._icon = new St.Icon({
            icon_name: 'camera-web-symbolic',
            style_class: 'faceauth-icon',
            icon_size: 30,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._ringWrap.add_child(this._icon);

        this._label = new St.Label({
            style_class: 'faceauth-label',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._label.clutter_text.set_line_wrap(true);
        this._box.add_child(this._label);

        this._pulsing = false;
        this._hideTimeoutId = 0;
        this._state = 'idle';

        this.opacity = 0;
        this.visible = false;
    }

    setState(state, extra = {}) {
        if (this._hideTimeoutId) {
            GLib.source_remove(this._hideTimeoutId);
            this._hideTimeoutId = 0;
        }

        this._state = state;

        for (const cls of ['faceauth-state-scanning', 'faceauth-state-success',
            'faceauth-state-timeout', 'faceauth-state-error']) {
            this._box.remove_style_class_name(cls);
        }

        if (state === 'idle') {
            this._stopPulse();
            this._fadeOut();
            return;
        }

        this._box.add_style_class_name(`faceauth-state-${state}`);

        let text = STATE_LABELS[state] || '';
        if (state === 'timeout' && extra.retry_in)
            text = `Not recognized — retrying in ${extra.retry_in}s`;
        this._label.set_text(text);

        this._fadeIn();

        if (state === 'scanning')
            this._startPulse();
        else
            this._stopPulse();

        if (state === 'success' || state === 'error') {
            this._hideTimeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
                this._hideTimeoutId = 0;
                this._fadeOut();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    _fadeIn() {
        this.visible = true;
        this.ease({
            opacity: 255,
            duration: 200,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    _fadeOut() {
        this.ease({
            opacity: 0,
            duration: 300,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (this.opacity === 0)
                    this.visible = false;
            },
        });
    }

    _startPulse() {
        if (this._pulsing)
            return;
        this._pulsing = true;
        this._pulseStep(true);
    }

    _stopPulse() {
        this._pulsing = false;
        this._ring.remove_all_transitions();
        this._ring.set_scale(1, 1);
        this._ring.opacity = 255;
    }

    _pulseStep(expanding) {
        if (!this._pulsing)
            return;

        const scale = expanding ? 1.35 : 1.0;
        const opacity = expanding ? 60 : 255;

        this._ring.ease({
            scale_x: scale,
            scale_y: scale,
            opacity,
            duration: 900,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            onComplete: () => this._pulseStep(!expanding),
        });
    }

    destroy() {
        this._stopPulse();
        if (this._hideTimeoutId) {
            GLib.source_remove(this._hideTimeoutId);
            this._hideTimeoutId = 0;
        }
        super.destroy();
    }
});

export default class FaceAuthLockscreenExtension extends Extension {
    enable() {
        this._indicator = new FaceAuthIndicator();
        Main.layoutManager.screenShieldGroup.add_child(this._indicator);

        this._statusPath = `${GLib.get_user_runtime_dir()}/faceauth/status.json`;
        this._monitor = null;
        this._pollId = 0;
        this._changedId = 0;

        this._setupMonitor();
        this._refresh();

        this._pollId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, POLL_FALLBACK_SECONDS, () => {
                this._refresh();
                return GLib.SOURCE_CONTINUE;
            });
    }

    _setupMonitor() {
        try {
            const file = Gio.File.new_for_path(this._statusPath);
            this._monitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
            this._changedId = this._monitor.connect('changed', () => this._refresh());
        } catch (e) {
            this._monitor = null;
        }
    }

    _refresh() {
        let state = 'idle';
        let extra = {};

        try {
            const file = Gio.File.new_for_path(this._statusPath);
            const [ok, contents] = file.load_contents(null);
            if (ok) {
                const text = new TextDecoder().decode(contents);
                const data = JSON.parse(text);
                if (typeof data.ts === 'number' &&
                    (GLib.get_real_time() / 1e6 - data.ts) < STALE_SECONDS) {
                    state = data.state || 'idle';
                    extra = data;
                }
            }
        } catch (e) {
            // Missing/unreadable/invalid status file just means "idle".
        }

        if (this._indicator)
            this._indicator.setState(state, extra);
    }

    disable() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        if (this._monitor) {
            if (this._changedId)
                this._monitor.disconnect(this._changedId);
            this._monitor.cancel();
            this._monitor = null;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
