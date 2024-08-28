#!/bin/bash
# This script installs Moonraker's PolicyKit Rules used to grant access

POLKIT_LEGACY_DIR="/etc/polkit-1/localauthority/50-local.d"
POLKIT_DIR="/etc/polkit-1/rules.d"
POLKIT_USR_DIR="/usr/share/polkit-1/rules.d"
MOONRAKER_UNIT="/etc/systemd/system/moonraker.service"
MOONRAKER_GID="-1"

check_moonraker_service()
{

    # Force Add the moonraker-admin group
    sudo groupadd -f moonraker-admin
    [ ! -f $MOONRAKER_UNIT ] && return
    # Make sure the unit file contains supplementary group
    HAS_SUPP="$( grep -cm1 "SupplementaryGroups=moonraker-admin" $MOONRAKER_UNIT || true )"
    [ "$HAS_SUPP" -eq 1 ] && return
    report_status "Adding moonraker-admin supplementary group to $MOONRAKER_UNIT"
    sudo sed -i "/^Type=simple$/a SupplementaryGroups=moonraker-admin" $MOONRAKER_UNIT
    sudo systemctl daemon-reload
}

add_polkit_legacy_rules()
{
    RULE_FILE="${POLKIT_LEGACY_DIR}/10-moonraker.pkla"
    report_status "Installing Moonraker PolicyKit Rules (Legacy) to ${RULE_FILE}..."
    ACTIONS="org.freedesktop.systemd1.manage-units"
    ACTIONS="${ACTIONS};org.freedesktop.login1.power-off"
    ACTIONS="${ACTIONS};org.freedesktop.login1.power-off-multiple-sessions"
    ACTIONS="${ACTIONS};org.freedesktop.login1.reboot"
    ACTIONS="${ACTIONS};org.freedesktop.login1.reboot-multiple-sessions"
    ACTIONS="${ACTIONS};org.freedesktop.login1.halt"
    ACTIONS="${ACTIONS};org.freedesktop.login1.halt-multiple-sessions"
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
    POLKIT_VERSION="$( pkaction --version | grep -Po "(\d+\.?\d*)" )"
    report_status "PolicyKit Version ${POLKIT_VERSION} Detected"
    if [ "$POLKIT_VERSION" = "0.105" ]; then
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
    MOONRAKER_GID=$( getent group moonraker-admin | awk -F: '{printf "%d", $3}' )
    sudo /bin/sh -c "cat > ${RULE_FILE}" << EOF
// Allow Moonraker User to manage systemd units, reboot and shutdown
// the system
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.systemd1.manage-units" ||
         action.id == "org.freedesktop.login1.power-off" ||
         action.id == "org.freedesktop.login1.power-off-multiple-sessions" ||
         action.id == "org.freedesktop.login1.reboot" ||
         action.id == "org.freedesktop.login1.reboot-multiple-sessions" ||
         action.id == "org.freedesktop.login1.halt" ||
         action.id == "org.freedesktop.login1.halt-multiple-sessions" ||
         action.id.startsWith("org.freedesktop.packagekit.")) &&
        subject.user == "$USER") {
        // Only allow processes with the "moonraker-admin" supplementary group
        // access
        var regex = "^Groups:.+?\\\s$MOONRAKER_GID[\\\s\\\0]";
        var cmdpath = "/proc/" + subject.pid.toString() + "/status";
        try {
            polkit.spawn(["grep", "-Po", regex, cmdpath]);
            return polkit.Result.YES;
        } catch (error) {
            return polkit.Result.NOT_HANDLED;
        }
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

CLEAR="n"
ROOT="n"
DISABLE_SYSTEMCTL="n"

# Parse command line arguments
while :; do
    case $1 in
        -c|--clear)
            CLEAR="y"
            ;;
        -r|--root)
            ROOT="y"
            ;;
        -z|--disable-systemctl)
            DISABLE_SYSTEMCTL="y"
            ;;
        *)
            break
    esac

    shift
done

if [ "$ROOT" = "n" ]; then
    verify_ready
fi

if [ "$CLEAR" = "y" ]; then
    clear_polkit_rules
else
    set -e
    check_moonraker_service
    add_polkit_rules
    if [ $DISABLE_SYSTEMCTL = "n" ]; then
        report_status "Restarting Moonraker..."
        sudo systemctl restart moonraker
    fi
fi
