# Welcome to Moonraker Documentation

Moonraker is a Python 3 based web server that exposes APIs with which
client applications may use to interact with the 3D printing firmware
[Klipper](https://github.com/Klipper3d/klipper). Communication between
the Klippy host and Moonraker is done over a Unix Domain Socket.  Tornado
is used to provide Moonraker's server functionality.

Users should refer to the [Installation](installation.md) and
[Configuration](configuration.md) sections for documentation on how
to install and configure Moonraker.

Front end and other client developers may refer to the
[External API](./external_api/introduction.md)
documentation.

Backend developers should refer to the
[contributing](contributing.md) section for basic contribution
guidelines prior to creating a pull request.  The
[components](components.md) document provides a brief overview
of how to create a component and interact with Moonraker's
primary internal APIs.
