## Get Code and Build venv
FROM python:3 as build

WORKDIR /opt
COPY . moonraker

RUN python -m venv venv \
 && venv/bin/pip install -r moonraker/scripts/moonraker-requirements.txt

## Runtime Image
FROM python:3-slim as run

RUN apt update \
 && apt install -y \
      libopenjp2-7 \
      python3-libgpiod \
      curl \
      libcurl4-openssl-dev \
      libssl-dev \
      liblmdb0 \
      libsodium-dev \
      zlib1g-dev \
  && apt clean

WORKDIR /opt
COPY --from=build /opt/moonraker ./moonraker
COPY --from=build /opt/venv ./venv

RUN mkdir run cfg gcode db
RUN groupadd moonraker --gid 1000 \
 && useradd moonraker --uid 1000 --gid moonraker \
 && usermod moonraker --append --groups dialout \
 && chown -R moonraker:moonraker /opt/*

## Start Moonraker
USER moonraker
EXPOSE 7125
VOLUME ["/opt/run", "/opt/cfg", "/opt/gcode", "/opt/db"]
ENTRYPOINT ["/opt/venv/bin/python", "moonraker/moonraker/moonraker.py"]
CMD ["-c", "cfg/moonraker.cfg"]

