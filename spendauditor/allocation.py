"""Cost allocation from tags: who owns each charge, and does the tag follow policy?

Real tags are messy (the FinOps Foundation sample has `env` vs `environment` and keys with stray spaces),
so allocation is lenient (it still finds the team) while compliance is strict (exact keys only).
That way spend is not lost to "unallocated" because of a typo, but the typo is still reported.
"""
from __future__ import annotations

import json

REQUIRED_KEYS = ("team", "environment", "application")  # from data/policies/tagging_policy.md
TEAM_ALIASES = {"team", "owner_team", "business_unit"}
ENV_ALIASES = {"environment", "env"}


def parse_tags(raw) -> dict:
    if raw is None or (isinstance(raw, float)) or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def derive_team(raw) -> str:
    """Lenient: normalise key case/whitespace and accept known aliases."""
    for k, v in parse_tags(raw).items():
        if str(k).strip().lower() in TEAM_ALIASES and str(v).strip():
            return str(v).strip().lower()
    return "unallocated"


def tag_issues(raw) -> list[str]:
    """Strict: list every way this tag set breaks the tagging policy."""
    tags = parse_tags(raw)
    if not tags:
        return ["no tags"]
    issues = []
    keys = set(tags)
    for req in REQUIRED_KEYS:
        if req in keys:
            continue
        near = [k for k in keys if str(k).strip().lower() == req or (req == "environment" and k.strip() in ENV_ALIASES)]
        issues.append(f"'{near[0]}' should be '{req}'" if near else f"missing '{req}'")
    return issues


def is_compliant(raw) -> bool:
    return not tag_issues(raw)
