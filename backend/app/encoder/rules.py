"""Pure rule evaluation: probe facts in, a target out.

An ordered list, first match wins. Each rule ANDs its conditions. A target is
either a preset name or the literal ``skip``. No I/O, no clock, no randomness --
so the UI's "test against a file" tool runs the identical code path the
watcher does, and the answer it shows is the answer that will happen.
"""

from dataclasses import dataclass, field
from typing import Any

# Fields a condition may name, mapped to how they compare. Enumerated rather
# than accepting any key from the probe dict so a typo is an error at
# evaluation instead of a rule that silently never fires.
_NUMERIC_FIELDS = {"height", "width", "size", "bit_rate", "bit_depth",
                   "frame_rate", "duration"}
_STRING_FIELDS = {"video_codec", "profile", "source_tool", "encoder_tag"}
_BOOL_FIELDS = {"hdr", "dolby_vision"}
_FIELDS = _NUMERIC_FIELDS | _STRING_FIELDS | _BOOL_FIELDS

# `contains` is string-only. It exists for `encoder_tag`, whose value carries a
# version -- "libmakemkv v1.18.4 (1.3.10/1.5.2) win(x64-release)" -- so an
# exact comparison would break on the next MakeMKV release. `source_tool` is
# the normalised field for the common case; this is the escape hatch.
_OPERATORS = {">=", "<=", ">", "<", "==", "!=", "contains"}
_STRING_ONLY_OPERATORS = {"contains"}

SKIP = "skip"
"""Target meaning 'leave this file alone'."""


class RuleError(ValueError):
    """Raised when a rule names an unknown field or operator."""


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class Rule:
    id: str
    conditions: list[Condition]
    target: str


@dataclass(frozen=True)
class Match:
    """Which rule won, what it selected, and which rules were tested.

    ``evaluated`` exists for the UI: rules after the winner are never reached,
    and an unreachable rule is otherwise invisible until it has failed to fire
    on a file the user expected it to catch.
    """

    rule_id: str | None
    target: str
    evaluated: list[str] = field(default_factory=list)


def evaluate(facts: dict, rules: list[Rule], fallback: str) -> Match:
    """The first rule whose conditions all hold, else *fallback*."""
    evaluated: list[str] = []
    for rule in rules:
        evaluated.append(rule.id)
        if all(_holds(facts, c) for c in rule.conditions):
            return Match(rule_id=rule.id, target=rule.target, evaluated=evaluated)
    return Match(rule_id=None, target=fallback, evaluated=evaluated)


def _holds(facts: dict, condition: Condition) -> bool:
    if condition.field not in _FIELDS:
        raise RuleError(
            f"Unknown condition field {condition.field!r}. "
            f"Known: {sorted(_FIELDS)}"
        )
    if condition.op not in _OPERATORS:
        raise RuleError(
            f"Unknown operator {condition.op!r}. Known: {sorted(_OPERATORS)}"
        )

    actual = facts.get(condition.field)
    if actual is None:
        # The probe could not determine this fact. Not an error -- the rule
        # simply does not apply, and evaluation falls through to the next one.
        return False

    if condition.field in _BOOL_FIELDS:
        return _compare(bool(actual), bool(condition.value), condition.op)

    if condition.field in _STRING_FIELDS:
        # ffprobe reports `hevc`; a user typing `HEVC` means the same thing.
        return _compare(
            str(actual).lower(), str(condition.value).lower(), condition.op
        )

    if condition.op in _STRING_ONLY_OPERATORS:
        raise RuleError(
            f"Operator {condition.op!r} applies to text fields only, not "
            f"{condition.field!r}"
        )

    try:
        return _compare(float(actual), float(condition.value), condition.op)
    except (TypeError, ValueError):
        # A non-numeric value in a numeric field: treat as no match rather than
        # failing the whole evaluation, so one bad rule cannot stop the queue.
        return False


def _compare(left: Any, right: Any, op: str) -> bool:
    if op == "contains":
        return str(right) in str(left)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left < right
