# claude-usage-exporter

Export **Claude Code token usage** -- per agent, model, and token type -- to
**Prometheus, OTLP, or stdout**, parsed straight from the transcripts Claude Code
already writes. Works on **Max/Pro OAuth subscriptions**, where API-key telemetry
isn't available.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

```
claude_tokens_total{agent="engineer",      model="claude-opus-4-8", token_type="output"}      1840
claude_tokens_total{agent="chief-of-staff", model="claude-opus-4-8", token_type="cache_read"} 39000
```

## Why this exists

If you run Claude Code -- especially a multi-agent setup that shells out to
`claude -p` -- and you want token spend as **time-series metrics in your own
Prometheus/Grafana stack**, the existing options don't quite get you there:

- **[ccusage](https://github.com/ryoppippi/ccusage)** is excellent for CLI summaries, but it's CLI-only -- no metrics push.
- **Claude Code's native OpenTelemetry** exports metrics, but short `claude -p` runs
  (often sub-second in an orchestrator) can exit before the metric export interval
  flushes, so those runs drop their telemetry. It also leans on API-key-style
  telemetry config rather than a Max/Pro subscription.

The transcripts Claude Code writes to `~/.claude/projects/**/*.jsonl` carry the real
per-turn `message.usage`, are written regardless of how short the run was, and encode
the working directory (hence the agent) in the project-dir name. This tool parses
those into cumulative counters. Same `message.usage` source as native telemetry, no
flush race, and free per-agent attribution.

**Scope, honestly:** this is a focused utility for a specific niche -- subscription
users who want Claude Code token metrics in their own observability stack. It is not
a general "agent telemetry platform," and it stays small on purpose.

## Install

```bash
# Core (stdout + Prometheus sinks, zero dependencies):
pip install claude-usage-exporter

# With the OTLP sink (pulls in the OpenTelemetry SDK):
pip install "claude-usage-exporter[otlp]"

# Or run without installing, via uv:
uvx claude-usage-exporter --dry-run
```

Requires Python 3.11+.

## Quickstart

See what it would emit from your own transcripts, without writing state or pushing
anywhere:

```bash
claude-usage-exporter --dry-run
```

Try it against the bundled sample (note the duplicated message id is de-duplicated):

```bash
claude-usage-exporter --dry-run \
  --projects-dir examples/sample-transcript \
  --labeler-config examples/labeler-openclaw.toml
```

## Sinks

Pick one or more with `--sink` (repeatable). Default is `stdout`.

### stdout -- debugging / piping (no dependencies)

```bash
claude-usage-exporter --sink stdout --format text   # exposition-style lines
claude-usage-exporter --sink stdout --format json    # structured
```

### Prometheus -- textfile collector (no dependencies)

Writes the [node_exporter textfile-collector](https://github.com/prometheus/node_exporter#textfile-collector)
format atomically. Point a running node_exporter at the same directory
(`--collector.textfile.directory`) and it scrapes the file on its next cycle -- no
Prometheus server needed by this tool.

```bash
claude-usage-exporter --sink prometheus \
  --prometheus-textfile /var/lib/node_exporter/textfile/claude.prom
```

### OTLP -- push to an OTLP/HTTP endpoint (needs the `[otlp]` extra)

Emits an OTLP `ObservableCounter` with CUMULATIVE temporality, so `rate()` /
`increase()` work across runs. Auth is sent verbatim as the `Authorization` header
(e.g. `Basic <token>` for Grafana Cloud), from `--otlp-auth` or `$OTLP_AUTH`.

```bash
export OTLP_AUTH="Basic <base64-token>"
claude-usage-exporter --sink otlp \
  --otlp-endpoint https://otlp-gateway.example.net/otlp
```

Two OTLP gotchas worth knowing:

- **Stable instance id.** `service.instance.id` is pinned to the hostname by default
  (override with `--service-instance-id` / `$OTLP_SERVICE_INSTANCE_ID`). Left to the
  SDK it would be a random UUID *per process*, so a cron-style run would create a new
  series set every invocation. Keep it stable.
- **`_total` suffix normalization.** OTLP-to-Prometheus backends (e.g. Grafana Cloud)
  move a counter's `_total` suffix to the *end* of the name. `claude_tokens_total`
  passes through unchanged; a name like `my_total_metric` would arrive as
  `my_metric_total`. Name your metric so `_total` is already terminal.

## Per-agent labeling (and "multi-framework" support)

By default there's no agent label -- usage is keyed by `model` + `token_type`. To
attribute usage to agents, pass a rules file. Rules are first-match-wins regexes over
the transcript's project-dir name:

```bash
claude-usage-exporter --labeler-config examples/labeler-openclaw.toml --dry-run
```

```toml
# examples/labeler-openclaw.toml
label = "agent"
default = "unknown"
match_on = "parent"

[[rules]]
pattern = "-agents-engineer$"
value = "engineer"
```

The key design point: **orchestration frameworks (OpenClaw, Hermes, ...) don't have
their own transcript formats** -- they all drive `claude -p`, and Claude Code writes
the transcripts. The only thing that differs per framework is how a launch directory
maps to an agent. So supporting a new framework is a *config file*, not code.

## How it works

- **Incremental** -- a per-file byte offset is persisted in a state file; each run
  reads only newly appended bytes. Scans are sub-second.
- **Exact** -- messages are de-duplicated by `message.id` (Claude Code repeats
  assistant lines within a turn), and only the top-level `usage` is read.
- **Monotonic** -- cumulative totals persist in state and are fully re-emitted every
  run, producing a proper counter. A state reset reads as a normal counter reset. The
  state snapshot is written *before* the push, so a failed push self-heals next run.

State lives at `~/.local/state/claude-usage-exporter/state.json` by default. Its size
is dominated by a FIFO-capped (500k) set of seen message ids -- a few MB at most.

## Querying tip (OTLP push gotcha)

When pushed via OTLP, samples can be **sparse** -- sparser than Prometheus's default
5-minute staleness window. A plain instant query for the metric can look empty even
when everything is healthy. Probe with a long-window function instead:

```promql
# Does data exist / how fresh is it?
count(last_over_time(claude_tokens_total[60d]))

# Activity over the last 7 days, per agent:
sum by (agent) (increase(claude_tokens_total[7d]))
```

## Running on a schedule

Generic systemd user units are in [`examples/systemd/`](examples/systemd/) (a oneshot
service + a 10-minute timer). Put secrets in an `EnvironmentFile` rather than
`source`-ing them, so a value containing a space (like `Basic <token>`) survives intact.

## Architecture

Three seams, each independently swappable:

```
 Source                         Labeler                          Sink
 (Claude Code jsonl reader) --> (path -> {agent, ...}) --------> (stdout / prometheus / otlp)
 core.py                        labelers.py                      sinks.py
```

- **Source** (`core.py`) -- the only code that knows the Claude Code transcript
  format. One implementation; deliberately not abstracted over other agent CLIs
  (Codex, Cursor, ...) until there's real demand -- that's the documented seam for a
  future contributor.
- **Labeler** (`labelers.py`) -- turns a transcript into labels. Config-driven, so new
  frameworks need no code.
- **Sink** (`sinks.py`) -- turns cumulative totals into a destination write. Adding one
  (Datadog, StatsD, remote-write) is a ~20-line class implementing
  `emit(metric_name, samples)`.

## Development

```bash
pip install -e ".[otlp,dev]"
pytest
```

The stdout and Prometheus sinks are fully verified offline; the Prometheus output is
additionally linted with `promtool check metrics` when `promtool` is on PATH. The OTLP
mapping is unit-tested via the OpenTelemetry SDK without needing a live endpoint.

## Metric reference

Single metric: `claude_tokens_total` (configurable via `--metric`).

| Label        | Values                                              |
| ------------ | --------------------------------------------------- |
| `token_type` | `input`, `output`, `cache_read`, `cache_creation`   |
| `model`      | the model id from the transcript                    |
| `agent`      | whatever your labeler assigns (absent if none)      |

## License

MIT -- see [LICENSE](LICENSE).
