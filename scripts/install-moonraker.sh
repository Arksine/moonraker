#!/bin/bash
# This script installs Moonraker on a Raspberry Pi machine running
# Raspbian/Raspberry Pi OS based distributions.

PYTHONDIR="${MOONRAKER_VENV:-${HOME}/moonraker-env}"
SYSTEMDDIR="/etc/systemd/system"
REBUILD_ENV="${MOONRAKER_REBUILD_ENV:-n}"
FORCE_DEFAULTS="${MOONRAKER_FORCE_DEFAULTS:-n}"
DISABLE_SYSTEMCTL="${MOONRAKER_DISABLE_SYSTEMCTL:-n}"
SKIP_POLKIT="${MOONRAKER_SKIP_POLKIT:-n}"
CONFIG_PATH="${MOONRAKER_CONFIG_PATH}"
LOG_PATH="${MOONRAKER_LOG_PATH}"
DATA_PATH="${MOONRAKER_DATA_PATH}"
INSTANCE_ALIAS="${MOONRAKER_ALIAS:-moonraker}"
SERVICE_VERSION="1"
MACHINE_PROVIDER="systemd_cli"

package_decode_script=$( cat << EOF
import sys
import json
try:
  ret = json.load(sys.stdin)
except Exception:
  exit(0)
sys.stdout.write(' '.join(ret['debian']))
EOF
)

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
    # Update system package info
    report_status "Running apt-get update..."
    sudo apt-get update --allow-releaseinfo-change

    system_deps="${SRCDIR}/scripts/system-dependencies.json"
    if [ -f "${system_deps}" ]; then
        if [ ! -x "$(command -v python3)" ]; then
            report_status "Installing python3 base package..."
            sudo apt-get install --yes python3
        fi
        PKGS="$( cat ${system_deps} | python3 -c "${package_decode_script}" )"

    else
        echo "Error: system-dependencies.json not found, falling back to legacy pacakge list"
        PKGLIST="${PKGLIST} python3-virtualenv python3-dev python3-libgpiod liblmdb-dev"
        PKGLIST="${PKGLIST} libopenjp2-7 libsodium-dev zlib1g-dev libjpeg-dev packagekit"
        PKGLIST="${PKGLIST} wireless-tools curl"
        PKGS=${PKGLIST}
    fi

    # Install desired packages
    report_status "Installing Moonraker Dependencies:"
    report_status "${PKGS}"
    sudo apt-get install --yes ${PKGS}
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
        #GET_PIP="${HOME}/get-pip.py"
        #curl https://bootstrap.pypa.io/pip/3.6/get-pip.py -o ${GET_PIP}
        #${PYTHONDIR}/bin/python ${GET_PIP}
        #rm ${GET_PIP}
    fi

    # Install/update dependencies
    ${PYTHONDIR}/bin/pip install -r ${SRCDIR}/scripts/moonraker-requirements.txt
}

# Step 5: Initialize data folder
init_data_path()
{
    report_status "Initializing Moonraker Data Path at ${DATA_PATH}"
    config_dir="${DATA_PATH}/config"
    logs_dir="${DATA_PATH}/logs"
    env_dir="${DATA_PATH}/systemd"
    config_file="${DATA_PATH}/config/moonraker.conf"
    [ ! -e "${DATA_PATH}" ] && mkdir ${DATA_PATH}
    [ ! -e "${config_dir}" ] && mkdir ${config_dir}
    [ ! -e "${logs_dir}" ] && mkdir ${logs_dir}
    [ ! -e "${env_dir}" ] && mkdir ${env_dir}
    [ -n "${CONFIG_PATH}" ] && config_file=${CONFIG_PATH}
    # Write initial configuration for first time installs
    if [ ! -f $SERVICE_FILE ] && [ ! -e "${config_file}" ]; then
        report_status "Writing Config File ${config_file}:\n"
        /bin/sh -c "cat > ${config_file}" << EOF
# Moonraker Configuration File

[server]
host: 0.0.0.0
port: 7125
# Make sure the klippy_uds_address is correct.  It is initialized
# to the default address.
klippy_uds_address: /tmp/klippy_uds

[machine]
provider: ${MACHINE_PROVIDER}

EOF
        cat ${config_file}
    fi
}

# Step 6: Install startup script
install_script()
{
    # Create systemd service file
    ENV_FILE="${DATA_PATH}/systemd/moonraker.env"
    if [ ! -f $ENV_FILE ] || [ $FORCE_DEFAULTS = "y" ]; then
        rm -f $ENV_FILE
        env_vars="MOONRAKER_DATA_PATH=\"${DATA_PATH}\""
        [ -n "${CONFIG_PATH}" ] && env_vars="${env_vars}\nMOONRAKER_CONFIG_PATH=\"${CONFIG_PATH}\""
        [ -n "${LOG_PATH}" ] && env_vars="${env_vars}\nMOONRAKER_LOG_PATH=\"${LOG_PATH}\""
        env_vars="${env_vars}\nMOONRAKER_ARGS=\"-m moonraker\""
        env_vars="${env_vars}\nPYTHONPATH=\"${SRCDIR}\"\n"
        echo -e $env_vars > $ENV_FILE
    fi
    [ -f $SERVICE_FILE ] && [ $FORCE_DEFAULTS = "n" ] && return
    report_status "Installing system start script..."
    sudo groupadd -f moonraker-admin
    sudo /bin/sh -c "cat > ${SERVICE_FILE}" << EOF
# systemd service file for moonraker
[Unit]
Description=API Server for Klipper SV${SERVICE_VERSION}
Requires=network-online.target
After=network-online.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=$USER
SupplementaryGroups=moonraker-admin
RemainAfterExit=yes
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHONDIR}/bin/python \$MOONRAKER_ARGS
Restart=always
RestartSec=10
EOF
# Use systemctl to enable the klipper systemd service script
    if [ $DISABLE_SYSTEMCTL = "n" ]; then
        sudo systemctl enable "${INSTANCE_ALIAS}.service"
        sudo systemctl daemon-reload
    fi
}

# Step 7: Validate/Install polkit rules
check_polkit_rules()
{
    if [ ! -x "$(command -v pkaction)" ]; then
        return
    fi
    POLKIT_VERSION="$( pkaction --version | grep -Po "(\d+\.?\d*)" )"
    NEED_POLKIT_INSTALL="n"
    if [ "$POLKIT_VERSION" = "0.105" ]; then
        POLKIT_LEGACY_FILE="/etc/polkit-1/localauthority/50-local.d/10-moonraker.pkla"
        # legacy policykit rules don't give users other than root read access
        if sudo [ ! -f $POLKIT_LEGACY_FILE ]; then
            NEED_POLKIT_INSTALL="y"
        fi
    else
        POLKIT_FILE="/etc/polkit-1/rules.d/moonraker.rules"
        POLKIT_USR_FILE="/usr/share/polkit-1/rules.d/moonraker.rules"
        if [ ! -f $POLKIT_FILE ] && [ ! -f $POLKIT_USR_FILE ]; then
            NEED_POLKIT_INSTALL="y"
        fi
    fi
    if [ "${NEED_POLKIT_INSTALL}" = "y" ]; then
        if [ "${SKIP_POLKIT}" = "y" ]; then
            echo -e "\n*** No PolicyKit Rules detected, run 'set-policykit-rules.sh'"
            echo "*** if you wish to grant Moonraker authorization to manage"
            echo "*** system services, reboot/shutdown the system, and update"
            echo "*** packages."
        else
            report_status "Installing PolKit Rules"
            ${SRCDIR}/scripts/set-policykit-rules.sh -z
            MACHINE_PROVIDER="systemd_dbus"
        fi
    else
        MACHINE_PROVIDER="systemd_dbus"
    fi
}

# Step 8: Start server
start_software()
{
    report_status "Launching Moonraker API Server..."
    sudo systemctl restart ${INSTANCE_ALIAS}
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

# Parse command line arguments
while getopts "rfzxc:l:d:a:" arg; do
    case $arg in
        r) REBUILD_ENV="y";;
        f) FORCE_DEFAULTS="y";;
        z) DISABLE_SYSTEMCTL="y";;
        x) SKIP_POLKIT="y";;
        c) CONFIG_PATH=$OPTARG;;
        l) LOG_PATH=$OPTARG;;
        d) DATA_PATH=$OPTARG;;
        a) INSTANCE_ALIAS=$OPTARG;;
    esac
done

if [ -z "${DATA_PATH}" ]; then
    if [ "${INSTANCE_ALIAS}" = "moonraker" ]; then
        DATA_PATH="${HOME}/printer_data"
    else
        num="$( echo ${INSTANCE_ALIAS} | grep  -Po "moonraker[-_]?\K\d+" || true )"
        if [ -n "${num}" ]; then
            DATA_PATH="${HOME}/printer_${num}_data"
        else
            DATA_PATH="${HOME}/${INSTANCE_ALIAS}_data"
        fi
    fi
fi

SERVICE_FILE="${SYSTEMDDIR}/${INSTANCE_ALIAS}.service"

# Run installation steps defined above
verify_ready
cleanup_legacy
install_packages
create_virtualenv
init_data_path
install_script
check_polkit_rules
if [ $DISABLE_SYSTEMCTL = "n" ]; then
    start_software
fi
