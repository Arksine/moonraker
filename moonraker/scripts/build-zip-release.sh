#!/bin/bash
# This script builds a zipped source release for Moonraker and Klipper.

install_packages()
{
    PKGLIST="python3-dev curl"

    # Update system package info
    report_status "Running apt-get update..."
    sudo apt-get update

    # Install desired packages
    report_status "Installing packages..."
    sudo apt-get install --yes $PKGLIST
}

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

    if [ ! -d "$SRCDIR/.git" ]; then
        echo "This script must be run from a git repo"
        exit -1
    fi

    if [ ! -d "$KLIPPER_DIR/.git" ]; then
        echo "This script must be run from a git repo"
        exit -1
    fi
}

# Force script to exit if an error occurs
set -e

SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/.. && pwd )"
OUTPUT_DIR="$SRCDIR/.dist"
KLIPPER_DIR="$HOME/klipper"
BETA=""

# Parse command line arguments
while getopts "o:k:b" arg; do
    case $arg in
        o) OUTPUT_DIR=$OPTARG;;
        k) KLIPPER_DIR=$OPTARG;;
        b) BETA="-b";;
    esac
done

[ ! -d $OUTPUT_DIR ] && mkdir $OUTPUT_DIR
verify_ready
if [ "$BETA" = "" ]; then
    releaseTag=$( git -C $KLIPPER_DIR describe --tags `git -C $KLIPPER_DIR rev-list --tags --max-count=1` )
    echo "Checking out Klipper release $releaseTag"
    git -C $KLIPPER_DIR checkout $releaseTag
fi
python3 "$SRCDIR/scripts/build_release.py" -k $KLIPPER_DIR -o $OUTPUT_DIR $BETA
