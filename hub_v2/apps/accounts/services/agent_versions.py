"""Helpers for comparing the version reported by an agent with the rollout target."""

from __future__ import annotations

import re
from dataclasses import dataclass


_VERSION_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """Return a comparable release tuple for the project's strict X.Y.Z format."""
    match = _VERSION_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class AgentVersionState:
    code: str
    label: str
    badge_class: str


def classify_agent_version(reported: str | None, expected: str | None) -> AgentVersionState:
    """Classify a reported agent version against the version baked into the hub image."""
    reported_text = str(reported or "").strip()
    expected_parsed = parse_version(expected)
    if not reported_text:
        return AgentVersionState("missing", "nao registrada", "badge-secondary")

    reported_parsed = parse_version(reported_text)
    if reported_parsed is None:
        return AgentVersionState("invalid", "formato invalido", "badge-danger")
    if expected_parsed is None:
        return AgentVersionState("unverified", "sem alvo", "badge-secondary")
    if reported_parsed < expected_parsed:
        return AgentVersionState("outdated", "atualizacao disponivel", "badge-warning")
    if reported_parsed > expected_parsed:
        return AgentVersionState("ahead", "a frente do hub", "badge-warning")
    return AgentVersionState("current", "atual", "badge-success")
