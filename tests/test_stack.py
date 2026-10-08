"""Structural tests for the dump1090-stack Compose deployment.

Stdlib + PyYAML only - the stack itself ships no runtime dependencies.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

from dump1090_stack.metrics import (
    EXPORTER_METRICS,
    HISTOGRAM_METRICS,
    METRIC_LABELS,
    is_known_metric,
)
from dump1090_stack.validate import (
    ALERT_NAMES,
    alert_rules,
    build_contexts,
    compose_variables,
    compose_volumes,
    documented_env,
    load_dashboards,
    metrics_in,
    resolve,
    validate_stack,
)

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SERVICES = (
    "readsb",
    "tar1090",
    "dump1090-exporter",
    "prometheus",
    "grafana",
    "alertmanager",
    "recorder",
)

REQUIRED_DASHBOARD_TOPICS = ("message rate", "aircraft", "signal", "cpu")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _compose() -> dict[str, Any]:
    data = yaml.safe_load(_read("compose.yaml"))
    assert isinstance(data, dict)
    return data


def _services() -> dict[str, Any]:
    services = _compose()["services"]
    assert isinstance(services, dict)
    return services


def _alerts() -> list[dict[str, Any]]:
    return [dict(rule) for rule in alert_rules(ROOT / "prometheus" / "alerts.yml")]


def test_compose_parses_and_declares_every_service() -> None:
    services = _services()
    assert tuple(services) == REQUIRED_SERVICES


def test_readsb_uses_wiedehopf_image_and_host_networking() -> None:
    readsb = _services()["readsb"]
    assert readsb["network_mode"] == "host"
    assert readsb["image"] == "${READSB_IMAGE:-ghcr.io/wiedehopf/readsb:latest}"
    assert "privileged" in readsb
    command = readsb["command"]
    assert "--net" in command
    assert "--write-json=/run/readsb" in command
    assert "--net-bo-port=30005" in command


def test_recorder_runs_under_the_recorder_profile() -> None:
    recorder = _services()["recorder"]
    assert recorder["profiles"] == ["recorder"]
    assert recorder["container_name"] == "dump1090-recorder"
    assert any("recordings" in str(volume) for volume in recorder["volumes"])


def test_env_example_documents_exactly_the_compose_variables() -> None:
    used = compose_variables(_read("compose.yaml"))
    documented = set(documented_env(ROOT / ".env.example"))
    assert used == documented
    assert "READSB_ARGS" in used
    assert "GRAFANA_ADMIN_PASSWORD" in used


def test_every_relative_bind_source_exists_on_disk() -> None:
    problems = [problem for problem in validate_stack(ROOT) if "bind source" in problem]
    assert problems == []


def test_every_build_context_ships_a_dockerfile() -> None:
    compose = _compose()
    documented = documented_env(ROOT / ".env.example")
    builds = dict(build_contexts(compose, documented))
    assert set(builds) == {"tar1090", "dump1090-exporter", "recorder"}
    for service, dockerfile in builds.items():
        assert (ROOT / dockerfile).is_file(), service


def test_prometheus_scrapes_the_exporter_and_notifies_alertmanager() -> None:
    prometheus = yaml.safe_load(_read("prometheus/prometheus.yml"))
    targets = {
        str(target)
        for config in prometheus["scrape_configs"]
        for entry in config["static_configs"]
        for target in entry["targets"]
    }
    assert "dump1090-exporter:9702" in targets
    managers = prometheus["alerting"]["alertmanagers"]
    am_targets = {
        str(target) for entry in managers for target in entry["static_configs"][0]["targets"]
    }
    assert "alertmanager:9093" in am_targets


def test_prometheus_rule_files_are_mounted_by_compose() -> None:
    prometheus = yaml.safe_load(_read("prometheus/prometheus.yml"))
    env = documented_env(ROOT / ".env.example")
    mounts = {
        resolve(target, env): resolve(source, env)
        for _service, source, target in compose_volumes(_compose())
    }
    for rule_file in prometheus["rule_files"]:
        assert rule_file in mounts, rule_file
        source = mounts[rule_file]
        assert source.startswith("./")
        assert (ROOT / source).is_file()


def test_exactly_four_alert_rules_with_the_expected_names() -> None:
    alerts = _alerts()
    assert len(alerts) == 4
    assert sorted(str(rule["alert"]) for rule in alerts) == sorted(ALERT_NAMES)


def test_alert_rules_reference_only_real_exporter_metrics() -> None:
    for rule in _alerts():
        metrics = metrics_in(str(rule["expr"]))
        assert metrics, rule["alert"]
        for metric in metrics:
            assert is_known_metric(metric), f"{rule['alert']}: {metric}"


def test_alert_rules_carry_a_for_duration_and_severity() -> None:
    for rule in _alerts():
        assert rule.get("for"), rule["alert"]
        assert rule["labels"]["severity"] in {"critical", "warning"}
        assert rule["annotations"]["summary"]
        assert rule["annotations"]["description"]


def test_grafana_dashboards_parse_and_have_panels() -> None:
    dashboards = load_dashboards(ROOT / "grafana" / "dashboards")
    assert dashboards
    for dashboard in dashboards:
        assert dashboard["uid"]
        assert dashboard["panels"]


def test_dashboard_panels_cover_message_rate_aircraft_signal_and_cpu() -> None:
    dashboards = load_dashboards(ROOT / "grafana" / "dashboards")
    titles = " ".join(
        str(panel["title"]).lower() for dash in dashboards for panel in dash["panels"]
    )
    for topic in REQUIRED_DASHBOARD_TOPICS:
        assert topic in titles, topic


def test_dashboard_queries_only_metrics_the_exporter_emits() -> None:
    for dashboard in load_dashboards(ROOT / "grafana" / "dashboards"):
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                for metric in metrics_in(str(target.get("expr", ""))):
                    assert is_known_metric(metric), f"{panel['title']}: {metric}"


def test_dashboard_datasource_is_the_provisioned_one() -> None:
    datasource = yaml.safe_load(_read("grafana/provisioning/datasources/prometheus.yml"))
    uids = {str(entry["uid"]) for entry in datasource["datasources"]}
    assert uids == {"prometheus"}
    for dashboard in load_dashboards(ROOT / "grafana" / "dashboards"):
        for panel in dashboard["panels"]:
            assert panel["datasource"]["uid"] in uids


def test_dashboard_provider_path_matches_the_compose_mount() -> None:
    provider = yaml.safe_load(_read("grafana/provisioning/dashboards/dump1090.yml"))
    path = provider["providers"][0]["options"]["path"]
    volumes = _services()["grafana"]["volumes"]
    assert any(
        str(volume).endswith(f":{path}:ro") or str(volume).endswith(f":{path}")
        for volume in volumes
    )


def test_alertmanager_ships_a_webhook_receiver_referenced_by_the_route() -> None:
    config = yaml.safe_load(_read("alertmanager/config.yml"))
    receivers = {entry["name"] for entry in config["receivers"]}
    webhooked = {entry["name"] for entry in config["receivers"] if entry.get("webhook_configs")}
    assert webhooked, "at least one receiver must define webhook_configs"
    assert config["route"]["receiver"] in receivers & webhooked
    for subroute in config["route"].get("routes", []):
        assert subroute["receiver"] in receivers
        assert "severity" in str(subroute["matchers"])


def test_tar1090_nginx_serves_html_and_data() -> None:
    nginx = _read("tar1090/nginx.conf")
    assert "location /data/" in nginx
    assert "alias /data/;" in nginx
    assert "root /usr/share/nginx/html;" in nginx
    assert (ROOT / "tar1090" / "Dockerfile").is_file()


def test_recorder_script_is_a_posix_shell_script() -> None:
    script = _read("recorder/record.sh")
    assert script.startswith("#!/bin/sh")
    assert "\r" not in script, "record.sh must stay LF for the Linux container"
    assert "BEAST_HOST" in script
    assert (ROOT / "recorder" / "Dockerfile").is_file()


def test_makefile_exposes_the_documented_targets() -> None:
    makefile = _read("Makefile")
    targets = {
        line.split(":", 1)[0]
        for line in makefile.splitlines()
        if line and not line.startswith(("\t", " ", "#")) and ":" in line
    }
    assert {"up", "down", "logs", "config", "test"} <= targets
    assert "\tdocker compose" in makefile
    assert "--cov-fail-under=80" in makefile


def test_readme_documents_usb_passthrough_net_only_and_recorder() -> None:
    readme = _read("README.md")
    assert "/dev/bus/usb" in readme
    assert "devices:" in readme
    assert "privileged" in readme
    assert "--net-only" in readme
    assert "--profile recorder" in readme
    assert "technothatch/dump1090-stack" in readme


def test_pyproject_declares_the_spec_tooling() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert project["project"]["requires-python"] == ">=3.11"
    dev = project["project"]["optional-dependencies"]["dev"]
    for package in ("pytest", "pytest-cov", "ruff", "mypy", "pyyaml"):
        assert any(entry.startswith(package) for entry in dev), package
    assert project["project"]["dependencies"] == []
    select = project["tool"]["ruff"]["lint"]["select"]
    assert select == ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "SIM", "RUF"]
    assert project["tool"]["mypy"]["strict"] is True
    assert project["tool"]["coverage"]["run"]["source"] == ["dump1090_stack"]


def test_metric_inventory_matches_the_exporter_documentation() -> None:
    assert HISTOGRAM_METRICS <= EXPORTER_METRICS
    assert set(METRIC_LABELS) <= EXPORTER_METRICS
    assert METRIC_LABELS["dump1090_signal_dbfs"] == ("quantile",)
    assert is_known_metric("dump1090_aircraft_altitude_bucket")
    assert is_known_metric("dump1090_up")
    assert not is_known_metric("dump1090_does_not_exist")
    assert not is_known_metric("up")


def test_validate_stack_reports_no_problems_for_this_repository() -> None:
    assert validate_stack(ROOT) == []
