#!/bin/bash
# LMDB Database restore utility

DATABASE_PATH="${HOME}/printer_data/database"
MOONRAKER_ENV="${HOME}/moonraker-env"
INPUT_FILE="${HOME}/database.backup"

print_help()
{
    echo "Moonraker Database Restore Utility"
    echo
    echo "usage: restore-database.sh [-h] [-e <python env path>] [-d <database path>] [-i <input file>]"
    echo
    echo "optional arguments:"
    echo "  -h                  show this message"
    echo "  -e <env path>       Moonraker Python Environment"
    echo "  -d <database path>  Moonraker LMDB database path to restore to"
    echo "  -i <input file>     backup file to restore from"
    exit 0
}

# Parse command line arguments
while getopts "he:d:i:" arg; do
    case $arg in
        h) print_help;;
        e) MOONRAKER_ENV=$OPTARG;;
        d) DATABASE_PATH=$OPTARG;;
        i) INPUT_FILE=$OPTARG;;
    esac
done

PYTHON_BIN="${MOONRAKER_ENV}/bin/python"
DB_TOOL="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/dbtool.py"

if [ ! -f $PYTHON_BIN ]; then
    echo "No Python binary found at '${PYTHON_BIN}'"
    exit -1
fi

if [ ! -d $DATABASE_PATH ]; then
    echo "No database folder found at '${DATABASE_PATH}'"
    exit -1
fi

if [ ! -f $INPUT_FILE ]; then
    echo "No Database Backup File found at '${INPUT_FILE}'"
    exit -1
fi

if [ ! -f $DB_TOOL ]; then
    echo "Unable to locate dbtool.py at '${DB_TOOL}'"
    exit -1
fi

${PYTHON_BIN} ${DB_TOOL} restore ${DATABASE_PATH} ${INPUT_FILE}
