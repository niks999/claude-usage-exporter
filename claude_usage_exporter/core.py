"""Core: read Claude Code transcripts incrementally and fold token usage into cumulative counters.

This is the SOURCE seam. It is deliberately the only place that knows the Claude
Code transcript format. It stays framework-agnostic: which agent produced a
transcript is decided by an injected ``labeler`` (see ``labelers.py``), and where
the counters go is decided by a ``sink`` (see ``sinks.py``).

Design
------
* Incremental: a per-file byte offset in a state file; only appended bytes are read.
* Exact: dedup by ``message.id`` (Claude Code repeats assistant lines per turn, and
  the nested ``usage.iterations[]`` duplicates the top-level counts -- we read only
  the top-level ``usage`` and dedup the id).
* Monotonic: cumulative per-series totals persist in state and are re-emitted every
  run, so a CUMULATIVE-temporality counter is produced. Grafana's rate()/increase()
  work across invocations; a state reset reads as a normal counter reset.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

# Anthropic usage object field -> our token_type label value.
TOKEN_FIELDS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_creation",
}

# Bound the dedup set (FIFO) so state cannot grow without limit.
SEEN_CAP = 500_000

# A labeler maps a transcript path to extra labels, e.g. {"agent": "engineer"}.
Labeler = Callable[[Path], dict]


def _series_key(labels: dict) -> str:
    """Stable string key for a label set (order-independent, JSON-round-trippable)."""
    return json.dumps(labels, sort_keys=True, separators=(",", ":"))


def _labels_from_key(key: str) -> dict:
    return json.loads(key)


def load_state(path: str) -> dict:
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("offsets", {})  # {filepath: byte_offset}
    state.setdefault("totals", {})  # {series_key: int}
    state.setdefault("seen", [])  # recent message ids (FIFO, capped at SEEN_CAP)
    return state


def save_state(path: str, state: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)  # atomic


@dataclass
class ScanResult:
    new_messages: int = 0
    delta: dict = field(default_factory=dict)  # {series_key: tokens_added_this_run}


def scan(projects_dir: str, state: dict, labeler: Labeler) -> ScanResult:
    """Read appended bytes across all transcripts, fold new usage into cumulative totals.

    Mutates and returns via ``state``; returns a per-run delta summary for logging.
    """
    offsets: dict = state["offsets"]
    totals: dict = state["totals"]
    seen = set(state["seen"])
    seen_order = list(state["seen"])
    delta: dict = defaultdict(int)
    new_messages = 0

    for jsonl in sorted(Path(projects_dir).glob("*/*.jsonl")):
        fpath = str(jsonl)
        try:
            size = jsonl.stat().st_size
        except OSError:
            continue
        start = offsets.get(fpath, 0)
        if start > size:  # file truncated / rotated -> re-read from 0
            start = 0
        if start == size:
            continue
        labels_base = labeler(jsonl)
        try:
            with open(jsonl, "r", errors="replace") as fh:
                fh.seek(start)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = rec.get("message")
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    mid = msg.get("id")
                    if not isinstance(usage, dict) or not mid or mid in seen:
                        continue
                    seen.add(mid)
                    seen_order.append(mid)
                    new_messages += 1
                    model = msg.get("model") or "unknown"
                    for field_name, ttype in TOKEN_FIELDS.items():
                        n = usage.get(field_name) or 0
                        if n:
                            labels = {**labels_base, "model": model, "token_type": ttype}
                            key = _series_key(labels)
                            totals[key] = totals.get(key, 0) + n
                            delta[key] += n
                offsets[fpath] = fh.tell()
        except OSError:
            continue

    if len(seen_order) > SEEN_CAP:
        seen_order = seen_order[-SEEN_CAP:]
    state["seen"] = seen_order
    return ScanResult(new_messages=new_messages, delta=dict(delta))


def iter_samples(state: dict) -> Iterator[tuple[dict, int]]:
    """Yield ``(labels, value)`` for every series in the cumulative totals."""
    for key, val in state["totals"].items():
        yield _labels_from_key(key), val
