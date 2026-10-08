"""Unit tests for dump1090_stack.validate built around a miniature stack."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from dump1090_stack.validate import (
    StackError,
    alert_rules,
    build_contexts,
    compose_variables,
    compose_volumes,
    documented_env,
    load_dashboards,
    load_yaml,
    main,
    metrics_in,
    resolve,
    services,
    validate_stack,
)

COMPOSE = """\
name: mini
services:
  readsb:
    image: ghcr.io/wiedehopf/readsb:latest
    network_mode: host
    volumes:
      - ./data/readsb:/run/readsb
  prometheus:
    image: prom/prometheus:v3.15.0
    environment:
      TZ: ${TZ:-UTC}
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro
"""

PROMETHEUS = """\
global:
  scrape_interval: 15s
rule_files:
  - /etc/prometheus/alerts.yml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093
scrape_configs:
  - job_name: dump1090-exporter
    static_configs:
      - targets:
          - dump1090-exporter:9702
"""

ALERTS = """\
groups:
  - name: mini
    rules:
      - alert: DecoderDown
        expr: dump1090_up == 0
        for: 2m
        labels:
          severity: critical
      - alert: MessageRateLow
        expr: dump1090_messages_per_second < 1
        for: 15m
        labels:
          severity: warning
      - alert: HighBlocksDropped
        expr: increase(dump1090_blocks_dropped_total[15m]) > 100
        for: 5m
        labels:
          severity: warning
      - alert: NoAircraft
        expr: dump1090_aircraft == 0
        for: 30m
        labels:
          severity: warning
"""

DATASOURCE = """\
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
"""

DASHBOARD = """\
{
  "uid": "mini",
  "panels": [
    {
      "id": 1,
      "title": "Up",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "targets": [
        {
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "expr": "dump1090_up",
          "refId": "A"
        }
      ]
    }
  ]
}
"""

ALERTMANAGER = """\
route:
  receiver: webhook
receivers:
  - name: webhook
    webhook_configs:
      - url: http://127.0.0.1:9099/alerts
        send_resolved: true
"""


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build_stack(root: Path) -> Path:
    _write(root, "compose.yaml", COMPOSE)
    _write(root, ".env.example", "TZ=UTC\n")
    (root / "data" / "readsb").mkdir(parents=True)
    _write(root, "prometheus/prometheus.yml", PROMETHEUS)
    _write(root, "prometheus/alerts.yml", ALERTS)
    _write(root, "grafana/provisioning/datasources/prometheus.yml", DATASOURCE)
    _write(root, "grafana/dashboards/mini.json", DASHBOARD)
    _write(root, "alertmanager/config.yml", ALERTMANAGER)
    return root


@pytest.fixture()
def stack(tmp_path: Path) -> Path:
    return _build_stack(tmp_path)


def test_mini_stack_is_clean(stack: Path) -> None:
    assert validate_stack(stack) == []


def test_missing_bind_source_is_reported(stack: Path) -> None:
    shutil.rmtree(stack / "data" / "readsb")
    problems = validate_stack(stack)
    assert any("bind source does not exist: ./data/readsb" in item for item in problems)


def test_undocumented_variable_is_reported(stack: Path) -> None:
    compose = (stack / "compose.yaml").read_text(encoding="utf-8")
    _write(stack, "compose.yaml", compose.replace("${TZ:-UTC}", "${SNEAKY_VAR:-x}", 1))
    problems = validate_stack(stack)
    assert any("does not document ${SNEAKY_VAR}" in item for item in problems)


def test_unused_documented_variable_is_reported(stack: Path) -> None:
    with (stack / ".env.example").open("a", encoding="utf-8") as handle:
        handle.write("UNUSED_THING=1\n")
    problems = validate_stack(stack)
    assert any("unused variable UNUSED_THING" in item for item in problems)


def test_broken_compose_yaml_is_a_single_problem(stack: Path) -> None:
    _write(stack, "compose.yaml", "services: [\n")
    problems = validate_stack(stack)
    assert len(problems) == 1
    assert "invalid YAML" in problems[0]


def test_missing_prometheus_config_is_a_single_problem(stack: Path) -> None:
    (stack / "prometheus/prometheus.yml").unlink()
    problems = validate_stack(stack)
    assert len(problems) == 1
    assert "cannot read file" in problems[0]


def test_unknown_alert_metric_is_reported(stack: Path) -> None:
    alerts = (stack / "prometheus/alerts.yml").read_text(encoding="utf-8")
    _write(
        stack,
        "prometheus/alerts.yml",
        alerts.replace("dump1090_aircraft ==", "dump1090_aircraft_flying =="),
    )
    problems = validate_stack(stack)
    assert any("unknown metric dump1090_aircraft_flying" in item for item in problems)


def test_missing_dashboard_file_is_reported(stack: Path) -> None:
    (stack / "grafana/dashboards/mini.json").unlink()
    problems = validate_stack(stack)
    assert any("no dashboard JSON files" in item for item in problems)


def test_unknown_route_receiver_is_reported(stack: Path) -> None:
    config = (stack / "alertmanager/config.yml").read_text(encoding="utf-8")
    _write(
        stack, "alertmanager/config.yml", config.replace("receiver: webhook", "receiver: missing")
    )
    problems = validate_stack(stack)
    assert any("is not configured" in item for item in problems)


def test_unknown_subroute_receiver_is_reported(stack: Path) -> None:
    config = (stack / "alertmanager/config.yml").read_text(encoding="utf-8")
    _write(
        stack,
        "alertmanager/config.yml",
        config.replace("receivers:", "  routes:\n    - receiver: ghost\nreceivers:"),
    )
    problems = validate_stack(stack)
    assert any("'ghost'" in item and "not configured" in item for item in problems)


def test_unmounted_rule_file_is_reported(stack: Path) -> None:
    prometheus = (stack / "prometheus/prometheus.yml").read_text(encoding="utf-8")
    _write(
        stack,
        "prometheus/prometheus.yml",
        prometheus.replace("/etc/prometheus/alerts.yml", "/etc/prometheus/nope.yml"),
    )
    problems = validate_stack(stack)
    assert any("not mounted" in item for item in problems)


def test_missing_build_file_is_reported(stack: Path) -> None:
    compose = (stack / "compose.yaml").read_text(encoding="utf-8")
    _write(stack, "compose.yaml", compose + "  builder:\n    build: ./missing\n")
    problems = validate_stack(stack)
    assert any("missing build file" in item for item in problems)


def test_scrape_config_without_exporter_target_is_reported(stack: Path) -> None:
    _write(
        stack,
        "prometheus/prometheus.yml",
        PROMETHEUS.replace("dump1090-exporter:9702", "other:9100"),
    )
    problems = validate_stack(stack)
    assert any("no scrape target" in item for item in problems)


def test_alerting_without_alertmanager_target_is_reported(stack: Path) -> None:
    _write(
        stack,
        "prometheus/prometheus.yml",
        PROMETHEUS.replace("alertmanager:9093", "somewhere:9093"),
    )
    problems = validate_stack(stack)
    assert any("alerting does not target" in item for item in problems)


def test_rule_missing_expr_for_or_severity_is_reported(stack: Path) -> None:
    _write(
        stack,
        "prometheus/alerts.yml",
        """
groups:
  - name: broken
    rules:
      - alert: DecoderDown
        expr: dump1090_up == 0
      - alert: NoAircraft
        for: 5m
        labels:
          severity: warning
""",
    )
    problems = validate_stack(stack)
    assert any("has no expr" in item for item in problems)
    assert any("has no 'for' duration" in item for item in problems)
    assert any("no severity label" in item for item in problems)
    assert any("expected alerts" in item for item in problems)


def test_receiver_without_webhook_configs_is_reported(stack: Path) -> None:
    _write(
        stack, "alertmanager/config.yml", "route:\n  receiver: plain\nreceivers:\n  - name: plain\n"
    )
    problems = validate_stack(stack)
    assert any("webhook_configs" in item for item in problems)


def test_alertmanager_rejects_bad_shapes(stack: Path) -> None:
    _write(stack, "alertmanager/config.yml", "route:\n  receiver: a\nreceivers: nope\n")
    assert any("receivers" in item for item in validate_stack(stack))

    _write(stack, "alertmanager/config.yml", "route:\n  receiver: a\nreceivers: []\n")
    assert any("non-empty 'receivers'" in item for item in validate_stack(stack))

    _write(
        stack,
        "alertmanager/config.yml",
        "route:\n  receiver: a\nreceivers:\n  - webhook_configs: []\n",
    )
    assert any("without a name" in item for item in validate_stack(stack))

    _write(
        stack,
        "alertmanager/config.yml",
        "route:\n  receiver: a\nreceivers:\n  - name: a\n  - name: a\n",
    )
    assert any("duplicate receiver" in item for item in validate_stack(stack))

    _write(stack, "alertmanager/config.yml", "route: nope\nreceivers:\n  - name: a\n")
    assert any("route" in item and "mapping" in item for item in validate_stack(stack))


def test_dashboard_shape_problems(stack: Path) -> None:
    _write(stack, "grafana/dashboards/mini.json", '{"panels": []}')
    problems = validate_stack(stack)
    assert any("without a uid" in item for item in problems)
    assert any("has no panels" in item for item in problems)

    _write(stack, "grafana/dashboards/mini.json", '{"uid": "x", "panels": ["nope"]}')
    problems = validate_stack(stack)
    assert any("non-mapping panel" in item for item in problems)

    _write(stack, "grafana/dashboards/mini.json", '{"uid": "x", "panels": [{"targets": "x"}]}')
    assert validate_stack(stack) == []


def test_dashboard_with_unknown_datasource_is_reported(stack: Path) -> None:
    _write(
        stack,
        "grafana/dashboards/mini.json",
        DASHBOARD.replace('"uid": "prometheus"', '"uid": "grafana"'),
    )
    problems = validate_stack(stack)
    assert any("unknown datasource grafana" in item for item in problems)


def test_datasource_provisioning_without_uids_is_an_error(stack: Path) -> None:
    _write(stack, "grafana/provisioning/datasources/prometheus.yml", "apiVersion: 1\n")
    problems = validate_stack(stack)
    assert len(problems) == 1
    assert "datasources" in problems[0]

    _write(
        stack,
        "grafana/provisioning/datasources/prometheus.yml",
        DATASOURCE.replace("uid: prometheus\n", ""),
    )
    problems = validate_stack(stack)
    assert len(problems) == 1
    assert "datasource uid" in problems[0]


def test_main_reports_success(stack: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(stack)]) == 0
    assert "OK:" in capsys.readouterr().out


def test_main_reports_failure(stack: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.rmtree(stack / "data" / "readsb")
    assert main(["--root", str(stack)]) == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "problem(s) found" in captured.err


def test_resolve_prefers_env_then_default_then_name() -> None:
    assert resolve("${A:-b}", {}) == "b"
    assert resolve("${A:-b}", {"A": "c"}) == "c"
    assert resolve("${A}", {}) == "A"
    assert resolve("${A:-}", {}) == ""
    assert resolve("plain", {}) == "plain"


def test_compose_variables_returns_unique_names() -> None:
    assert compose_variables("${A:-1} ${A:-1} ${B} $C") == {"A", "B"}
    assert compose_variables("no variables here") == set()


def test_metrics_in_expr_extracts_only_dump1090_series() -> None:
    assert metrics_in("rate(dump1090_messages_total[5m])") == {"dump1090_messages_total"}
    assert metrics_in("sum by (stage) (dump1090_cpu_milliseconds)") == {"dump1090_cpu_milliseconds"}
    assert metrics_in("up == 0") == set()


def test_documented_env_parses_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# comment\n\nA=1\nB=\nC=x=y\n", encoding="utf-8")
    assert documented_env(path) == {"A": "1", "B": "", "C": "x=y"}


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("not a key value line\n", "KEY=VALUE"),
        ("A=1\nA=2\n", "duplicate variable"),
        ("1BAD=x\n", "invalid variable name"),
    ],
)
def test_documented_env_rejects_bad_files(tmp_path: Path, text: str, match: str) -> None:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(StackError, match=match):
        documented_env(path)


def test_documented_env_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StackError, match="cannot read"):
        documented_env(tmp_path / "absent.env")


def test_load_yaml_missing_file(tmp_path: Path) -> None:
    with pytest.raises(StackError, match="cannot read"):
        load_yaml(tmp_path / "absent.yml")


def test_load_yaml_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("a: [1\n", encoding="utf-8")
    with pytest.raises(StackError, match="invalid YAML"):
        load_yaml(path)


def test_load_yaml_without_pyyaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)
    path = tmp_path / "any.yml"
    path.write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(StackError, match="PyYAML"):
        load_yaml(path)


def test_alert_rules_requires_a_groups_list(tmp_path: Path) -> None:
    path = tmp_path / "rules.yml"
    path.write_text("foo: bar\n", encoding="utf-8")
    with pytest.raises(StackError, match="groups"):
        alert_rules(path)


def test_alert_rules_requires_a_top_level_mapping(tmp_path: Path) -> None:
    path = tmp_path / "rules.yml"
    path.write_text("- a\n", encoding="utf-8")
    with pytest.raises(StackError, match="YAML mapping"):
        alert_rules(path)


def test_alert_rules_rejects_malformed_groups(tmp_path: Path) -> None:
    path = tmp_path / "rules.yml"
    path.write_text("groups: nope\n", encoding="utf-8")
    with pytest.raises(StackError, match="groups"):
        alert_rules(path)

    path.write_text("groups:\n  - nope\n", encoding="utf-8")
    with pytest.raises(StackError, match="mapping"):
        alert_rules(path)

    path.write_text("groups:\n  - name: g\n", encoding="utf-8")
    with pytest.raises(StackError, match="rules"):
        alert_rules(path)

    path.write_text("groups:\n  - name: g\n    rules:\n      - nope\n", encoding="utf-8")
    with pytest.raises(StackError, match="mapping"):
        alert_rules(path)


def test_load_dashboards_reports_file_problems(tmp_path: Path) -> None:
    with pytest.raises(StackError, match="missing"):
        load_dashboards(tmp_path / "absent")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(StackError, match="no dashboard"):
        load_dashboards(empty)

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StackError, match="invalid JSON"):
        load_dashboards(broken)

    (broken / "x.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(StackError, match="JSON object"):
        load_dashboards(broken)

    (broken / "x.json").unlink()
    (broken / "x.json").mkdir()
    with pytest.raises(StackError, match="cannot read"):
        load_dashboards(broken)


def test_services_rejects_bad_shapes() -> None:
    with pytest.raises(StackError, match="services"):
        services({})
    with pytest.raises(StackError, match="mapping"):
        services({"services": {"a": "b"}})
    assert services({"services": {"a": {"image": "x"}}}) == [("a", {"image": "x"})]


def test_compose_volumes_handles_both_syntaxes() -> None:
    compose = {
        "services": {
            "a": {
                "volumes": [
                    "./x:/y:ro",
                    {"type": "bind", "source": "./p", "target": "/q"},
                ]
            },
            "b": {"volumes": [{"type": "volume", "source": "named", "target": "/z"}]},
        }
    }
    assert compose_volumes(compose) == [
        ("a", "./x", "/y"),
        ("a", "./p", "/q"),
        ("b", "named", "/z"),
    ]


def test_compose_volumes_rejects_bad_entries() -> None:
    with pytest.raises(StackError, match="without a target"):
        compose_volumes({"services": {"a": {"volumes": ["justname"]}}})
    with pytest.raises(StackError, match="unsupported"):
        compose_volumes({"services": {"a": {"volumes": [42]}}})
    assert compose_volumes({"services": {"a": {"volumes": [{"type": "tmpfs"}]}}}) == []
    with pytest.raises(StackError, match="volumes"):
        compose_volumes({"services": {"a": {"volumes": "nope"}}})


def test_build_contexts_accepts_string_and_mapping_forms() -> None:
    compose = {
        "services": {
            "a": {"build": "./a"},
            "b": {"build": {"context": "./b", "dockerfile": "Customfile"}},
            "c": {"image": "scratch"},
        }
    }
    assert build_contexts(compose, {}) == [
        ("a", Path("./a") / "Dockerfile"),
        ("b", Path("./b") / "Customfile"),
    ]
    with pytest.raises(StackError, match="invalid 'build'"):
        build_contexts({"services": {"a": {"build": 42}}}, {})
