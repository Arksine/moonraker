# Plugins

Documentation Forthcoming

## Power - Indigo

Useful for those that have both an Insteon ApplianceLinc on their printer
AND manage their Insteon devices with an Indigo server. Tested against
Indigo 7.4/7.5, should work correctly with versions dating back to 6.x.
(I think)

You can test the connectivity yourself by navigating to your Indigo server in
a browser-
http://indigoserver.local:8176/devices/Printer%20Name.json

You should receive an http auth, with correct credentials get a json
dump of the device state/info.

Notes on configuring:
`address` can be DNS or IP, but you must include the port server.local:8176
`output_name` is the Indigo name for the device, use %20 for spaces
