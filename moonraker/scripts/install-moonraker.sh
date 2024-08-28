#!/bin/bash
# This script installs Moonraker on Debian based Linux distros.

SUPPORTED_DISTROS="debian"
PYTHONDIR="${MOONRAKER_VENV:-${HOME}/moonraker-env}"
SYSTEMDDIR="/etc/systemd/system"
REBUILD_ENV="${MOONRAKER_REBUILD_ENV:-n}"
FORCE_SYSTEM_INSTALL="${MOONRAKER_FORCE_SYSTEM_INSTALL:-n}"
DISABLE_SYSTEMCTL="${MOONRAKER_DISABLE_SYSTEMCTL:-n}"
SKIP_POLKIT="${MOONRAKER_SKIP_POLKIT:-n}"
CONFIG_PATH="${MOONRAKER_CONFIG_PATH}"
LOG_PATH="${MOONRAKER_LOG_PATH}"
DATA_PATH="${MOONRAKER_DATA_PATH}"
INSTANCE_ALIAS="${MOONRAKER_ALIAS:-moonraker}"
SPEEDUPS="${MOONRAKER_SPEEDUPS:-n}"
SERVICE_VERSION="1"
DISTRIBUTION=""
IS_SRC_DIST="n"
PACKAGES=""

# Check deprecated FORCE_DEFAULTS environment variable
if [ ! -z "${MOONRAKER_FORCE_DEFAULTS}" ]; then
    echo "Deprecated MOONRAKER_FORCE_DEFAULTS environment variable"
    echo -e "detected.  Please use MOONRAKER_FORCE_SYSTEM_INSTALL\n"
    FORCE_SYSTEM_INSTALL=$MOONRAKER_FORCE_DEFAULTS
fi

# Force script to exit if an error occurs
set -e

# Find source director from the pathname of this script
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/.. && pwd )"

# Determine if Moonraker is to be installed from source
if [ -f "${SRCDIR}/moonraker/__init__.py" ]; then
    echo "Installing from Moonraker source..."
    IS_SRC_DIST="y"
fi

# Detect Current Distribution
detect_distribution() {
    distro_list=""
    if [ -f "/etc/os-release" ]; then
        distro_list="$( grep -Po "^ID=\K.+" /etc/os-release || true )"
        like_str="$( grep -Po "^ID_LIKE=\K.+" /etc/os-release || true )"
        if [ ! -z "${like_str}" ]; then
            distro_list="${distro_list} ${like_str}"
        fi
        if [ ! -z "${distro_list}" ]; then
            echo "Found Linux distribution IDs: ${distro_list}"
        else
            echo "Unable to detect Linux Distribution."
        fi
    fi

    distro_id=""
    while [ "$distro_list" != "$distro_id" ]; do
        distro_id="${distro_list%% *}"
        distro_list="${distro_list#$distro_id }"
        supported_dists=$SUPPORTED_DISTROS
        supported_id=""
        while [ "$supported_dists" != "$supported_id" ]; do
            supported_id="${supported_dists%% *}"
            supported_dists="${supported_dists#$supported_id }"
            if [ "$distro_id" = "$supported_id" ]; then
                DISTRIBUTION=$distro_id
                echo "Distribution detected: $DISTRIBUTION"
                break
            fi
        done
        [ ! -z "$DISTRIBUTION" ] && break
    done

    if [ -z "$DISTRIBUTION" ] && [ -x "$( which apt-get || true )" ]; then
        # Fall back to debian if apt-get is deteted
        echo "Found apt-get, falling back to debian distribution"
        DISTRIBUTION="debian"
    fi

    # *** AUTO GENERATED OS PACKAGE DEPENDENCES START ***
    if [ ${DISTRIBUTION} = "debian" ]; then
        PACKAGES="python3-virtualenv python3-dev libopenjp2-7 libsodium-dev zlib1g-dev"
        PACKAGES="${PACKAGES} libjpeg-dev packagekit wireless-tools curl"
        PACKAGES="${PACKAGES} build-essential"
    fi
    # *** AUTO GENERATED OS PACKAGE DEPENDENCES END ***
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
    if [ -z "${PACKAGES}" ]; then
        echo "Unsupported Linux Distribution ${DISTRIBUTION}. "
        echo "Bypassing system package installation."
        return
    fi
    # Update system package info
    report_status "Running apt-get update..."
    sudo apt-get update --allow-releaseinfo-change

    # Install desired packages
    report_status "Installing Moonraker Dependencies:"
    report_status "${PACKAGES}"
    sudo apt-get install --yes ${PACKAGES}
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
    export SKIP_CYTHON=1
    if [ $IS_SRC_DIST = "y" ]; then
        report_status "Installing Moonraker python dependencies..."
        ${PYTHONDIR}/bin/pip install -r ${SRCDIR}/scripts/moonraker-requirements.txt

        if [ ${SPEEDUPS} = "y" ]; then
            report_status "Installing Speedups..."
            ${PYTHONDIR}/bin/pip install -r ${SRCDIR}/scripts/moonraker-speedups.txt
        fi
    else
        report_status "Installing Moonraker package via Pip..."
        if [ ${SPEEDUPS} = "y" ]; then
            ${PYTHONDIR}/bin/pip install -U moonraker[speedups]
        else
            ${PYTHONDIR}/bin/pip install -U moonraker
        fi
    fi
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
        # detect machine provider
        if [ "$( systemctl is-active dbus )" = "active" ]; then
            provider="systemd_dbus"
        else
            provider="systemd_cli"
        fi
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
provider: ${provider}

EOF
        cat ${config_file}
    fi
}

# Step 6: Install startup script
install_script()
{
    # Create systemd service file
    ENV_FILE="${DATA_PATH}/systemd/moonraker.env"
    if [ ! -f $ENV_FILE ] || [ $FORCE_SYSTEM_INSTALL = "y" ]; then
        rm -f $ENV_FILE
        env_vars="MOONRAKER_DATA_PATH=\"${DATA_PATH}\""
        [ -n "${CONFIG_PATH}" ] && env_vars="${env_vars}\nMOONRAKER_CONFIG_PATH=\"${CONFIG_PATH}\""
        [ -n "${LOG_PATH}" ] && env_vars="${env_vars}\nMOONRAKER_LOG_PATH=\"${LOG_PATH}\""
        env_vars="${env_vars}\nMOONRAKER_ARGS=\"-m moonraker\""
        [ $IS_SRC_DIST = "y" ] && env_vars="${env_vars}\nPYTHONPATH=\"${SRCDIR}\"\n"
        echo -e $env_vars > $ENV_FILE
    fi
    [ -f $SERVICE_FILE ] && [ $FORCE_SYSTEM_INSTALL = "n" ] && return
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
    if [ ! -x "$(command -v pkaction || true)" ]; then
        echo "PolKit not installed"
        return
    fi
    if [ "${SKIP_POLKIT}" = "y" ]; then
        echo "Skipping PolKit rules installation"
        return
    fi
    POLKIT_VERSION="$( pkaction --version | grep -Po "(\d+\.?\d*)" )"
    NEED_POLKIT_INSTALL="n"
    if [ $FORCE_SYSTEM_INSTALL = "n" ]; then
        if [ "$POLKIT_VERSION" = "0.105" ]; then
            POLKIT_LEGACY_FILE="/etc/polkit-1/localauthority/50-local.d/10-moonraker.pkla"
            # legacy policykit rules don't give users other than root read access
            if sudo [ ! -f $POLKIT_LEGACY_FILE ]; then
                NEED_POLKIT_INSTALL="y"
            else
                echo "PolKit rules file found at ${POLKIT_LEGACY_FILE}"
            fi
        else
            POLKIT_FILE="/etc/polkit-1/rules.d/moonraker.rules"
            POLKIT_USR_FILE="/usr/share/polkit-1/rules.d/moonraker.rules"
            if sudo [ -f $POLKIT_FILE ]; then
                echo "PolKit rules file found at ${POLKIT_FILE}"
            elif sudo [ -f $POLKIT_USR_FILE ]; then
                echo "PolKit rules file found at ${POLKIT_USR_FILE}"
            else
                NEED_POLKIT_INSTALL="y"
            fi
        fi
    else
        NEED_POLKIT_INSTALL="y"
    fi
    if [ "${NEED_POLKIT_INSTALL}" = "y" ]; then
        report_status "Installing PolKit Rules"
        polkit_script="${SRCDIR}/scripts/set-policykit-rules.sh"
        if [ $IS_SRC_DIST != "y" ]; then
            polkit_script="${PYTHONDIR}/share/moonraker"
            polkit_script="${polkit_script}/scripts/set-policykit-rules.sh"
        fi
        if [ -f "$polkit_script" ]; then
            set +e
            $polkit_script -z
            set -e
        else
            echo "PolKit rule install script not found at $polkit_script"
        fi
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

# Parse command line arguments
while getopts "rfzxsc:l:d:a:" arg; do
    case $arg in
        r) REBUILD_ENV="y";;
        f) FORCE_SYSTEM_INSTALL="y";;
        z) DISABLE_SYSTEMCTL="y";;
        x) SKIP_POLKIT="y";;
        s) SPEEDUPS="y";;
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
detect_distribution
cleanup_legacy
install_packages
create_virtualenv
init_data_path
install_script
check_polkit_rules
if [ $DISABLE_SYSTEMCTL = "n" ]; then
    start_software
fi
