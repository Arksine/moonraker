#!/bin/bash
# This script installs Moonraker on a Raspberry Pi machine running
# Raspbian/Raspberry Pi OS based distributions.

PYTHONDIR="${HOME}/moonraker-env"
SYSTEMDDIR="/etc/systemd/system"
REBUILD_ENV="n"
FORCE_DEFAULTS="n"
CONFIG_PATH="${HOME}/moonraker.conf"
LOG_PATH="/tmp/moonraker.log"

# Step 1:  Verify Klipper has been installed
check_klipper()
{
    if [ "$(sudo systemctl list-units --full -all -t service --no-legend | grep -F "klipper.service")" ]; then
        echo "Klipper service found!"
    else
        echo "Klipper service not found, please install Klipper first"
        exit -1
    fi

}

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
    PKGLIST="${PKGLIST} curl libcurl4-openssl-dev libssl-dev liblmdb0"
    PKGLIST="${PKGLIST} libsodium-dev zlib1g-dev"

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
        virtualenv -p /usr/bin/python3 ${PYTHONDIR}
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
    sudo /bin/sh -c "cat > ${SERVICE_FILE}" << EOF
#Systemd service file for moonraker
[Unit]
Description=API Server for Klipper
After=network.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=$USER
RemainAfterExit=yes
WorkingDirectory=${SRCDIR}
ExecStart=${LAUNCH_CMD} -c ${CONFIG_PATH} -l ${LOG_PATH}
Restart=always
RestartSec=10
EOF
# Use systemctl to enable the klipper systemd service script
    sudo systemctl enable moonraker.service
    sudo systemctl daemon-reload
}


# Step 6: Start server
start_software()
{
    report_status "Launching Moonraker API Server..."
    sudo systemctl stop klipper
    sudo systemctl restart moonraker
    sudo systemctl start klipper
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
while getopts "rfc:l:" arg; do
    case $arg in
        r) REBUILD_ENV="y";;
        f) FORCE_DEFAULTS="y";;
        c) CONFIG_PATH=$OPTARG;;
        l) LOG_PATH=$OPTARG;;
    esac
done

# Run installation steps defined above
verify_ready
check_klipper
cleanup_legacy
install_packages
create_virtualenv
install_script
start_software
