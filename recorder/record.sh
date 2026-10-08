#!/bin/sh
# dump1090-recorder - record the Beast output of readsb/dump1090.
#
# Reads BEAST_HOST:BEAST_PORT (the decoder runs with network_mode: host,
# so the default is the Docker host itself) and writes one file per
# RECORD_INTERVAL seconds into RECORD_DIR.
#
# File format: raw Beast TCP stream (0x1a escaped). Re-feed a recording
# into the stack's Beast input port with:
#   nc localhost 30004 < recordings/20261003T120000Z.beast
set -eu

host="${BEAST_HOST:-host.docker.internal}"
port="${BEAST_PORT:-30005}"
dir="${RECORD_DIR:-/recordings}"
interval="${RECORD_INTERVAL:-900}"

mkdir -p "$dir"

echo "dump1090-recorder: ${host}:${port} -> ${dir} (rotating every ${interval}s)"

while :; do
    file="${dir}/$(date -u +%Y%m%dT%H%M%SZ).beast"
    echo "dump1090-recorder: writing ${file}"
    # timeout(1) bounds each file; a dead feed just rotates empty files.
    timeout "$interval" nc -w 10 "$host" "$port" >"$file" || true
    sleep 2
done
