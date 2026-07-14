#!/bin/bash
# This script installs Moonraker on Debian based Linux distros.

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
DEV_INSTALL="${MOONRAKER_DEV_INSTALL:-n}"
PY_INST_TYPE="${MOONRAKER_PYTHON_INSTALL_TYPE:-venv}"
SERVICE_VERSION="1"
DISTRIBUTION=""
DISTRO_VERSION=""
IS_SRC_DIST="n"
PACKAGES=""

# Check deprecated FORCE_DEFAULTS environment variable
if [ ! -z "${MOONRAKER_FORCE_DEFAULTS}" ]; then
    echo "Deprecated MOONRAKER_FORCE_DEFAULTS environment variable"
    echo -e "detected.  Please use MOONRAKER_FORCE_SYSTEM_INSTALL\n"
    FORCE_SYSTEM_INSTALL=$MOONRAKER_FORCE_DEFAULTS
fi

# Check if this is a dev container, apply dev defaults if not set by environment
if [ "${MOONRAKER_VENDOR}" = "vscode-dev" ]; then
    echo "VSCode Dev Container detected..."
    [ -z "${MOONRAKER_DEV_INSTALL}" ] && DEV_INSTALL="y"
    [ -z "${MOONRAKER_PYTHON_INSTALL_TYPE}" ] && PY_INST_TYPE="user"
    [ -z "${MOONRAKER_DISABLE_SYSTEMCTL}" ] && DISABLE_SYSTEMCTL="y"
fi

# Force script to exit if an error occurs
set -e

# Find source director from the pathname of this script
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/.. && pwd )"

# Determine if Moonraker is to be installed from source
if [ -f "${SRCDIR}/moonraker/__init__.py" ]; then
    echo "Installing from Moonraker source..."
    cd $SRCDIR
    IS_SRC_DIST="y"
fi

# Detect Current Distribution
detect_distribution() {
    if [ -f "/etc/os-release" ]; then
        source "/etc/os-release"
        DISTRO_VERSION="$VERSION_ID"
        DISTRIBUTION="$ID"
    fi

    # *** AUTO GENERATED OS PACKAGE SCRIPT START ***
    get_pkgs_script=$(cat << EOF
from __future__ import annotations
import os
import shlex
import re
import pathlib
import logging

from typing import Tuple, Dict, List, Any

def _get_distro_info() -> Dict[str, Any]:
    try:
        import distro
    except ModuleNotFoundError:
        pass
    else:
        return dict(
            distro_id=distro.id(),
            distro_version=distro.version(),
            aliases=distro.like().split()
        )
    release_file = pathlib.Path("/etc/os-release")
    release_info: Dict[str, str] = {}
    with release_file.open("r") as f:
        lexer = shlex.shlex(f, posix=True)
        lexer.whitespace_split = True
        for item in list(lexer):
            if "=" in item:
                key, val = item.split("=", maxsplit=1)
                release_info[key] = val
    return dict(
        distro_id=release_info.get("ID", ""),
        distro_version=release_info.get("VERSION_ID", ""),
        aliases=release_info.get("ID_LIKE", "").split()
    )

def _convert_version(version: str) -> Tuple[str | int, ...]:
    version = version.strip()
    ver_match = re.match(r"\d+(\.\d+)*((?:-|\.).+)?", version)
    if ver_match is not None:
        return tuple([
            int(part) if part.isdigit() else part
            for part in re.split(r"\.|-", version)
        ])
    return (version,)

class SysDepsParser:
    def __init__(self, distro_info: Dict[str, Any] | None = None) -> None:
        if distro_info is None:
            distro_info = _get_distro_info()
        self.distro_id: str = distro_info.get("distro_id", "")
        self.aliases: List[str] = distro_info.get("aliases", [])
        self.distro_version: Tuple[int | str, ...] = tuple()
        version = distro_info.get("distro_version")
        if version:
            self.distro_version = _convert_version(version)
        self.vendor: str = os.getenv("MOONRAKER_VENDOR", "")
        if not self.vendor and pathlib.Path("/etc/rpi-issue").is_file():
            self.vendor = "raspberry-pi"
        exclusions = os.getenv("MOONRAKER_EXCLUDED_PKGS", "")
        self.exclusions: List[str] = [
            excl.strip() for excl in exclusions.split() if excl.strip()
        ]

    def _parse_spec(self, full_spec: str) -> str | None:
        parts = full_spec.split(";", maxsplit=1)
        pkg_name = parts[0].strip()
        if pkg_name in self.exclusions or not pkg_name:
            logging.info(f"Package '{full_spec}' excluded by environment")
            return None
        if len(parts) == 1:
            return pkg_name
        expressions = re.split(r"( and | or )", parts[1].strip())
        if not len(expressions) & 1:
            logging.info(
                f"Requirement specifier is missing an expression "
                f"between logical operators : {full_spec}"
            )
            return None
        last_result: bool = True
        last_logical_op: str | None = "and"
        for idx, exp in enumerate(expressions):
            if idx & 1:
                if last_logical_op is not None:
                    logging.info(
                        "Requirement specifier contains sequential logical "
                        f"operators: {full_spec}"
                    )
                    return None
                logical_op = exp.strip()
                if logical_op not in ("and", "or"):
                    logging.info(
                        f"Invalid logical operator {logical_op} in requirement "
                        f"specifier: {full_spec}")
                    return None
                last_logical_op = logical_op
                continue
            elif last_logical_op is None:
                logging.info(
                    f"Requirement specifier contains two sequential expressions "
                    f"without a logical operator: {full_spec}")
                return None
            dep_parts = re.split(r"(==|!=|<=|>=|<|>)", exp.strip())
            req_var = dep_parts[0].strip().lower()
            if len(dep_parts) != 3:
                logging.info(f"Invalid comparison, must be 3 parts: {full_spec}")
                return None
            elif req_var == "distro_id":
                left_op: str | Tuple[int | str, ...] = self.distro_id
                right_op = dep_parts[2].strip().strip("\"'")
            elif req_var == "vendor":
                left_op = self.vendor
                right_op = dep_parts[2].strip().strip("\"'")
            elif req_var == "distro_version":
                if not self.distro_version:
                    logging.info(
                        "Distro Version not detected, cannot satisfy requirement: "
                        f"{full_spec}"
                    )
                    return None
                left_op = self.distro_version
                right_op = _convert_version(dep_parts[2].strip().strip("\"'"))
            else:
                logging.info(f"Invalid requirement specifier: {full_spec}")
                return None
            operator = dep_parts[1].strip()
            try:
                compfunc = {
                    "<": lambda x, y: x < y,
                    ">": lambda x, y: x > y,
                    "==": lambda x, y: x == y,
                    "!=": lambda x, y: x != y,
                    ">=": lambda x, y: x >= y,
                    "<=": lambda x, y: x <= y
                }.get(operator, lambda x, y: False)
                result = compfunc(left_op, right_op)
                if last_logical_op == "and":
                    last_result &= result
                else:
                    last_result |= result
                last_logical_op = None
            except Exception:
                logging.exception(f"Error comparing requirements: {full_spec}")
                return None
        if last_result:
            return pkg_name
        return None

    def parse_dependencies(self, sys_deps: Dict[str, List[str]]) -> List[str]:
        if not self.distro_id:
            logging.info(
                "Failed to detect current distro ID, cannot parse dependencies"
            )
            return []
        all_ids = [self.distro_id] + self.aliases
        for distro_id in all_ids:
            if distro_id in sys_deps:
                if not sys_deps[distro_id]:
                    logging.info(
                        f"Dependency data contains an empty package definition "
                        f"for linux distro '{distro_id}'"
                    )
                    continue
                processed_deps: List[str] = []
                for dep in sys_deps[distro_id]:
                    parsed_dep = self._parse_spec(dep)
                    if parsed_dep is not None:
                        processed_deps.append(parsed_dep)
                return processed_deps
        else:
            logging.info(
                f"Dependency data has no package definition for linux "
                f"distro '{self.distro_id}'"
            )
        return []
# *** SYSTEM DEPENDENCIES START ***
system_deps = {
    "debian": [
        "python3-virtualenv", "python3-dev", "libopenjp2-7", "libsodium-dev",
        "zlib1g-dev", "libjpeg-dev", "packagekit",
        "wireless-tools; distro_id != 'ubuntu' or distro_version <= '24.04'",
        "iw; distro_id == 'ubuntu' and distro_version >= '24.10'",
        "python3-libcamera; vendor == 'raspberry-pi' and distro_version >= '11'",
        "curl", "build-essential"
    ],
}
# *** SYSTEM DEPENDENCIES END ***
parser = SysDepsParser()
pkgs = parser.parse_dependencies(system_deps)
if pkgs:
    print(' '.join(pkgs), end="")
exit(0)
EOF
)
    # *** AUTO GENERATED OS PACKAGE SCRIPT END ***
    PACKAGES="$( python3 -c "$get_pkgs_script" )"
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
    report_status "Installing Moonraker System Packages..."
    echo "Linux Distribution: ${DISTRIBUTION} ${DISTRO_VERSION}"
    echo "Packages: ${PACKAGES}"
    # Update system package info
    report_status "Running apt-get update..."
    sudo apt-get update --allow-releaseinfo-change

    # Install desired packages
    sudo apt-get install --yes ${PACKAGES}
}

# Step 4: Create python virtual environment
create_virtualenv()
{
    if [ $PY_INST_TYPE = "system" ]; then
        pip_inst="pip install"
    elif [ $PY_INST_TYPE = "user" ]; then
        pip_inst="pip install --user"
    else
        pip_inst="${PYTHONDIR}/bin/pip install"
        report_status "Installing python virtual environment..."

        # If venv exists and user prompts a rebuild, then do so
        if [ -d ${PYTHONDIR} ] && [ $REBUILD_ENV = "y" ]; then
            report_status "Removing old virtualenv"
            rm -rf ${PYTHONDIR}
        fi

        if [ ! -d ${PYTHONDIR} ]; then
            virtualenv -p python3 ${PYTHONDIR}
            #GET_PIP="${HOME}/get-pip.py"
            #curl https://bootstrap.pypa.io/pip/3.6/get-pip.py -o ${GET_PIP}
            #${PYTHONDIR}/bin/python ${GET_PIP}
            #rm ${GET_PIP}
        fi
    fi
    echo "Using pip install command '${pip_inst}'..."
    # Install/update dependencies
    export SKIP_CYTHON=1
    if [ $IS_SRC_DIST = "y" ]; then
        report_status "Installing Moonraker python dependencies..."
        $pip_inst -r ${SRCDIR}/scripts/moonraker-requirements.txt

        if [ $DEV_INSTALL = "y" ]; then
            report_status "Installing dev requirements..."
            $pip_inst -r ${SRCDIR}/scripts/moonraker-speedups.txt
            $pip_inst -r ${SRCDIR}/scripts/moonraker-dev-reqs.txt
            $pip_inst -r ${SRCDIR}/docs/doc-requirements.txt
        elif [ $SPEEDUPS = "y" ]; then
            report_status "Installing Speedups..."
            $pip_inst -r ${SRCDIR}/scripts/moonraker-speedups.txt
        fi
    else
        report_status "Installing Moonraker package via Pip..."
        if [ $DEV_INSTALL = "y" ]; then
            $pip_inst -U moonraker[speedups,dev]
        elif [ $SPEEDUPS = "y" ]; then
            $pip_inst -U moonraker[speedups]
        else
            $pip_inst -U moonraker
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
    if [ ! -d $SYSTEMDDIR ]; then
        report_status "Systemd not detected, aborting service installation"
    fi
    # Create systemd service file
    ENV_FILE="${DATA_PATH}/systemd/moonraker.env"
    if [ ! -f $ENV_FILE ] || [ $FORCE_SYSTEM_INSTALL = "y" ]; then
        report_status "Creating systemd environment file ${ENV_FILE}..."
        rm -f $ENV_FILE
        env_vars="MOONRAKER_DATA_PATH=\"${DATA_PATH}\""
        [ -n "${CONFIG_PATH}" ] && env_vars="${env_vars}\nMOONRAKER_CONFIG_PATH=\"${CONFIG_PATH}\""
        [ -n "${LOG_PATH}" ] && env_vars="${env_vars}\nMOONRAKER_LOG_PATH=\"${LOG_PATH}\""
        env_vars="${env_vars}\nMOONRAKER_ARGS=\"-m moonraker\""
        [ $IS_SRC_DIST = "y" ] && env_vars="${env_vars}\nPYTHONPATH=\"${SRCDIR}\"\n"
        echo -e $env_vars > $ENV_FILE
    fi
    [ -f $SERVICE_FILE ] && [ $FORCE_SYSTEM_INSTALL = "n" ] && return
    report_status "Installing systemd service unit..."
    python_bin="${PYTHONDIR}/bin/python"
    [ $PY_INST_TYPE != "venv" ] && python_bin="python3"
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
ExecStart=${python_bin} \$MOONRAKER_ARGS
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
            py_bin="$PYTHONDIR/bin/python"
            pkg_path="$( $py_bin -c 'import moonraker; print(moonraker.__path__[0])')"
            polkit_script="${pkg_path}/scripts/set-policykit-rules.sh"
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
