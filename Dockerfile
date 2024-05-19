## Runtime Image
FROM python:3.12-alpine as run


RUN apk add openjpeg py3-libgpiod libcurl libssl3 libsodium-dev libjpeg-turbo tiff libxcb zlib-dev iproute2 && \
rm -rf /var/cache/apk/*


WORKDIR /opt
COPY . /opt/moonraker/
RUN addgroup moonraker --gid 1000 \
 && adduser moonraker -u 1000 -G moonraker -D \
 && addgroup moonraker moonraker && addgroup moonraker dialout && \
 pdatadir="/opt/printer_data/" &&\
 mkdir -p "$pdatadir/run" "$pdatadir/gcodes" "$pdatadir/logs" "$pdatadir/database" "$pdatadir/config" && \
 cp /opt/moonraker/docs/moonraker.conf /opt/printer_data/config/moonraker.conf && \
 python -m venv venv && \
 apk add --no-cache gcc patch linux-headers musl-dev &&\
 venv/bin/pip install -r moonraker/scripts/moonraker-requirements.txt &&  \
 apk del gcc patch linux-headers musl-dev && rm -rf /var/cache/apk/* && \ 
 chown -R moonraker:moonraker /opt/* 

## Start Moonraker
USER moonraker
EXPOSE 7125
# VOLUME ["/opt/printer_data/run", "/opt/printer_data/gcodes", "/opt/printer_data/logs", "/opt/printer_data/database", "/opt/printer_data/config"]
ENTRYPOINT ["/opt/venv/bin/python", "moonraker/moonraker/moonraker.py"]
CMD ["-d", "/opt/printer_data"]
