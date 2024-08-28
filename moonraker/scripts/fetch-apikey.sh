#!/bin/bash
# Helper Script for fetching the API Key from a moonraker database
DATABASE_PATH="${HOME}/printer_data/database"
MOONRAKER_ENV="${HOME}/moonraker-env"
DB_ARGS="--read=READ --db=authorized_users get _API_KEY_USER_"
API_REGEX='(?<="api_key": ")([^"]+)'

print_help()
{
    echo "Moonraker API Key Extraction Utility"
    echo
    echo "usage: fetch-apikey.sh [-h] [-e <python env path>] [-d <database path>]"
    echo
    echo "optional arguments:"
    echo "  -h                  show this message"
    echo "  -e <env path>       path to Moonraker env folder"
    echo "  -d <database path>  path to Moonraker LMDB database folder"
    exit 0
}

# Parse command line arguments
while getopts "he:d:" arg; do
    case $arg in
        h) print_help;;
        e) MOONRAKER_ENV=$OPTARG;;
        d) DATABASE_PATH=$OPTARG;;
    esac
done

PYTHON_BIN="${MOONRAKER_ENV}/bin/python"

if [ ! -f $PYTHON_BIN ]; then
    echo "No Python binary found at '${PYTHON_BIN}'"
    exit -1
fi

if [ ! -d $DATABASE_PATH ]; then
    echo "No Moonraker database found at '${DATABASE_PATH}'"
    exit -1
fi

${PYTHON_BIN} -mlmdb --env=${DATABASE_PATH} ${DB_ARGS} | grep -Po "${API_REGEX}"
