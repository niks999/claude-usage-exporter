"""End-to-end CLI behaviour (dry-run + prometheus sink), all offline."""

from __future__ import annotations

import json

from claude_usage_exporter import cli


def _seed(tmp_path):
    proj = tmp_path / "projects" / "home-u-openclaw-agents-engineer"
    proj.mkdir(parents=True)
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    with open(proj / "s.jsonl", "w") as fh:
        fh.write(json.dumps({"message": {"id": "m1", "model": "claude-opus-4-8", "usage": usage}}) + "\n")
    return proj


def test_dry_run_prints_and_does_not_persist(tmp_path, capsys):
    _seed(tmp_path)
    labeler_cfg = tmp_path / "labeler.json"
    labeler_cfg.write_text('{"rules": [{"pattern": "-agents-engineer$", "value": "engineer"}]}')
    state_file = tmp_path / "state.json"

    rc = cli.main(
        [
            "--projects-dir",
            str(tmp_path / "projects"),
            "--state-file",
            str(state_file),
            "--labeler-config",
            str(labeler_cfg),
            "--dry-run",
        ]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert 'agent="engineer"' in out
    assert not state_file.exists()  # dry-run must not persist


def test_prometheus_sink_end_to_end(tmp_path):
    _seed(tmp_path)
    state_file = tmp_path / "state.json"
    prom = tmp_path / "out.prom"

    rc = cli.main(
        [
            "--projects-dir",
            str(tmp_path / "projects"),
            "--state-file",
            str(state_file),
            "--sink",
            "prometheus",
            "--prometheus-textfile",
            str(prom),
        ]
    )

    assert rc == 0
    assert state_file.exists()  # non-dry-run persists
    text = prom.read_text()
    assert "# TYPE claude_tokens_total counter" in text
    assert "claude_tokens_total{" in text


def test_otlp_sink_requires_endpoint(tmp_path):
    _seed(tmp_path)
    try:
        cli.main(
            [
                "--projects-dir",
                str(tmp_path / "projects"),
                "--state-file",
                str(tmp_path / "state.json"),
                "--sink",
                "otlp",
            ]
        )
    except SystemExit as exc:
        assert "otlp" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("expected SystemExit for missing OTLP endpoint")
