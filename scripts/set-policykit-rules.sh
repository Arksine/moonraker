#!/bin/bash
# This script installs Moonraker's PolicyKit Rules used to grant access

POLKIT_LEGACY_DIR="/etc/polkit-1/localauthority/50-local.d"
POLKIT_DIR="/etc/polkit-1/rules.d"
POLKIT_USR_DIR="/usr/share/polkit-1/rules.d"

add_polkit_legacy_rules()
{
    RULE_FILE="${POLKIT_LEGACY_DIR}/10-moonraker.pkla"
    report_status "Installing Moonraker PolicyKit Rules (Legacy) to ${RULE_FILE}..."
    ACTIONS="org.freedesktop.systemd1.manage-units"
    ACTIONS="${ACTIONS};org.freedesktop.login1.power-off"
    ACTIONS="${ACTIONS};org.freedesktop.login1.power-off-multiple-sessions"
    ACTIONS="${ACTIONS};org.freedesktop.login1.reboot"
    ACTIONS="${ACTIONS};org.freedesktop.login1.reboot-multiple-sessions"
    ACTIONS="${ACTIONS};org.freedesktop.packagekit.*"
    sudo /bin/sh -c "cat > ${RULE_FILE}" << EOF
[moonraker permissions]
Identity=unix-user:$USER
Action=$ACTIONS
ResultAny=yes
EOF
}

add_polkit_rules()
{
    if [ ! -x "$(command -v pkaction)" ]; then
        echo "PolicyKit not installed"
        exit 1
    fi
    POLKIT_VERSION="$( pkaction --version | grep -Po "(\d?\.\d+)" )"
    report_status "PolicyKit Version ${POLKIT_VERSION} Detected"
    if [ $POLKIT_VERSION = "0.105" ]; then
        # install legacy pkla file
        add_polkit_legacy_rules
        return
    fi
    RULE_FILE=""
    if [ -d $POLKIT_USR_DIR ]; then
        RULE_FILE="${POLKIT_USR_DIR}/moonraker.rules"
    elif [ -d $POLKIT_DIR ]; then
        RULE_FILE="${POLKIT_DIR}/moonraker.rules"
    else
        echo "PolicyKit rules folder not detected"
        exit 1
    fi
    report_status "Installing PolicyKit Rules to ${RULE_FILE}..."
    sudo /bin/sh -c "cat > ${RULE_FILE}" << EOF
// Allow Moonraker User to manage systemd units, reboot and shutdown
// the system
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.systemd1.manage-units" ||
         action.id == "org.freedesktop.login1.power-off" ||
         action.id == "org.freedesktop.login1.power-off-multiple-sessions" ||
         action.id == "org.freedesktop.login1.reboot" ||
         action.id == "org.freedesktop.login1.reboot-multiple-sessions" ||
         action.id.startsWith("org.freedesktop.packagekit.")) &&
        subject.user == "$USER") {
        return polkit.Result.YES;
    }
});
EOF
}

clear_polkit_rules()
{
    report_status "Removing all Moonraker PolicyKit rules"
    sudo rm -f "${POLKIT_LEGACY_DIR}/10-moonraker.pkla"
    sudo rm -f "${POLKIT_USR_DIR}/moonraker.rules"
    sudo rm -f "${POLKIT_DIR}/moonraker.rules"
}

# Helper functions
report_status()
{
    echo -e "\n\n###### $1"
}

verify_ready()
{
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit -1
    fi
}

CLEAR="$1"

if [ $CLEAR = "--clear" ] || [ $CLEAR = "-c" ]; then
    clear_polkit_rules
else
    set -e
    add_polkit_rules
fi
