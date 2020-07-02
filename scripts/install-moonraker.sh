#!/bin/bash
# This script installs Moonraker on a Raspberry Pi machine running
# Raspbian/Raspberry Pi OS based distributions.

PYTHONDIR="${HOME}/moonraker-env"

# Step 1:  Verify Klipper has been installed
check_klipper()
{
    if [ "$(systemctl list-units --full -all -t service --no-legend | grep -F "klipper.service")" ]; then
        echo "Klipper service found!"
    else
        echo "Klipper service not found, please install Klipper first"
        exit -1
    fi

}

# Step 2: Install packages
install_packages()
{
    PKGLIST="python3-virtualenv python3-dev nginx"

    # Update system package info
    report_status "Running apt-get update..."
    sudo apt-get update

    # Install desired packages
    report_status "Installing packages..."
    sudo apt-get install --yes ${PKGLIST}
}

# Step 3: Create python virtual environment
create_virtualenv()
{
    report_status "Updating python virtual environment..."

    # Create virtualenv if it doesn't already exist
    [ ! -d ${PYTHONDIR} ] && virtualenv -p /usr/bin/python3 ${PYTHONDIR}

    # Install/update dependencies
    ${PYTHONDIR}/bin/pip install -r ${SRCDIR}/scripts/moonraker-requirements.txt
}

# Step 4: Install startup script
install_script()
{
    report_status "Installing system start script..."
    sudo cp "${SRCDIR}/scripts/moonraker-start.sh" /etc/init.d/moonraker
    sudo update-rc.d moonraker defaults
}

# Step 5: Install startup script config
install_config()
{
    DEFAULTS_FILE=/etc/default/moonraker
    [ -f $DEFAULTS_FILE ] && return

    report_status "Installing system start configuration..."
    sudo /bin/sh -c "cat > $DEFAULTS_FILE" <<EOF
# Configuration for /etc/init.d/moonraker

MOONRAKER_USER=$USER

MOONRAKER_EXEC=${PYTHONDIR}/bin/python

MOONRAKER_ARGS="${SRCDIR}/moonraker/moonraker.py"

EOF
}

# Step 4: Start server
start_software()
{
    report_status "Launching Moonraker API Server..."
    sudo /etc/init.d/klipper stop
    sudo /etc/init.d/moonraker restart
    sudo /etc/init.d/klipper start
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

# Run installation steps defined above
verify_ready
check_klipper
install_packages
create_virtualenv
install_script
install_config
start_software
