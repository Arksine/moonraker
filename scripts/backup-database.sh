#!/bin/bash
# LMDB Database backup utility

DATABASE_PATH="${HOME}/printer_data/database"
MOONRAKER_ENV="${HOME}/moonraker-env"
OUPUT_FILE="${HOME}/database.backup"

print_help()
{
    echo "Moonraker Database Backup Utility"
    echo
    echo "usage: backup-database.sh [-h] [-e <python env path>] [-d <database path>] [-o <output file>]"
    echo
    echo "optional arguments:"
    echo "  -h                  show this message"
    echo "  -e <env path>       Moonraker Python Environment"
    echo "  -d <database path>  Moonraker LMDB database to backup"
    echo "  -o <output file>    backup file to save to"
    exit 0
}

# Parse command line arguments
while getopts "he:d:o:" arg; do
    case $arg in
        h) print_help;;
        e) MOONRAKER_ENV=$OPTARG;;
        d) DATABASE_PATH=$OPTARG;;
        o) OUPUT_FILE=$OPTARG;;
    esac
done

PYTHON_BIN="${MOONRAKER_ENV}/bin/python"
DB_TOOL="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/dbtool.py"

if [ ! -f $PYTHON_BIN ]; then
    echo "No Python binary found at '${PYTHON_BIN}'"
    exit -1
fi

if [ ! -f "$DATABASE_PATH/data.mdb" ]; then
    echo "No Moonraker database found at '${DATABASE_PATH}'"
    exit -1
fi

if [ ! -f $DB_TOOL ]; then
    echo "Unable to locate dbtool.py at '${DB_TOOL}'"
    exit -1
fi

${PYTHON_BIN} ${DB_TOOL} backup ${DATABASE_PATH} ${OUPUT_FILE}
