"""Sink output formats. The Prometheus and stdout sinks are fully verifiable offline."""

from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest

from claude_usage_exporter import sinks

SAMPLES = [
    ({"agent": "engineer", "model": "claude-opus-4-8", "token_type": "input"}, 13),
    ({"agent": "chief-of-staff", "model": "claude-opus-4-8", "token_type": "output"}, 7),
]


def test_stdout_text_format():
    buf = io.StringIO()
    sinks.StdoutSink(fmt="text", stream=buf).emit("claude_tokens_total", SAMPLES)
    out = buf.getvalue()
    assert 'claude_tokens_total{agent="engineer",model="claude-opus-4-8",token_type="input"} 13' in out
    assert out.count("\n") == 2


def test_stdout_json_format():
    buf = io.StringIO()
    sinks.StdoutSink(fmt="json", stream=buf).emit("claude_tokens_total", SAMPLES)
    payload = json.loads(buf.getvalue())
    assert payload["metric"] == "claude_tokens_total"
    assert {s["value"] for s in payload["series"]} == {13, 7}


def test_prometheus_textfile_is_written_atomically_and_parses(tmp_path):
    out = tmp_path / "sub" / "claude.prom"  # parent does not exist yet
    sinks.PrometheusTextfileSink(str(out)).emit("claude_tokens_total", SAMPLES)

    text = out.read_text()
    lines = text.splitlines()
    assert lines[0].startswith("# HELP claude_tokens_total")
    assert lines[1] == "# TYPE claude_tokens_total counter"
    body = [ln for ln in lines if not ln.startswith("#")]
    assert len(body) == 2
    assert all(ln.startswith("claude_tokens_total{") for ln in body)
    # No leftover temp file.
    assert not (tmp_path / "sub" / "claude.prom.tmp").exists()

    # Round-trip: every data line is "<metric>{labels} <int>".
    for ln in body:
        head, value = ln.rsplit(" ", 1)
        assert int(value) in (13, 7)
        assert head.startswith("claude_tokens_total{") and head.endswith("}")


def test_prometheus_label_escaping():
    buf = io.StringIO()
    sinks.StdoutSink(fmt="text", stream=buf).emit("m", [({"model": 'weird"\\name'}, 1)])
    assert 'model="weird\\"\\\\name"' in buf.getvalue()


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool not installed")
def test_prometheus_output_passes_promtool(tmp_path):
    out = tmp_path / "claude.prom"
    sinks.PrometheusTextfileSink(str(out)).emit("claude_tokens_total", SAMPLES)
    proc = subprocess.run(
        ["promtool", "check", "metrics"],
        stdin=out.open("rb"),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode()


def test_otlp_observations_mapping():
    pytest.importorskip("opentelemetry")
    obs = sinks.build_observations(SAMPLES)
    assert len(obs) == 2
    values = {o.value for o in obs}
    assert values == {13, 7}
    # Attributes carry the full label set.
    engineer = next(o for o in obs if o.value == 13)
    assert engineer.attributes["agent"] == "engineer"
    assert engineer.attributes["token_type"] == "input"


def test_otlp_sink_without_extra_raises_helpful_error(monkeypatch):
    # Simulate the extra not being installed by hiding the exporter import.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("opentelemetry.exporter"):
            raise ImportError("no otel")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        sinks.OtlpSink(endpoint="http://localhost:4318").emit("m", SAMPLES)
    assert "otlp" in str(exc.value).lower()
