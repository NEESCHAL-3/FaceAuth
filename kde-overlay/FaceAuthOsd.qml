/*
    FaceAuth lock screen overlay.

    This file is NOT part of the stock plasma-desktop lockscreen package -
    it is copied in next to LockOsd.qml by the FaceAuth installer and loaded
    by a small Loader block appended to LockScreenUi.qml. If anything in
    here fails to load, the Loader simply shows nothing: the password field
    and authentication flow (defined entirely in LockScreenUi.qml) are
    untouched by this file.
*/
import QtQuick
import QtQuick.Layouts
import QtCore
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.plasma5support as Plasma5Support

Item {
    id: faceAuthOsd

    readonly property string statusPath: {
        // writableLocation() returns a "file://" URL, not a plain path -
        // strip the scheme so shell commands (cat, below) can use it directly.
        let dir = String(StandardPaths.writableLocation(StandardPaths.RuntimeLocation));
        if (dir.startsWith("file://"))
            dir = dir.substring(7);
        return dir + "/faceauth/status.json";
    }
    readonly property int staleSeconds: 10

    property string state: "idle"
    property var extra: ({})

    implicitWidth: content.implicitWidth + Kirigami.Units.largeSpacing * 3
    implicitHeight: content.implicitHeight + Kirigami.Units.largeSpacing * 2
    opacity: state === "idle" ? 0 : 1
    visible: opacity > 0
    Behavior on opacity {
        NumberAnimation { duration: Kirigami.Units.longDuration; easing.type: Easing.InOutQuad }
    }

    Rectangle {
        anchors.fill: parent
        radius: Kirigami.Units.cornerRadius * 2
        color: Kirigami.Theme.backgroundColor
        opacity: 0.75
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 0.12)
    }

    ColumnLayout {
        id: content
        anchors.centerIn: parent
        spacing: Kirigami.Units.smallSpacing

        Item {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 72
            implicitHeight: 72

            Rectangle {
                id: ring
                anchors.centerIn: parent
                width: 72
                height: 72
                radius: width / 2
                color: "transparent"
                border.width: 3
                border.color: faceAuthOsd.stateColor
                opacity: 0.9

                SequentialAnimation on scale {
                    id: pulseAnim
                    running: faceAuthOsd.state === "scanning"
                    loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 1.35; duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 1.35; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
                }
                SequentialAnimation on opacity {
                    running: faceAuthOsd.state === "scanning"
                    loops: Animation.Infinite
                    NumberAnimation { from: 0.9; to: 0.25; duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.25; to: 0.9; duration: 900; easing.type: Easing.InOutSine }
                }
            }

            Kirigami.Icon {
                anchors.centerIn: parent
                width: Kirigami.Units.iconSizes.medium
                height: width
                source: "camera-web-symbolic"
                color: faceAuthOsd.stateColor
            }
        }

        PlasmaComponents3.Label {
            Layout.alignment: Qt.AlignHCenter
            Layout.maximumWidth: Kirigami.Units.gridUnit * 16
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            font.bold: true
            text: faceAuthOsd.statusText
        }
    }

    readonly property color stateColor: {
        switch (state) {
        case "success": return Kirigami.Theme.positiveTextColor;
        case "timeout": return Kirigami.Theme.neutralTextColor;
        case "error": return Kirigami.Theme.negativeTextColor;
        default: return Kirigami.Theme.highlightColor;
        }
    }

    readonly property string statusText: {
        switch (state) {
        case "scanning": return "Scanning for your face…";
        case "success": return "Face recognized";
        case "timeout":
            return extra.retry_in
                ? `Not recognized — retrying in ${extra.retry_in}s`
                : "Not recognized — retrying automatically";
        case "error": return "Camera unavailable";
        default: return "";
        }
    }

    // Local-file XMLHttpRequest is blocked by Qt's QML security policy
    // (QML_XHR_ALLOW_FILE_READ), so status.json is read via the standard
    // Plasma "executable" data engine instead - the same mechanism many
    // Plasma widgets use to shell out to a command periodically.
    readonly property string catCommand: statusPath ? ("cat '" + statusPath + "'") : ""

    function applyStatusText(stdout) {
        try {
            const data = JSON.parse(stdout);
            const now = Date.now() / 1000;
            if (typeof data.ts === "number" && (now - data.ts) < staleSeconds) {
                faceAuthOsd.extra = data;
                faceAuthOsd.state = data.state || "idle";
                return;
            }
        } catch (e) {
            // missing/unreadable/invalid status file just means "idle"
        }
        faceAuthOsd.state = "idle";
    }

    Plasma5Support.DataSource {
        id: statusSource
        engine: "executable"
        interval: 400
        connectedSources: faceAuthOsd.catCommand ? [faceAuthOsd.catCommand] : []
        onNewData: (sourceName, data) => faceAuthOsd.applyStatusText(data["stdout"] || "")
    }
}
