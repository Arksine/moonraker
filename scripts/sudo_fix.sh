#!/bin/bash

# moonraker-sudo (mnrkrsudo)
# Provides a specified Group that is intended to elevate user privileges
# to help moonraker with sudo actions, if in CustomPIOS Images with
# Module "password-for-sudo".
#
# Partially used functions from Arcsine
#
# Copyright (C) 2020 Stephan Wendel <me@stephanwe.de>
#
# This file may be distributed under the terms of the GNU GPLv3 license

### Exit on Errors
set -e

### Configuration

SUDOERS_DIR='/etc/sudoers.d'
SUDOERS_FILE='020-sudo-for-moonraker'
NEW_GROUP='mnrkrsudo'


### Functions

verify_ready()
{
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit -1
    fi
}

create_sudoers_file()
{

    SCRIPT_TEMP_PATH=/tmp

    report_status "Creating ${SUDOERS_FILE} ..."
    sudo rm -f $SCRIPT_TEMP_PATH/$SUDOERS_FILE
    sudo sed "s/GROUPNAME/$NEW_GROUP/g" > $SCRIPT_TEMP_PATH/$SUDOERS_FILE << '#EOF'

### Elevate moonraker API rights
### Do NOT allow Command Parts only Full Commands
### for example
###
### /sbin/systemctl "reboot", /sbin/apt "update", .....

Defaults!/usr/bin/apt-get env_keep +="DEBIAN_FRONTEND"

Cmnd_Alias REBOOT = /sbin/shutdown -r now, /bin/systemctl "reboot"
Cmnd_Alias SHUTDOWN = /sbin/shutdown now, /sbin/shutdown -h now, /bin/systemctl "poweroff"
Cmnd_Alias APT = /usr/bin/apt-get
Cmnd_Alias SYSTEMCTL = /bin/systemctl



%GROUPNAME ALL=(ALL) NOPASSWD: REBOOT, SHUTDOWN, APT, SYSTEMCTL

#EOF

    report_status "\e[1;32m...done\e[0m"
}

update_env()
{
    report_status "Export System Variable: DEBIAN_FRONTEND=noninteractive"
    sudo /bin/sh -c 'echo "DEBIAN_FRONTEND=noninteractive" >> /etc/environment'
}

verify_syntax()
{
    if [ -n "$(whereis -b visudo | awk '{print $2}')" ]; then

        report_status "\e[1;33mVerifying Syntax of ${SUDOERS_FILE}\e[0m\n"

        if [ $(LANG=C sudo visudo -cf $SCRIPT_TEMP_PATH/$SUDOERS_FILE | grep -c "OK" ) -eq 1 ];
            then
                VERIFY_STATUS=0
                report_status "\e[1;32m$(LANG=C sudo visudo -cf $SCRIPT_TEMP_PATH/$SUDOERS_FILE)\e[0m"
            else
                report_status "\e[1;31mSyntax Error:\e[0m Check File: $SCRIPT_TEMP_PATH/$SUDOERS_FILE"
                exit 1
        fi
    else
        VERIFY_STATUS=0
        report_status "\e[1;31mCommand 'visudo' not found. Skip verifying sudoers file.\e[0m"
    fi
}

install_sudoers_file()
{
    verify_syntax
    if [ $VERIFY_STATUS -eq 0 ];
        then
            report_status "Copying $SCRIPT_TEMP_PATH/$SUDOERS_FILE to $SUDOERS_DIR/$SUDOERS_FILE"
            sudo chmod 0440 $SCRIPT_TEMP_PATH/$SUDOERS_FILE
            sudo cp --preserve=mode $SCRIPT_TEMP_PATH/$SUDOERS_FILE $SUDOERS_DIR/$SUDOERS_FILE
        else
            exit 1
    fi
}

check_update_sudoers_file()
{
    if [ -e "$SUDOERS_DIR/$SUDOERS_FILE" ];
        then
            create_sudoers_file
            if [ -z $(sudo diff $SCRIPT_TEMP_PATH/$SUDOERS_FILE $SUDOERS_DIR/$SUDOERS_FILE) ]
                then
                    report_status "No need to update $SUDOERS_DIR/$SUDOERS_FILE"
                else
                    report_status "$SUDOERS_DIR/$SUDOERS_FILE needs to be updated."
                    install_sudoers_file
            fi
    fi
}


add_new_group()
{
    sudo addgroup --system $NEW_GROUP &> /dev/null
    report_status "\e[1;32m...done\e[0m"
}

add_user_to_group()
{
    sudo usermod -aG $NEW_GROUP $USER &> /dev/null
    report_status "\e[1;32m...done\e[0m"
}

adduser_hint()
{
    report_status "\e[1;31mYou have to REBOOT to take changes effect!\e[0m"
}

# Helper functions
report_status()
{
    echo -e "\n\n###### $1"
}

clean_temp()
{
    sudo rm -f $SCRIPT_TEMP_PATH/$SUDOERS_FILE
}
### Main

verify_ready

if [ -e "$SUDOERS_DIR/$SUDOERS_FILE" ] && [ $(sudo cat /etc/gshadow | grep -c "${NEW_GROUP}") -eq 1 ] && [ $(groups | grep -c "$NEW_GROUP") -eq 1 ];
    then
        check_update_sudoers_file
        report_status "\e[1;32mEverything is setup, nothing to do...\e[0m\n"
        exit 0

    else

        if [ -e "$SUDOERS_DIR/$SUDOERS_FILE" ];
            then
                report_status "\e[1;32mFile exists:\e[0m ${SUDOERS_FILE}"
                check_update_sudoers_file
            else
                report_status "\e[1;31mFile not found:\e[0m ${SUDOERS_FILE}\n"
                create_sudoers_file
                install_sudoers_file
        fi

        if [ $(sudo cat /etc/gshadow | grep -c "${NEW_GROUP}") -eq 1 ];
            then
                report_status "Group ${NEW_GROUP} already exists..."
            else
                report_status "Group ${NEW_GROUP} will be added..."
                add_new_group
        fi

        if [ $(groups | grep -c "$NEW_GROUP") -eq 1 ];
            then
                report_status "User ${USER} is already in $NEW_GROUP..."
            else
                report_status "Adding User ${USER} to Group $NEW_GROUP..."
                add_user_to_group
                adduser_hint
        fi
fi

update_env
clean_temp
exit 0
