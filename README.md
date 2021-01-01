#  Moonraker - API Web Server for Klipper

Moonraker is a Python 3 based web server that exposes APIs with which
client applications may use to interact with Klipper. Communcation between
the Klippy host and Moonraker is done over a Unix Domain Socket.  Tornado
is used to provide Moonraker's server functionality.

Note that Moonraker does not come bundled with a client, you will need to
install one.  The following clients are currently available:
- [Mainsail](https://github.com/meteyou/mainsail) by Meteyou
- [Fluidd](https://github.com/cadriel/fluidd) by Cadriel
- [KlipperScreen](https://github.com/jordanruthe/KlipperScreen) by jordanruthe

### Changes

This section contains changelogs that users and developers may reference
to see if any action is necessary on their part.  The date of the most
recent change is included.

Users:\
[user_changes.md](/docs/user_changes.md) - December 31st 2020

Developers:\
[api_changes.md](/docs/api_changes.md) - December 30th 2020
