"""Command-line interface: scan transcripts, then fan the totals out to one or more sinks."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__, core, labelers, sinks

DEFAULT_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
DEFAULT_STATE_FILE = os.path.expanduser("~/.local/state/claude-usage-exporter/state.json")
DEFAULT_METRIC = "claude_tokens_total"


def build_labeler(args: argparse.Namespace) -> core.Labeler:
    if args.labeler_config:
        return labelers.RulesLabeler.from_file(args.labeler_config)
    return labelers.null_labeler


def build_sinks(args: argparse.Namespace) -> list:
    chosen = args.sink or ["stdout"]
    built = []
    for name in chosen:
        if name == "stdout":
            built.append(sinks.StdoutSink(fmt=args.format))
        elif name == "prometheus":
            if not args.prometheus_textfile:
                raise SystemExit("--sink prometheus requires --prometheus-textfile PATH (or $PROM_TEXTFILE)")
            built.append(sinks.PrometheusTextfileSink(args.prometheus_textfile))
        elif name == "otlp":
            endpoint = args.otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            if not endpoint:
                raise SystemExit("--sink otlp requires --otlp-endpoint (or $OTEL_EXPORTER_OTLP_ENDPOINT)")
            built.append(sinks.OtlpSink(endpoint=endpoint, auth=args.otlp_auth, service=args.service))
        else:  # pragma: no cover - argparse choices guard this
            raise SystemExit(f"unknown sink: {name}")
    return built


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="claude-usage-exporter", description=__doc__)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument(
        "--projects-dir",
        default=os.environ.get("CLAUDE_PROJECTS_DIR", DEFAULT_PROJECTS_DIR),
        help="Claude Code projects dir (default: ~/.claude/projects)",
    )
    ap.add_argument(
        "--state-file",
        default=os.environ.get("EXPORTER_STATE_FILE", DEFAULT_STATE_FILE),
        help="incremental scan state (default: ~/.local/state/claude-usage-exporter/state.json)",
    )
    ap.add_argument("--metric", default=DEFAULT_METRIC, help="metric name (default: claude_tokens_total)")
    ap.add_argument(
        "--labeler-config",
        default=os.environ.get("LABELER_CONFIG"),
        help="TOML/JSON rules file mapping transcripts to an agent label (default: no agent label)",
    )
    ap.add_argument(
        "--sink",
        action="append",
        choices=["stdout", "prometheus", "otlp"],
        help="destination; repeatable (default: stdout)",
    )
    ap.add_argument("--format", default="text", choices=["text", "json"], help="stdout sink format")
    ap.add_argument(
        "--prometheus-textfile",
        default=os.environ.get("PROM_TEXTFILE"),
        help="output .prom path for the prometheus sink",
    )
    ap.add_argument("--otlp-endpoint", default="", help="OTLP/HTTP base URL for the otlp sink")
    ap.add_argument("--otlp-auth", default=None, help="Authorization header value for the otlp sink (or $OTLP_AUTH)")
    ap.add_argument("--service", default="claude-usage-exporter", help="OTLP service.name resource attribute")
    ap.add_argument("--dry-run", action="store_true", help="parse + print to stdout; do not persist state or push")
    ap.add_argument("--reset-state", action="store_true", help="ignore prior state (re-read all history)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    labeler = build_labeler(args)
    state = {"offsets": {}, "totals": {}, "seen": []} if args.reset_state else core.load_state(args.state_file)

    t0 = time.time()
    result = core.scan(args.projects_dir, state, labeler)
    elapsed = time.time() - t0

    grand = sum(state["totals"].values())
    print(
        f"[claude-usage-exporter] scanned in {elapsed:.2f}s -- +{result.new_messages} new msgs -- "
        f"cumulative {grand:,} tokens across {len(state['totals'])} series",
        file=sys.stderr,
    )

    samples = list(core.iter_samples(state))

    if args.dry_run:
        sinks.StdoutSink(fmt=args.format).emit(args.metric, samples)
        return 0

    # Persist BEFORE pushing: totals are cumulative and fully re-emitted each run, so
    # a failed push self-heals on the next run while state stays consistent with the
    # dedup set. Sinks are driven after the durable snapshot is on disk.
    core.save_state(args.state_file, state)
    for sink in build_sinks(args):
        sink.emit(args.metric, samples)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
