# dump1090-stack

[![CI](https://github.com/technothatch/dump1090-stack/actions/workflows/ci.yml/badge.svg)](https://github.com/technothatch/dump1090-stack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/technothatch/dump1090-stack/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/technothatch/dump1090-stack)](https://github.com/technothatch/dump1090-stack/releases)
[![Issues](https://img.shields.io/github/issues/technothatch/dump1090-stack)](https://github.com/technothatch/dump1090-stack/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/technothatch/dump1090-stack/pulls)
[![Code size](https://img.shields.io/github/languages/code-size/technothatch/dump1090-stack)](https://github.com/technothatch/dump1090-stack)
[![Last commit](https://img.shields.io/github/last-commit/technothatch/dump1090-stack)](https://github.com/technothatch/dump1090-stack/commits/main)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)](#development)

A production-ready Docker Compose stack that turns a receiver running
**dump1090** / **readsb** into a monitored ADS-B station: decoder, web UI,
metrics, dashboards and alerting in one `docker compose up`. It packages the
pieces you would otherwise wire together by hand - readsb decoding RTL-SDR
input, tar1090 serving the map, dump1090-exporter exposing Prometheus
metrics, Prometheus + Grafana for graphs, and Alertmanager for notifications -
with every file validated by a Python test suite before it reaches a server.

## Features

- **readsb decoder** (`ghcr.io/wiedehopf/readsb`) on `network_mode: host`, so
  feeders, MLAT clients and port 30001-30005/8042 consumers work exactly as
  on a bare-metal install.
- **tar1090 map UI** served by nginx, with the decoder JSON directory
  mounted read-only at `/data`.
- **dump1090-exporter** scraping `/data/*.json` and serving Prometheus
  metrics on `:9702` (own `Dockerfile`, healthcheck included).
- **Prometheus** (15s scrape, configurable retention) with four alert rules:
  `DecoderDown`, `MessageRateLow`, `HighBlocksDropped`, `NoAircraft`.
- **Grafana** with a provisioned Prometheus datasource and a ready-made
  `dump1090 / readsb overview` dashboard (message rate, aircraft count,
  signal, CPU, blocks dropped, scrape duration).
- **Alertmanager** with a webhook receiver example and fast-path routing for
  `severity="critical"`.
- **Optional `recorder` profile** that archives the raw Beast TCP stream to
  rotating files.
- **Validator + tests**: `make config` cross-checks compose variables,
  bind mounts, build contexts, alert metrics and dashboards; the pytest suite
  keeps them from drifting.

## Install

Requirements: Docker with Compose v2, Python 3.11+ (for the validator and
tests), GNU Make (optional - every target is a plain command).

```bash
git clone https://github.com/technothatch/dump1090-stack.git
cd dump1090-stack
cp .env.example .env
python -m pip install -e ".[dev]"
```

## Quickstart

```bash
$ make config
OK: . (compose, .env.example, alerts, dashboards, receivers)

$ make up
[+] Running 8/8
 + Network dump1090-stack_default  Created
 + Container alertmanager          Healthy
 ...

$ docker compose ps
NAME                   STATUS          PORTS
readsb                 running         0.0.0.0:30001-30005->30001-30005/tcp
tar1090                running         0.0.0.0:8080->80/tcp
dump1090-exporter      running         9702/tcp
prometheus             running         127.0.0.1:9090->9090/tcp
grafana                running         0.0.0.0:3000->3000/tcp
alertmanager           running         127.0.0.1:9093->9093/tcp
```

Then open:

- http://localhost:8080/ - tar1090 map
- http://localhost:3000/ - Grafana (default `admin` / `changeme`, change it)
- http://127.0.0.1:9090/ - Prometheus (loopback only; tunnel with
  `ssh -L 9090:localhost:9090 host` over SSH)
- http://127.0.0.1:9093/ - Alertmanager (loopback only)

Stop with `make down`; follow logs with `make logs`.

## Configuration reference

Every variable below is documented in `.env.example`; compose falls back to
the listed default when it is unset.

| Flag / env var / file                    | Meaning                                              | Default                    |
| ---------------------------------------- | ---------------------------------------------------- | -------------------------- |
| `TZ`                                     | Timezone for all containers                          | `UTC`                      |
| `READSB_IMAGE`                           | Decoder image                                        | `ghcr.io/wiedehopf/readsb:latest` |
| `READSB_PRIVILEGED`                      | `true` = privileged container (USB dongle access)    | `true`                     |
| `READSB_DEVICE_TYPE`                     | readsb `--device-type` (`rtlsdr`, `none`, ...)       | `rtlsdr`                   |
| `READSB_ARGS`                            | Extra readsb flags (position, `--net-only`, ...)     | (empty)                    |
| `READSB_API_PORT`                        | readsb HTTP API port on the host                     | `8042`                     |
| `READSB_DATA_DIR`                        | Host dir receiving `aircraft.json` etc.              | `./data/readsb`            |
| `TAR1090_PORT`                           | tar1090 map UI port                                  | `8080`                     |
| `DUMP1090_URL`                           | Decoder origin the exporter scrapes                  | `http://tar1090`           |
| `PROMETHEUS_PORT`                        | Prometheus port (bound to `127.0.0.1`)               | `9090`                     |
| `PROMETHEUS_RETENTION`                   | Local TSDB retention                                 | `15d`                      |
| `GRAFANA_PORT`                           | Grafana port                                         | `3000`                     |
| `GRAFANA_ADMIN_USER`                     | Initial Grafana admin user                           | `admin`                    |
| `GRAFANA_ADMIN_PASSWORD`                 | Initial Grafana admin password                       | `changeme`                 |
| `ALERTMANAGER_PORT`                      | Alertmanager port (bound to `127.0.0.1`)             | `9093`                     |
| `RECORD_HOST`                            | Beast endpoint the recorder connects to              | `host.docker.internal`     |
| `RECORD_PORT`                            | Beast port                                           | `30005`                    |
| `RECORD_INTERVAL`                        | Seconds per recording file                           | `900`                      |
| `RECORD_DATA_DIR`                        | Host dir storing recordings                          | `./recordings`             |
| `compose.yaml`                           | The stack definition (7 services, 1 profile)         | -                          |
| `prometheus/prometheus.yml`              | Scrape config + alertmanager target                  | -                          |
| `prometheus/alerts.yml`                  | The four alert rules (mounted read-only)             | -                          |
| `grafana/provisioning/`                  | Datasource + dashboard provisioning                  | -                          |
| `grafana/dashboards/dump1090.json`       | The overview dashboard                               | -                          |
| `alertmanager/config.yml`                | Webhook receiver example                             | -                          |
| `Makefile`                               | `up` `down` `logs` `config` `test` and friends       | -                          |

### RTL-SDR USB pass-through

The decoder must see the dongle, so `readsb` runs with:

```yaml
privileged: ${READSB_PRIVILEGED:-true}
```

`privileged` grants access to `/dev/bus/usb` (and the USB char devices the
kernel exposes). The equivalent narrower grants, if you prefer them, are:

```yaml
devices:
  - /dev/bus/usb:/dev/bus/usb
# or
volumes:
  - /dev/bus/usb:/dev/bus/usb
```

Docker Desktop (macOS/Windows) cannot pass USB devices into containers at
all - on those hosts set `READSB_PRIVILEGED=false` and use the `--net-only`
mode below.

### Remote feeds with `--net-only` (no local dongle)

If another receiver already exposes Beast on port 30005, or you are on
Docker Desktop, point readsb at it instead of a dongle:

```bash
# .env
READSB_PRIVILEGED=false
READSB_DEVICE_TYPE=none
READSB_ARGS=--net-only --net-connector 192.168.1.10,30005,beast_in
```

`--net-only` runs readsb purely as a network client (`sdr_type=SDR_NONE`),
so no device access is needed; `--net-connector host,port,beast_in` pulls the
Beast stream from the remote decoder.

### Recorder profile

Archive the raw Beast stream (rotating one file per `RECORD_INTERVAL`):

```bash
$ make up-recorder
# or: docker compose --profile recorder up -d --build
```

Re-feed a recording later with:

```bash
nc localhost 30004 < recordings/20261003T120000Z.beast
```

## How it works

```
RTL-SDR dongle (or --net-connector feed)
        |  USB /dev/bus/usb
        v
   readsb (network_mode: host, privileged)
        |  writes /run/readsb/{aircraft,receiver,stats,history_N}.json
        v   bind-mounted read-only
   tar1090 (nginx)  <-- http://localhost:8080  map UI
        |  serves /data/*.json
        v
   dump1090-exporter :9702  --> Prometheus :9090 --> Grafana :3000
                                     |
                                     +--> Alertmanager :9093 --> webhook
```

Alert rules live in `prometheus/alerts.yml` (mounted to
`/etc/prometheus/alerts.yml` and referenced by `rule_files`); the webhook
receiver in `alertmanager/config.yml` points at `host.docker.internal`
(declared via `extra_hosts` in compose) so a sink running on the Docker host
receives POSTs.

## Development

```bash
python -m pip install -e ".[dev]"

ruff check .            # lint
ruff format --check .   # formatting
mypy src                # types
python -m pytest -q --cov --cov-report=term-missing --cov-fail-under=80
```

Or simply `make check` (lint + format check + mypy + tests), `make test`,
`make lint`, `make format`. `make config` runs the same validation CI uses:
compose parses, every `${VAR}` is documented in `.env.example`, every
relative bind source and build context exists, Prometheus scrapes the
exporter, all four alert rules reference metrics `dump1090-exporter` really
emits, dashboards parse against the provisioned datasource, and the
Alertmanager route resolves to a configured receiver.

## Related projects

- [dump1090-fa](https://github.com/flightaware/dump1090) - the original
  dump1090 decoder this ecosystem descends from
- [readsb](https://github.com/wiedehopf/readsb) - the decoder this stack runs
- [tar1090](https://github.com/wiedehopf/tar1090) - the web UI served here
- [graphs1090](https://github.com/wiedehopf/graphs1090) - long-term graphs
  companion
- [dump1090-exporter](https://github.com/technothatch/dump1090-exporter) -
  Prometheus exporter built into this stack

## License

MIT - see [LICENSE](LICENSE).

---

## Related projects

Part of the [technothatch ADS-B toolkit](https://github.com/technothatch/technothatch#readme) - the full index with one-liners for every tool lives there.

- **Run & operate:** [dump1090-stack](https://github.com/technothatch/dump1090-stack) - full Compose stack; [dump1090-healthcheck](https://github.com/technothatch/dump1090-healthcheck) - decoder watchdog; [dump1090-alerts](https://github.com/technothatch/dump1090-alerts) - rule-based alerting
- **Metrics & data:** [dump1090-exporter](https://github.com/technothatch/dump1090-exporter) - Prometheus; [dump1090-influx](https://github.com/technothatch/dump1090-influx) - InfluxDB/Telegraf; [dump1090-grafana](https://github.com/technothatch/dump1090-grafana) - dashboards; [dump1090-recorder](https://github.com/technothatch/dump1090-recorder) - SQLite history; [dump1090-coverage](https://github.com/technothatch/dump1090-coverage) - heatmaps & reports
- **Feeds & networking:** [beast-relay](https://github.com/technothatch/beast-relay) - Beast fan-out; [dump1090-geofence](https://github.com/technothatch/dump1090-geofence) - region-filtered feeds; [dump1090-to-mqtt](https://github.com/technothatch/dump1090-to-mqtt) - MQTT/Home Assistant
- **Interfaces:** [dump1090-dashboard](https://github.com/technothatch/dump1090-dashboard) - web UI; [adsb-tui](https://github.com/technothatch/adsb-tui) - terminal radar; [dump1090-cli](https://github.com/technothatch/dump1090-cli) - aircraft.json CLI; [dump1090-sdk](https://github.com/technothatch/dump1090-sdk) - TypeScript client
- **Testing & decoding:** [dump1090-simulator](https://github.com/technothatch/dump1090-simulator) - synthetic traffic; [dump1090-replay](https://github.com/technothatch/dump1090-replay) - recorded history replay; [squawk-decoder](https://github.com/technothatch/squawk-decoder) - squawk/emitter/ICAO decoder
