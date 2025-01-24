#!/bin/bash
# Helper Script for fetching the API Key from a moonraker database
DATABASE_PATH="${HOME}/printer_data/database"
MOONRAKER_ENV="${HOME}/moonraker-env"
DB_ARGS="--read=READ --db=authorized_users get _API_KEY_USER_"
API_REGEX='(?<="api_key": ")([^"]+)'
GENERATE_NEW="n"

print_help()
{
    echo "Moonraker API Key Extraction Utility"
    echo
    echo "usage: fetch-apikey.sh [-h] [-g] [-e <python env path>] [-d <database path>]"
    echo
    echo "optional arguments:"
    echo "  -h                  show this message"
    echo "  -g                  generate new API Key"
    echo "  -e <env path>       path to Moonraker env folder"
    echo "  -d <database path>  path to Moonraker database folder"
    exit 0
}

# Parse command line arguments
while getopts "hge:d:" arg; do
    case $arg in
        h) print_help;;
        g) GENERATE_NEW="y";;
        e) MOONRAKER_ENV=$OPTARG;;
        d) DATABASE_PATH=$OPTARG;;
    esac
done

PYTHON_BIN="${MOONRAKER_ENV}/bin/python"
SQL_DATABASE="${DATABASE_PATH}/moonraker-sql.db"

SQL_APIKEY_SCRIPT=$(cat << EOF
import sqlite3
import uuid
conn = sqlite3.connect("$SQL_DATABASE")
if "$GENERATE_NEW" == "y":
    new_key = uuid.uuid4().hex
    with conn:
        conn.execute(
            "UPDATE authorized_users SET password = ? WHERE username='_API_KEY_USER_'",
            (new_key,)
        )
res = conn.execute(
    "SELECT password FROM authorized_users WHERE username='_API_KEY_USER_'"
)
print(res.fetchone()[0])
conn.close()
EOF
)

if [ ! -f $PYTHON_BIN ]; then
    # attempt to fall back to system install python
    if [ ! -x "$( which python3 || true )" ]; then
        echo "No Python binary found at '${PYTHON_BIN}' or on the system"
        exit 1
    fi
    PYTHON_BIN="python3"
fi

if [ ! -d $DATABASE_PATH ]; then
    echo "No Moonraker database found at '${DATABASE_PATH}'"
    exit 1
fi

if [ -f "$SQL_DATABASE" ]; then
    echo "Fetching API Key from Moonraker's SQL database..." >&2
    $PYTHON_BIN -c "$SQL_APIKEY_SCRIPT"
    if [ "${GENERATE_NEW}" = "y" ]; then
        echo "New API Key Generated, restart Moonraker to apply" >&2
    fi
else
    echo "Falling back to legacy lmdb database..." >&2
    if [ "${GENERATE_NEW}" = "y" ]; then
        echo "The -g option may only be used with SQL database implementations"
        exit 1
    fi
    ${PYTHON_BIN} -mlmdb --env=${DATABASE_PATH} ${DB_ARGS} | grep -Po "${API_REGEX}"
fi
