#!/bin/sh
# System startup script for Moonraker,  Klipper's API Server

### BEGIN INIT INFO
# Provides:          moonraker
# Required-Start:    $local_fs
# Required-Stop:
# X-Start-Before:    $klipper
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: moonraker daemon
# Description:       Starts the Moonraker daemon
### END INIT INFO

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
DESC="moonraker daemon"
NAME="moonraker"
DEFAULTS_FILE=/etc/default/moonraker
PIDFILE=/var/run/moonraker.pid

. /lib/lsb/init-functions

# Read defaults file
[ -r $DEFAULTS_FILE ] && . $DEFAULTS_FILE

case "$1" in
start)  log_daemon_msg "Starting moonraker" $NAME
        start-stop-daemon --start --quiet --exec $MOONRAKER_EXEC \
                          --background --pidfile $PIDFILE --make-pidfile \
                          --chuid $MOONRAKER_USER --user $MOONRAKER_USER \
                          -- $MOONRAKER_ARGS
        log_end_msg $?
        ;;
stop)   log_daemon_msg "Stopping moonraker" $NAME
        killproc -p $PIDFILE $MOONRAKER_EXEC
        RETVAL=$?
        [ $RETVAL -eq 0 ] && [ -e "$PIDFILE" ] && rm -f $PIDFILE
        log_end_msg $RETVAL
        ;;
restart) log_daemon_msg "Restarting moonraker" $NAME
        $0 stop
        $0 start
        ;;
reload|force-reload)
        log_daemon_msg "Reloading configuration not supported" $NAME
        log_end_msg 1
        ;;
status)
        status_of_proc -p $PIDFILE $MOONRAKER_EXEC $NAME && exit 0 || exit $?
        ;;
*)      log_action_msg "Usage: /etc/init.d/moonraker {start|stop|status|restart|reload|force-reload}"
        exit 2
        ;;
esac
exit 0
