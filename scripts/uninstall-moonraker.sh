#!/bin/bash
# Moonraker uninstall script for Raspbian/Raspberry Pi OS

stop_service() {
    # Stop Moonraker Service
    echo "#### Stopping Moonraker Service.."
    sudo systemctl stop moonraker
}

remove_service() {
    # Remove Moonraker LSB/systemd service
    echo
    echo "#### Removing Moonraker Service.."
    if [ -f "/etc/init.d/moonraker" ]; then
        # legacy installation, remove the LSB service
        sudo update-rc.d -f moonraker remove
        sudo rm -f /etc/init.d/moonraker
        sudo rm -f /etc/default/moonraker
    else
        # Remove systemd installation
        sudo systemctl disable moonraker
        sudo rm -f /etc/systemd/system/moonraker.service
        sudo systemctl daemon-reload
        sudo systemctl reset-failed
    fi
}

remove_files() {
    # Remove API Key file from older versions
    if [ -e ~/.klippy_api_key ]; then
        echo "Removing legacy API Key"
        rm ~/.klippy_api_key
    fi

    # Remove API Key file from recent versions
    if [ -e ~/.moonraker_api_key ]; then
        echo "Removing API Key"
        rm ~/.moonraker_api_key
    fi

    # Remove virtualenv
    if [ -d ~/moonraker-env ]; then
        echo "Removing virtualenv..."
        rm -rf ~/moonraker-env
    else
        echo "No moonraker virtualenv found"
    fi

    # Notify user of method to remove Moonraker source code
    echo
    echo "The Moonraker system files and virtualenv have been removed."
    echo
    echo "The following command is typically used to remove source files:"
    echo "  rm -rf ~/moonraker"
    echo
    echo "You may also wish to uninstall nginx:"
    echo "  sudo apt-get remove nginx"
}

verify_ready()
{
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit -1
    fi
}

verify_ready
stop_service
remove_service
remove_files
