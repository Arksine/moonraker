#!/bin/bash
# Data Path Fix for legacy MainsailOS and FluiddPi installations running
# a single instance of Moonraker with a default configuration

DATA_PATH="${HOME}/printer_data"
DATA_PATH_BKP="${HOME}/.broken_printer_data"
DB_PATH="${HOME}/.moonraker_database"
CONFIG_PATH="${HOME}/klipper_config"
LOG_PATH="${HOME}/klipper_logs"
GCODE_PATH="${HOME}/gcode_files"
MOOONRAKER_CONF="${CONFIG_PATH}/moonraker.conf"
MOONRAKER_LOG="${LOG_PATH}/moonraker.log"

[ ! -d "${DB_PATH}" ] && echo "Error: unable to find database: ${DB_PATH}" && exit 1
[ ! -f "${MOOONRAKER_CONF}" ] && echo "Error: unable to find config: ${MOOONRAKER_CONF}" && exit 1
[ ! -d "${LOG_PATH}" ] && echo "Error: unable to find log path: ${LOG_PATH}" && exit 1
[ ! -d "${GCODE_PATH}" ] && echo "Error: unable to find gcode path: ${GCODE_PATH}" && exit 1

sudo systemctl stop moonraker

[ -d "${DATA_PATH_BKP}" ] && rm -rf ${DATA_PATH_BKP}
[ -d "${DATA_PATH}" ] && echo "Moving broken datapath to ${DATA_PATH_BKP}" && mv ${DATA_PATH} ${DATA_PATH_BKP}

mkdir ${DATA_PATH}

echo "Creating symbolic links..."
ln -s ${DB_PATH} "$DATA_PATH/database"
ln -s ${GCODE_PATH} "$DATA_PATH/gcodes"
ln -s ${LOG_PATH} "$DATA_PATH/logs"
ln -s ${CONFIG_PATH} "$DATA_PATH/config"

~/moonraker-env/bin/python -mlmdb -e ${DB_PATH} -d moonraker edit --delete=validate_install

echo "Running install script"

~/moonraker/scripts/install-moonraker.sh -f -c ${MOOONRAKER_CONF} -l ${MOONRAKER_LOG}
