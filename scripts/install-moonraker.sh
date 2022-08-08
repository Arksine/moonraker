#!/bin/bash
# This script installs Moonraker on a Raspberry Pi machine running
# Raspbian/Raspberry Pi OS based distributions.

PYTHONDIR="${MOONRAKER_VENV:-${HOME}/moonraker-env}"
SYSTEMDDIR="/etc/systemd/system"
REBUILD_ENV="${MOONRAKER_REBUILD_ENV:-n}"
FORCE_DEFAULTS="${MOONRAKER_FORCE_DEFAULTS:-n}"
DISABLE_SYSTEMCTL="${MOONRAKER_DISABLE_SYSTEMCTL:-n}"
CONFIG_PATH="${MOONRAKER_CONFIG_PATH:-${HOME}/moonraker.conf}"
LOG_PATH="${MOONRAKER_LOG_PATH:-/tmp/moonraker.log}"

# Step 2: Clean up legacy installation
cleanup_legacy() {
    if [ -f "/etc/init.d/moonraker" ]; then
        # Stop Moonraker Service
        echo "#### Cleanup legacy install script"
        sudo systemctl stop moonraker
        sudo update-rc.d -f moonraker remove
        sudo rm -f /etc/init.d/moonraker
        sudo rm -f /etc/default/moonraker
    fi
}

# Step 3: Install packages
install_packages()
{
    PKGLIST="python3-virtualenv python3-dev libopenjp2-7 python3-libgpiod"
    PKGLIST="${PKGLIST} curl libcurl4-openssl-dev libssl-dev liblmdb-dev"
    PKGLIST="${PKGLIST} libsodium-dev zlib1g-dev libjpeg-dev packagekit"
    PKGLIST="${PKGLIST} wireless-tools"

    # Update system package info
    report_status "Running apt-get update..."
    sudo apt-get update --allow-releaseinfo-change

    # Install desired packages
    report_status "Installing packages..."
    sudo apt-get install --yes ${PKGLIST}
}

# Step 4: Create python virtual environment
create_virtualenv()
{
    report_status "Installing python virtual environment..."

    # If venv exists and user prompts a rebuild, then do so
    if [ -d ${PYTHONDIR} ] && [ $REBUILD_ENV = "y" ]; then
        report_status "Removing old virtualenv"
        rm -rf ${PYTHONDIR}
    fi

    if [ ! -d ${PYTHONDIR} ]; then
        GET_PIP="${HOME}/get-pip.py"
        virtualenv --no-pip -p /usr/bin/python3 ${PYTHONDIR}
        curl https://bootstrap.pypa.io/pip/3.6/get-pip.py -o ${GET_PIP}
        ${PYTHONDIR}/bin/python ${GET_PIP}
        rm ${GET_PIP}
    fi

    # Install/update dependencies
    ${PYTHONDIR}/bin/pip install -r ${SRCDIR}/scripts/moonraker-requirements.txt
}

# Step 5: Install startup script
install_script()
{
    # Create systemd service file
    SERVICE_FILE="${SYSTEMDDIR}/moonraker.service"
    [ -f $SERVICE_FILE ] && [ $FORCE_DEFAULTS = "n" ] && return
    report_status "Installing system start script..."
    sudo groupadd -f moonraker-admin
    sudo /bin/sh -c "cat > ${SERVICE_FILE}" << EOF
#Systemd service file for moonraker
[Unit]
Description=API Server for Klipper
Requires=network-online.target
After=network-online.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=$USER
SupplementaryGroups=moonraker-admin
RemainAfterExit=yes
WorkingDirectory=${SRCDIR}
ExecStart=${LAUNCH_CMD} -c ${CONFIG_PATH} -l ${LOG_PATH}
Restart=always
RestartSec=10
EOF
# Use systemctl to enable the klipper systemd service script
    if [ $DISABLE_SYSTEMCTL = "n" ]; then
        sudo systemctl enable moonraker.service
        sudo systemctl daemon-reload
    fi
}

check_polkit_rules()
{
    if [ ! -x "$(command -v pkaction)" ]; then
        return
    fi
    POLKIT_VERSION="$( pkaction --version | grep -Po "(\d?\.\d+)" )"
    if [ "$POLKIT_VERSION" = "0.105" ]; then
        POLKIT_LEGACY_FILE="/etc/polkit-1/localauthority/50-local.d/10-moonraker.pkla"
        # legacy policykit rules don't give users other than root read access
        if sudo [ ! -f $POLKIT_LEGACY_FILE ]; then
            echo -e "\n*** No PolicyKit Rules detected, run 'set-policykit-rules.sh'"
            echo "*** if you wish to grant Moonraker authorization to manage"
            echo "*** system services, reboot/shutdown the system, and update"
            echo "*** packages."
        fi
    else
        POLKIT_FILE="/etc/polkit-1/rules.d/moonraker.rules"
        POLKIT_USR_FILE="/usr/share/polkit-1/rules.d/moonraker.rules"
        if [ ! -f $POLKIT_FILE ] && [ ! -f $POLKIT_USR_FILE ]; then
            echo -e "\n*** No PolicyKit Rules detected, run 'set-policykit-rules.sh'"
            echo "*** if you wish to grant Moonraker authorization to manage"
            echo "*** system services, reboot/shutdown the system, and update"
            echo "*** packages."
        fi
    fi
}

# Step 6: Start server
start_software()
{
    report_status "Launching Moonraker API Server..."
    sudo systemctl restart moonraker
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

# Force script to exit if an error occurs
set -e

# Find SRCDIR from the pathname of this script
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/.. && pwd )"
LAUNCH_CMD="${PYTHONDIR}/bin/python ${SRCDIR}/moonraker/moonraker.py"

# Parse command line arguments
while getopts "rfzc:l:" arg; do
    case $arg in
        r) REBUILD_ENV="y";;
        f) FORCE_DEFAULTS="y";;
        z) DISABLE_SYSTEMCTL="y";;
        c) CONFIG_PATH=$OPTARG;;
        l) LOG_PATH=$OPTARG;;
    esac
done

# Run installation steps defined above
verify_ready
cleanup_legacy
install_packages
create_virtualenv
install_script
check_polkit_rules
if [ $DISABLE_SYSTEMCTL = "n" ]; then
    start_software
fi
