#  Moonraker - API Web Server for Klipper

Moonraker is a Python 3 based web server that exposes APIs with which
client applications may use interact with Klipper. Communcation between
the Klippy host and Moonraker is done over a Unix Domain Socket.

Moonraker depends on Tornado for its server functionality.  Moonraker
does not come bundled with a client, you will need to install one,
such as [Mainsail](https://github.com/meteyou/mainsail).
