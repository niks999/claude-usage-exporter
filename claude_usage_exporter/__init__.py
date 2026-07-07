"""claude-usage-exporter: Claude Code token usage -> Prometheus / OTLP / stdout.

Parses the real per-turn ``message.usage`` Claude Code writes to its transcripts
(``~/.claude/projects/**/*.jsonl``) into cumulative token counters, labelled per
agent/model/token_type, and emits them to a pluggable sink. Works on Max/Pro
OAuth subscriptions, where API-key telemetry is not available.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
