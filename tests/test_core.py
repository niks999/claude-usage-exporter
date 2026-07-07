"""Scan / dedup / incremental-offset behaviour of the core."""

from __future__ import annotations

import json

from claude_usage_exporter import core
from claude_usage_exporter.labelers import null_labeler


def _write_msg(fh, mid, model="claude-opus-4-8", **tokens):
    usage = {
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "cache_read_input_tokens": tokens.get("cache_read", 0),
        "cache_creation_input_tokens": tokens.get("cache_creation", 0),
    }
    fh.write(json.dumps({"message": {"id": mid, "model": model, "usage": usage}}) + "\n")


def _totals_by_type(state):
    out = {}
    for labels, value in core.iter_samples(state):
        out[labels["token_type"]] = out.get(labels["token_type"], 0) + value
    return out


def test_scan_folds_usage(tmp_path):
    proj = tmp_path / "projects" / "some-project"
    proj.mkdir(parents=True)
    with open(proj / "a.jsonl", "w") as fh:
        _write_msg(fh, "m1", input=10, output=5, cache_read=100)
        _write_msg(fh, "m2", input=3, output=7)

    state = core.load_state(str(tmp_path / "state.json"))
    result = core.scan(str(tmp_path / "projects"), state, null_labeler)

    assert result.new_messages == 2
    assert _totals_by_type(state) == {"input": 13, "output": 12, "cache_read": 100}


def test_dedup_by_message_id(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    with open(proj / "a.jsonl", "w") as fh:
        _write_msg(fh, "dup", input=10)
        _write_msg(fh, "dup", input=10)  # Claude Code repeats assistant lines per turn.

    state = core.load_state(str(tmp_path / "state.json"))
    result = core.scan(str(tmp_path / "projects"), state, null_labeler)

    assert result.new_messages == 1
    assert _totals_by_type(state)["input"] == 10


def test_incremental_only_reads_appended_bytes(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    path = proj / "a.jsonl"
    with open(path, "w") as fh:
        _write_msg(fh, "m1", input=10)

    state = core.load_state(str(tmp_path / "state.json"))
    core.scan(str(tmp_path / "projects"), state, null_labeler)

    with open(path, "a") as fh:
        _write_msg(fh, "m2", input=5)
    result = core.scan(str(tmp_path / "projects"), state, null_labeler)

    assert result.new_messages == 1  # only the appended message
    assert _totals_by_type(state)["input"] == 15  # cumulative


def test_truncation_rereads_from_zero(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    path = proj / "a.jsonl"
    with open(path, "w") as fh:
        _write_msg(fh, "m1", input=10)

    state = core.load_state(str(tmp_path / "state.json"))
    core.scan(str(tmp_path / "projects"), state, null_labeler)

    # Rotate the file to a smaller one with a brand-new id.
    with open(path, "w") as fh:
        _write_msg(fh, "m2", input=4)
    result = core.scan(str(tmp_path / "projects"), state, null_labeler)

    assert result.new_messages == 1
    assert _totals_by_type(state)["input"] == 14


def test_labeler_labels_flow_into_series(tmp_path):
    proj = tmp_path / "projects" / "x-agents-engineer"
    proj.mkdir(parents=True)
    with open(proj / "a.jsonl", "w") as fh:
        _write_msg(fh, "m1", input=10)

    def labeler(path):
        return {"agent": "engineer"}

    state = core.load_state(str(tmp_path / "state.json"))
    core.scan(str(tmp_path / "projects"), state, labeler)

    labels, value = next(iter(core.iter_samples(state)))
    assert labels["agent"] == "engineer"
    assert labels["token_type"] == "input"
    assert value == 10


def test_state_roundtrip(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    with open(proj / "a.jsonl", "w") as fh:
        _write_msg(fh, "m1", input=10)

    state_path = str(tmp_path / "state.json")
    state = core.load_state(state_path)
    core.scan(str(tmp_path / "projects"), state, null_labeler)
    core.save_state(state_path, state)

    reloaded = core.load_state(state_path)
    assert reloaded["totals"] == state["totals"]
    assert reloaded["offsets"] == state["offsets"]
