# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-07-07

### Fixed
- OTLP sink now pins a stable `service.instance.id` (default: hostname, override via
  `--service-instance-id` / `$OTLP_SERVICE_INSTANCE_ID`). Recent `opentelemetry-sdk`
  versions auto-generate a random per-process UUID, which for a cron-style oneshot
  minted a fresh series set on every run -- cardinality churn that inflated `sum` and
  broke `increase`. Found via production shadow deployment.

## [0.1.0] - 2026-07-07

### Added
- Initial release. Incremental parser for Claude Code transcripts
  (`~/.claude/projects/**/*.jsonl`) into the cumulative counter `claude_tokens_total`,
  labelled by `agent` / `model` / `token_type`.
- Three sinks: stdout (text/JSON), Prometheus textfile-collector, and OTLP/HTTP
  (via the optional `[otlp]` extra).
- Config-driven per-agent labeling (first-match-wins regex rules), with a worked
  OpenClaw example.
- Generic systemd service + timer examples.
