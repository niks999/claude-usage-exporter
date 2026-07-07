"""Config-driven labeling (the multi-framework seam)."""

from __future__ import annotations

from pathlib import Path

from claude_usage_exporter import labelers


def _cfg():
    return {
        "label": "agent",
        "default": "unknown",
        "match_on": "parent",
        "rules": [
            {"pattern": r"-agents-chief-of-staff$", "value": "chief-of-staff"},
            {"pattern": r"-agents-engineer$", "value": "engineer"},
            {"pattern": r"-repos-agent-os$", "value": "agent-os-shared"},
        ],
    }


def test_null_labeler_returns_empty():
    assert labelers.null_labeler(Path("/whatever/x.jsonl")) == {}


def test_rules_first_match_wins():
    lab = labelers.RulesLabeler.from_config(_cfg())
    p = Path("/home/u/.claude/projects/home-u-openclaw-agents-engineer/sess.jsonl")
    assert lab(p) == {"agent": "engineer"}


def test_rules_default_when_unmatched():
    lab = labelers.RulesLabeler.from_config(_cfg())
    p = Path("/home/u/.claude/projects/home-u-some-random-project/sess.jsonl")
    assert lab(p) == {"agent": "unknown"}


def test_rules_match_on_parent_dir_name():
    lab = labelers.RulesLabeler.from_config(_cfg())
    p = Path("/home/u/.claude/projects/x-repos-agent-os/deep/sess.jsonl")
    # match_on="parent" looks at the immediate parent dir name, which here is "deep".
    assert lab(p) == {"agent": "unknown"}


def test_from_file_toml(tmp_path):
    cfg = tmp_path / "labeler.toml"
    cfg.write_text(
        'label = "agent"\ndefault = "unknown"\n\n[[rules]]\npattern = "-agents-engineer$"\nvalue = "engineer"\n'
    )
    lab = labelers.RulesLabeler.from_file(str(cfg))
    assert lab(Path("/p/x-agents-engineer/s.jsonl")) == {"agent": "engineer"}


def test_from_file_json(tmp_path):
    cfg = tmp_path / "labeler.json"
    cfg.write_text('{"rules": [{"pattern": "-agents-engineer$", "value": "engineer"}]}')
    lab = labelers.RulesLabeler.from_file(str(cfg))
    assert lab(Path("/p/x-agents-engineer/s.jsonl")) == {"agent": "engineer"}
