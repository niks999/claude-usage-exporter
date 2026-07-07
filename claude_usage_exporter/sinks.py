"""Sinks: emit cumulative token counters to a destination.

This is the SINK seam. Each sink implements ``emit(metric_name, samples)`` where
``samples`` is an iterable of ``(labels: dict, value: int)``. Adding a destination
(Datadog, StatsD, remote-write, ...) is a ~20-line class, not a plugin manifest.

Three sinks ship:
* StdoutSink            -- human text or JSON; zero dependencies; for debugging / piping.
* PrometheusTextfileSink-- node_exporter textfile-collector format; zero dependencies.
* OtlpSink              -- OTLP/HTTP push; needs the ``[otlp]`` extra. Proven in prod.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterable

Sample = tuple[dict, int]

_DEFAULT_HELP = "Cumulative Claude Code token usage parsed from agent transcripts."


def _sorted(samples: Iterable[Sample]) -> list[Sample]:
    return sorted(samples, key=lambda s: json.dumps(s[0], sort_keys=True))


def _fmt_labels_prom(labels: dict) -> str:
    """Render labels in Prometheus exposition syntax, escaping value specials."""

    def esc(v: object) -> str:
        return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    inner = ",".join(f'{k}="{esc(v)}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}" if inner else ""


class StdoutSink:
    """Print series to a stream as aligned exposition lines (``text``) or ``json``."""

    def __init__(self, fmt: str = "text", stream=None) -> None:
        self.fmt = fmt
        self.stream = stream if stream is not None else sys.stdout

    def emit(self, metric_name: str, samples: Iterable[Sample]) -> None:
        samples = _sorted(samples)
        if self.fmt == "json":
            payload = {"metric": metric_name, "series": [{"labels": lbl, "value": v} for lbl, v in samples]}
            json.dump(payload, self.stream, indent=2, sort_keys=True)
            self.stream.write("\n")
        else:
            for labels, value in samples:
                self.stream.write(f"{metric_name}{_fmt_labels_prom(labels)} {value}\n")


class PrometheusTextfileSink:
    """Write the node_exporter textfile-collector format, atomically.

    A separately-running node_exporter started with
    ``--collector.textfile.directory=<dir>`` scrapes the file on its next cycle;
    nothing here needs a live Prometheus. The write is atomic (tmp + rename) so the
    collector never reads a half-written file.
    """

    def __init__(self, path: str, help_text: str = _DEFAULT_HELP) -> None:
        self.path = path
        self.help_text = help_text

    def emit(self, metric_name: str, samples: Iterable[Sample]) -> None:
        lines = [f"# HELP {metric_name} {self.help_text}", f"# TYPE {metric_name} counter"]
        for labels, value in _sorted(samples):
            lines.append(f"{metric_name}{_fmt_labels_prom(labels)} {value}")
        text = "\n".join(lines) + "\n"

        target = Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as fh:
            fh.write(text)
        os.replace(tmp, self.path)  # atomic


def build_observations(samples: Iterable[Sample]):
    """Map samples to OTLP Observations. Split out so it is unit-testable offline."""
    from opentelemetry.metrics import Observation

    return [Observation(value, dict(labels)) for labels, value in samples]


class OtlpSink:
    """Push cumulative totals as an OTLP ObservableCounter (CUMULATIVE temporality).

    Requires the ``[otlp]`` extra (opentelemetry-sdk + otlp-proto-http exporter).
    Emitting the absolute running totals yields a monotonic series in the backend,
    so rate()/increase() work across invocations and a state reset reads as a
    counter reset. Auth (if any) comes from ``auth`` or ``$OTLP_AUTH`` and is sent
    verbatim as the ``Authorization`` header (e.g. ``Basic <token>`` for Grafana Cloud).
    """

    def __init__(
        self,
        endpoint: str,
        auth: str | None = None,
        service: str = "claude-usage-exporter",
        timeout_millis: int = 30_000,
    ) -> None:
        self.endpoint = endpoint
        self.auth = auth if auth is not None else os.environ.get("OTLP_AUTH")
        self.service = service
        self.timeout_millis = timeout_millis

    def emit(self, metric_name: str, samples: Iterable[Sample]) -> None:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:
            raise SystemExit("OTLP sink needs the 'otlp' extra: pip install 'claude-usage-exporter[otlp]'") from exc

        observations = build_observations(list(samples))
        headers = {"Authorization": self.auth} if self.auth else {}
        exporter = OTLPMetricExporter(endpoint=self.endpoint.rstrip("/") + "/v1/metrics", headers=headers)
        # A huge interval means we never auto-export; we drive it with force_flush.
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=2**31 - 1)
        provider = MeterProvider(resource=Resource.create({"service.name": self.service}), metric_readers=[reader])
        meter = provider.get_meter("claude-usage-exporter")

        def callback(_options):
            yield from observations

        meter.create_observable_counter(metric_name, callbacks=[callback], unit="1", description=_DEFAULT_HELP)
        ok = provider.force_flush(timeout_millis=self.timeout_millis)
        provider.shutdown()
        if not ok:
            raise RuntimeError("OTLP export failed -- state persisted; will retry next run.")
