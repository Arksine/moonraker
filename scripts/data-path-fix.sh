#!/bin/bash
# Data Path Fix for legacy MainsailOS and FluiddPi installations running
# a single instance of Moonraker with a default configuration

DATA_PATH="${HOME}/printer_data"
DATA_PATH_BKP="${HOME}/.broken_printer_data"
DB_PATH="${HOME}/.moonraker_database"
CONFIG_PATH="${HOME}/klipper_config"
LOG_PATH="${HOME}/klipper_logs"
GCODE_PATH="${HOME}/gcode_files"
MOONRAKER_CONF="${CONFIG_PATH}/moonraker.conf"
MOONRAKER_LOG="${LOG_PATH}/moonraker.log"
ALIAS="moonraker"

# Parse command line arguments
while getopts "c:l:d:a:m:g:" arg; do
    case $arg in
        c)
            MOONRAKER_CONF=$OPTARG
            CONFIG_PATH="$( dirname $OPTARG )"
            ;;
        l)
            MOONRAKER_LOG=$OPTARG
            LOG_PATH="$( dirname $OPTARG )"
            ;;
        d)
            DATA_PATH=$OPTARG
            dpbase="$( basename $OPTARG )"
            DATA_PATH_BKP="${HOME}/.broken_${dpbase}"
            ;;
        a)
            ALIAS=$OPTARG
            ;;
        m)
            DB_PATH=$OPTARG
            [ ! -f "${DB_PATH}/data.mdb" ] && echo "No valid database found at ${DB_PATH}" && exit 1
            ;;
        g)
            GCODE_PATH=$OPTARG
            [ ! -d "${GCODE_PATH}" ] && echo "No GCode Path found at ${GCODE_PATH}" && exit 1
            ;;
    esac
done

[ ! -f "${MOONRAKER_CONF}" ] && echo "Error: unable to find config: ${MOONRAKER_CONF}" && exit 1
[ ! -d "${LOG_PATH}" ] && echo "Error: unable to find log path: ${LOG_PATH}" && exit 1

sudo systemctl stop ${ALIAS}

[ -d "${DATA_PATH_BKP}" ] && rm -rf ${DATA_PATH_BKP}
[ -d "${DATA_PATH}" ] && echo "Moving broken datapath to ${DATA_PATH_BKP}" && mv ${DATA_PATH} ${DATA_PATH_BKP}

mkdir ${DATA_PATH}

echo "Creating symbolic links..."
[ -f "${DB_PATH}/data.mdb" ] && ln -s ${DB_PATH} "$DATA_PATH/database"
[ -d "${GCODE_PATH}" ] && ln -s ${GCODE_PATH} "$DATA_PATH/gcodes"
ln -s ${LOG_PATH} "$DATA_PATH/logs"
ln -s ${CONFIG_PATH} "$DATA_PATH/config"

[ -f "${DB_PATH}/data.mdb" ] && ~/moonraker-env/bin/python -mlmdb -e ${DB_PATH} -d moonraker edit --delete=validate_install

echo "Running Moonraker install script..."

~/moonraker/scripts/install-moonraker.sh -f -a ${ALIAS} -d ${DATA_PATH} -c ${MOONRAKER_CONF} -l ${MOONRAKER_LOG}
