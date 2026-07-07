"""Labelers: map a transcript file to a set of metric labels (e.g. which agent produced it).

This is the LABELER seam. A labeler is any callable ``(transcript_path: Path) -> dict``.

The important insight: orchestration frameworks (OpenClaw, Hermes, ...) do NOT have
their own transcript formats -- they all shell out to ``claude -p`` and Claude Code
writes the transcripts. The ONLY thing that differs per framework is how a transcript
maps to an agent identity. So "multi-framework support" is configuration, not code:
ship a rules file, not a plugin. ``examples/labeler-openclaw.toml`` is one such file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def null_labeler(_path: Path) -> dict:
    """No extra labels -- plain Claude Code usage, keyed only by model + token_type."""
    return {}


class RulesLabeler:
    """First-match-wins regex rules over part of the transcript path.

    Claude Code encodes the launch CWD as the project-dir name (``/`` -> ``-``), so
    the dir name identifies the agent/workspace. Each rule maps a regex to a label
    value; the first matching rule wins. Anything unmatched gets ``default``.
    """

    def __init__(
        self,
        rules: list[tuple[re.Pattern, str]],
        label: str = "agent",
        default: str = "unknown",
        match_on: str = "parent",
    ) -> None:
        self.rules = rules
        self.label = label
        self.default = default
        self.match_on = match_on  # "parent" dir name, "name" (filename), or "path"

    @classmethod
    def from_config(cls, config: dict) -> "RulesLabeler":
        rules = [(re.compile(rule["pattern"]), rule["value"]) for rule in config.get("rules", [])]
        return cls(
            rules,
            label=config.get("label", "agent"),
            default=config.get("default", "unknown"),
            match_on=config.get("match_on", "parent"),
        )

    @classmethod
    def from_file(cls, path: str) -> "RulesLabeler":
        text = Path(path).read_text()
        if str(path).endswith(".toml"):
            import tomllib

            config = tomllib.loads(text)
        else:
            config = json.loads(text)
        return cls.from_config(config)

    def _subject(self, path: Path) -> str:
        if self.match_on == "name":
            return path.name
        if self.match_on == "path":
            return str(path)
        return path.parent.name

    def __call__(self, path: Path) -> dict:
        subject = self._subject(path)
        for regex, value in self.rules:
            if regex.search(subject):
                return {self.label: value}
        return {self.label: self.default}
