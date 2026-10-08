"""Metric names emitted by dump1090-exporter.

Verified against ``dump1090-exporter/internal/exporter/exporter.go``: the
``Name*`` constants (lines 30-56) plus the literal names passed to
``prometheus.NewGaugeVec`` further down (``dump1090_cpr_*``,
``dump1090_tracks_*``). Prometheus alert rules in ``prometheus/alerts.yml``
and every Grafana panel query may only reference names from this module;
``tests/test_stack.py`` fails the build if the two ever drift apart.

The exporter registers a Prometheus client_golang collector per name, and
client_golang does not rewrite counter names, so the exposed series are
exactly these strings.
"""

from __future__ import annotations

EXPORTER_METRICS: frozenset[str] = frozenset(
    {
        "dump1090_up",
        "dump1090_receiver_refresh_seconds",
        "dump1090_receiver_info",
        "dump1090_messages_total",
        "dump1090_messages_per_second",
        "dump1090_aircraft",
        "dump1090_aircraft_with_position",
        "dump1090_signal_dbfs",
        "dump1090_blocks_dropped_total",
        "dump1090_cpu_milliseconds",
        "dump1090_cpr_local_ok",
        "dump1090_cpr_local_bad",
        "dump1090_cpr_local_skipped",
        "dump1090_cpr_global_ok",
        "dump1090_cpr_global_bad",
        "dump1090_cpr_global_skipped",
        "dump1090_tracks_all",
        "dump1090_tracks_single_message",
        "dump1090_scrape_duration_seconds",
        "dump1090_aircraft_altitude",
        "dump1090_aircraft_squawk",
    }
)

#: Label dimensions carried by each labelled collector.
METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "dump1090_aircraft_squawk": ("squawk",),
    "dump1090_cpu_milliseconds": ("stage",),
    "dump1090_receiver_info": ("version",),
    "dump1090_signal_dbfs": ("quantile",),
}

#: Histogram collector; Prometheus exposes it as _bucket/_sum/_count series.
HISTOGRAM_METRICS: frozenset[str] = frozenset({"dump1090_aircraft_altitude"})

_DERIVED_SUFFIXES = ("_bucket", "_sum", "_count")


def is_known_metric(name: str) -> bool:
    """Return True if *name* is a series dump1090-exporter can expose.

    Accepts collector names as well as the series a histogram expands to
    (``dump1090_aircraft_altitude_bucket`` and friends).
    """
    if name in EXPORTER_METRICS:
        return True
    for suffix in _DERIVED_SUFFIXES:
        if name.endswith(suffix) and name[: len(name) - len(suffix)] in EXPORTER_METRICS:
            return True
    return False
