#!/bin/bash
# Moonraker uninstall script for Raspbian/Raspberry Pi OS

stop_service() {
    # Stop Moonraker Service
    echo "#### Stopping Moonraker Service.."
    sudo service moonraker stop
}

remove_service() {
    # Remove Moonraker from Startup
    echo
    echo "#### Removing Moonraker from Startup.."
    sudo update-rc.d -f moonraker remove

    # Remove Moonraker from Services
    echo
    echo "#### Removing Moonraker Service.."
    sudo rm -f /etc/init.d/moonraker /etc/default/moonraker

}

remove_sudo_fix() {
    echo
    echo "#### Removing sudo_fix"
    sudo gpasswd -d $USER mnrkrsudo
    sudo delgroup --only-if-empty mnrkrsudo
    sudo rm -f /etc/sudoers.d/020-sudo-for-moonraker 
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
remove_sudo_fix