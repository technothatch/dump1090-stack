"""File-level validation for the dump1090-stack repository.

``make config`` and ``make test`` run this before anything touches Docker:

* ``compose.yaml`` parses and every ``${VAR}`` it uses is documented in
  ``.env.example`` (and vice versa);
* every relative bind mount and every ``build:`` context resolves to a
  file that exists on disk;
* ``prometheus/`` has a scrape config plus exactly the four alert rules,
  and each rule references a metric ``dump1090-exporter`` really emits;
* every Grafana dashboard parses, uses a provisioned datasource and only
  queries known metrics;
* the Alertmanager route points at a receiver that exists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dump1090_stack.metrics import is_known_metric

VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
METRIC_PATTERN = re.compile(r"\bdump1090_[a-z0-9_]+\b")
ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: The alert rules the stack ships, in alphabetical order.
ALERT_NAMES = ("DecoderDown", "HighBlocksDropped", "MessageRateLow", "NoAircraft")


class StackError(Exception):
    """A stack file is missing or cannot be parsed."""


def load_yaml(path: Path) -> Any:
    """Parse a YAML file, raising :class:`StackError` with the path."""
    try:
        import yaml
    except ImportError as exc:
        raise StackError(f"{path}: PyYAML is required (install the 'dev' extra)") from exc
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StackError(f"{path}: cannot read file: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StackError(f"{path}: invalid YAML: {exc}") from exc


def compose_variables(text: str) -> set[str]:
    """Return every ``${VAR}`` name referenced by *text*."""
    return {match.group(1) for match in VAR_PATTERN.finditer(text)}


def resolve(value: str, env: Mapping[str, str]) -> str:
    """Substitute ``${VAR}`` and ``${VAR:-default}`` the way compose does."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in env:
            return env[name]
        default = match.group(2)
        return name if default is None else default

    return VAR_PATTERN.sub(substitute, value)


def documented_env(path: Path) -> dict[str, str]:
    """Parse a dotenv-style file into ``{KEY: value}``, skipping comments."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise StackError(f"{path}: cannot read file: {exc}") from exc
    env: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise StackError(f"{path}: not a KEY=VALUE line: {line!r}")
        name = key.strip()
        if not ENV_KEY_PATTERN.fullmatch(name):
            raise StackError(f"{path}: invalid variable name: {name!r}")
        if name in env:
            raise StackError(f"{path}: duplicate variable: {name}")
        env[name] = value.strip()
    return env


def services(compose: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """Return ``(name, service)`` for every service in a compose mapping."""
    raw = compose.get("services")
    if not isinstance(raw, Mapping):
        raise StackError("compose.yaml: missing 'services' mapping")
    parsed: list[tuple[str, Mapping[str, Any]]] = []
    for name, service in raw.items():
        if not isinstance(service, Mapping):
            raise StackError(f"compose.yaml: service {str(name)!r} is not a mapping")
        parsed.append((str(name), service))
    return parsed


def compose_volumes(compose: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Return ``(service, source, target)`` for every volume entry."""
    entries: list[tuple[str, str, str]] = []
    for name, service in services(compose):
        volumes = service.get("volumes") or []
        if not isinstance(volumes, list):
            raise StackError(f"compose.yaml: service {name!r} needs a 'volumes' list")
        for item in volumes:
            parts = _volume_parts(item)
            if parts is not None:
                entries.append((name, parts[0], parts[1]))
    return entries


def build_contexts(compose: Mapping[str, Any], env: Mapping[str, str]) -> list[tuple[str, Path]]:
    """Return ``(service, path/to/Dockerfile)`` for every service with a build."""
    builds: list[tuple[str, Path]] = []
    for name, service in services(compose):
        build = service.get("build")
        if build is None:
            continue
        if isinstance(build, str):
            context, dockerfile = build, "Dockerfile"
        elif isinstance(build, Mapping):
            context = build.get("context", ".")
            dockerfile = build.get("dockerfile", "Dockerfile")
        else:
            raise StackError(f"compose.yaml: service {name!r} has an invalid 'build'")
        builds.append((name, Path(resolve(str(context), env)) / resolve(str(dockerfile), env)))
    return builds


def alert_rules(path: Path) -> list[Mapping[str, Any]]:
    """Parse a Prometheus rule file into a flat list of rules."""
    data = load_yaml(path)
    if not isinstance(data, Mapping):
        raise StackError(f"{path}: expected a YAML mapping with a 'groups' list")
    groups = data.get("groups")
    if not isinstance(groups, list):
        raise StackError(f"{path}: expected a top-level 'groups' list")
    rules: list[Mapping[str, Any]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            raise StackError(f"{path}: every group must be a mapping")
        group_rules = group.get("rules")
        if not isinstance(group_rules, list):
            raise StackError(f"{path}: every group needs a 'rules' list")
        for rule in group_rules:
            if not isinstance(rule, Mapping):
                raise StackError(f"{path}: every rule must be a mapping")
            rules.append(rule)
    return rules


def metrics_in(expr: str) -> set[str]:
    """Return the ``dump1090_*`` metric names referenced by a PromQL expr."""
    return set(METRIC_PATTERN.findall(expr))


def load_dashboards(directory: Path) -> list[Mapping[str, Any]]:
    """Parse every ``*.json`` Grafana dashboard in *directory*."""
    if not directory.is_dir():
        raise StackError(f"{directory}: dashboard directory is missing")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise StackError(f"{directory}: no dashboard JSON files found")
    dashboards: list[Mapping[str, Any]] = []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StackError(f"{path}: cannot read file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise StackError(f"{path}: invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise StackError(f"{path}: dashboard must be a JSON object")
        dashboards.append(raw)
    return dashboards


def validate_stack(root: Path) -> list[str]:
    """Check every cross-file invariant below *root*; return the problems."""
    try:
        compose = _load_yaml_mapping(root / "compose.yaml")
        env = documented_env(root / ".env.example")
        problems: list[str] = []
        _check_variables(root / "compose.yaml", env, problems)
        _check_binds(root, compose, env, problems)
        _check_builds(root, compose, env, problems)
        _check_prometheus(root, compose, env, problems)
        _check_dashboards(root, problems)
        _check_alertmanager(root, problems)
    except StackError as exc:
        return [str(exc)]
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: print every problem, exit non-zero if there are any."""
    parser = argparse.ArgumentParser(
        prog="dump1090-stack-validate",
        description="Validate the dump1090-stack compose/prometheus/grafana files.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current directory)",
    )
    args = parser.parse_args(argv)
    root: Path = args.root
    problems = validate_stack(root)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print(f"{len(problems)} problem(s) found in {root}", file=sys.stderr)
        return 1
    print(f"OK: {root} (compose, .env.example, alerts, dashboards, receivers)")
    return 0


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    data = load_yaml(path)
    if not isinstance(data, Mapping):
        raise StackError(f"{path}: expected a YAML mapping")
    return data


def _volume_parts(item: Any) -> tuple[str, str] | None:
    """Split one volume entry into ``(source, target)``."""
    if isinstance(item, str):
        parts = item.split(":")
        if len(parts) < 2:
            raise StackError(f"compose.yaml: volume entry without a target: {item!r}")
        return parts[0], parts[1]
    if isinstance(item, Mapping):
        source = item.get("source")
        target = item.get("target")
        if isinstance(source, str) and isinstance(target, str):
            return source, target
        return None
    raise StackError(f"compose.yaml: unsupported volume entry: {item!r}")


def _is_host_path(value: str) -> bool:
    """True for repo-relative bind sources.

    Absolute sources (``/dev/bus/usb``, ``C:\\...``) are checked on the
    target host at ``docker compose up`` time, not in this repository, and
    named volumes have no host path at all.
    """
    return value.startswith(("./", "../"))


def _check_variables(compose_path: Path, env: Mapping[str, str], problems: list[str]) -> None:
    try:
        text = compose_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StackError(f"{compose_path}: cannot read file: {exc}") from exc
    for name in sorted(compose_variables(text) - set(env)):
        problems.append(f".env.example does not document ${{{name}}}")
    for name in sorted(set(env) - compose_variables(text)):
        problems.append(f".env.example documents unused variable {name}")


def _check_binds(
    root: Path, compose: Mapping[str, Any], env: Mapping[str, str], problems: list[str]
) -> None:
    for service, source, _target in compose_volumes(compose):
        resolved = resolve(source, env)
        if not _is_host_path(resolved):
            continue
        if not (root / resolved).exists():
            problems.append(f"service {service}: bind source does not exist: {resolved}")


def _check_builds(
    root: Path, compose: Mapping[str, Any], env: Mapping[str, str], problems: list[str]
) -> None:
    for service, dockerfile in build_contexts(compose, env):
        if dockerfile.is_absolute():
            continue
        if not (root / dockerfile).is_file():
            problems.append(f"service {service}: missing build file {dockerfile}")


def _check_prometheus(
    root: Path, compose: Mapping[str, Any], env: Mapping[str, str], problems: list[str]
) -> None:
    prometheus_path = root / "prometheus" / "prometheus.yml"
    prometheus = _load_yaml_mapping(prometheus_path)
    mounts = {
        resolve(target, env): resolve(source, env)
        for _service, source, target in compose_volumes(compose)
    }

    rule_files = prometheus.get("rule_files")
    if not isinstance(rule_files, list) or not rule_files:
        raise StackError(f"{prometheus_path}: expected a non-empty 'rule_files' list")
    rule_paths: list[Path] = []
    for name in rule_files:
        key = str(name)
        if key not in mounts:
            problems.append(f"prometheus: rule file {key} is not mounted in compose.yaml")
            continue
        rule_paths.append(root / mounts[key])

    _check_scrape_config(prometheus, problems)

    alerts: list[str] = []
    for path in rule_paths:
        for rule in alert_rules(path):
            name = str(rule.get("alert", "<unnamed>"))
            alerts.append(name)
            expr = rule.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                problems.append(f"{path}: rule {name} has no expr")
                continue
            if "for" not in rule:
                problems.append(f"{path}: rule {name} has no 'for' duration")
            labels = rule.get("labels")
            if not isinstance(labels, Mapping) or "severity" not in labels:
                problems.append(f"{path}: rule {name} has no severity label")
            for metric in sorted(metrics_in(expr)):
                if not is_known_metric(metric):
                    problems.append(f"{path}: rule {name} references unknown metric {metric}")
    if sorted(alerts) != list(ALERT_NAMES):
        problems.append(
            f"prometheus: expected alerts {', '.join(ALERT_NAMES)}; found "
            f"{', '.join(sorted(alerts)) or 'none'}"
        )


def _check_scrape_config(prometheus: Mapping[str, Any], problems: list[str]) -> None:
    scrape_configs = prometheus.get("scrape_configs")
    if not isinstance(scrape_configs, list):
        raise StackError("prometheus/prometheus.yml: 'scrape_configs' must be a list")
    targets = {
        target
        for config in scrape_configs
        if isinstance(config, Mapping)
        for target in _static_targets(config)
    }
    if not any(target.startswith("dump1090-exporter:") for target in targets):
        problems.append("prometheus: no scrape target for the dump1090-exporter service")

    am_targets: set[str] = set()
    alerting = prometheus.get("alerting")
    if isinstance(alerting, Mapping):
        managers = alerting.get("alertmanagers")
        if isinstance(managers, list):
            for manager in managers:
                if isinstance(manager, Mapping):
                    am_targets.update(_static_targets(manager))
    if not any(target.startswith("alertmanager:") for target in am_targets):
        problems.append("prometheus: alerting does not target the alertmanager service")


def _static_targets(config: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    static = config.get("static_configs")
    if not isinstance(static, list):
        return found
    for entry in static:
        if not isinstance(entry, Mapping):
            continue
        targets = entry.get("targets")
        if isinstance(targets, list):
            found.extend(str(target) for target in targets)
    return found


def _check_dashboards(root: Path, problems: list[str]) -> None:
    dashboards = load_dashboards(root / "grafana" / "dashboards")
    datasource = _load_yaml_mapping(
        root / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    )
    uids = _datasource_uids(datasource)
    if not uids:
        raise StackError("grafana: no provisioned datasource uid found")

    for dashboard in dashboards:
        uid = str(dashboard.get("uid", "")) or "<no uid>"
        if not dashboard.get("uid"):
            problems.append("grafana: dashboard without a uid")
        panels = dashboard.get("panels")
        if not isinstance(panels, list) or not panels:
            problems.append(f"grafana: dashboard {uid} has no panels")
            continue
        for panel in panels:
            if not isinstance(panel, Mapping):
                problems.append(f"grafana: dashboard {uid} has a non-mapping panel")
                continue
            _check_panel(uid, panel, uids, problems)


def _datasource_uids(datasource: Mapping[str, Any]) -> set[str]:
    sources = datasource.get("datasources")
    if not isinstance(sources, list):
        raise StackError("grafana: datasource provisioning needs a 'datasources' list")
    uids = {
        str(source.get("uid"))
        for source in sources
        if isinstance(source, Mapping) and source.get("uid")
    }
    return uids


def _check_panel(
    uid: str,
    panel: Mapping[str, Any],
    uids: set[str],
    problems: list[str],
) -> None:
    datasource = panel.get("datasource")
    if isinstance(datasource, Mapping):
        panel_uid = str(datasource.get("uid", ""))
        if panel_uid and panel_uid not in uids:
            problems.append(f"grafana: dashboard {uid} uses unknown datasource {panel_uid}")
    targets = panel.get("targets")
    if not isinstance(targets, list):
        return
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        expr = target.get("expr")
        if not isinstance(expr, str):
            continue
        for metric in sorted(metrics_in(expr)):
            if not is_known_metric(metric):
                problems.append(f"grafana: dashboard {uid} queries unknown metric {metric}")


def _check_alertmanager(root: Path, problems: list[str]) -> None:
    path = root / "alertmanager" / "config.yml"
    config = _load_yaml_mapping(path)
    receivers = config.get("receivers")
    if not isinstance(receivers, list) or not receivers:
        raise StackError(f"{path}: expected a non-empty 'receivers' list")

    names: list[str] = []
    webhook_receivers: list[str] = []
    for receiver in receivers:
        if not isinstance(receiver, Mapping):
            raise StackError(f"{path}: every receiver must be a mapping")
        name = str(receiver.get("name", ""))
        if not name:
            raise StackError(f"{path}: receiver without a name")
        names.append(name)
        if isinstance(receiver.get("webhook_configs"), list):
            webhook_receivers.append(name)
    if len(names) != len(set(names)):
        raise StackError(f"{path}: duplicate receiver name")
    if not webhook_receivers:
        problems.append(f"{path}: no receiver defines webhook_configs")

    route = config.get("route")
    if not isinstance(route, Mapping):
        raise StackError(f"{path}: 'route' must be a mapping")
    routed = [route.get("receiver")]
    subroutes = route.get("routes")
    if isinstance(subroutes, list):
        routed.extend(
            subroute.get("receiver") for subroute in subroutes if isinstance(subroute, Mapping)
        )
    for target in routed:
        if str(target) not in names:
            problems.append(f"{path}: route receiver {str(target)!r} is not configured")
