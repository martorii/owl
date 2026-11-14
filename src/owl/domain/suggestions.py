"""Reconciliation: candidates + current values -> suggestions and conflicts (CONTRACT §6, §7.3).

Everything here works on *normalised* values; a candidate's raw LLM value must already have gone
through ``parse_value``. ``None`` is never a value: a missing current value is simply absent.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID

from owl.domain.catalog import ActiveField, Catalog
from owl.domain.evidence import MatchKind
from owl.domain.values import (
    DEFAULT_OPTIONS,
    ParseOptions,
    Value,
    ValueParseError,
    equality_key,
    format_value,
    parse_value,
)


class Mode(StrEnum):
    UPDATE = "update"
    EXTRACTION = "extraction"


class Action(StrEnum):
    ADD = "add"
    CHANGE = "change"


class WarningCode(StrEnum):
    CANDIDATE_DROPPED = "candidate_dropped"
    DOCUMENT_TRUNCATED = "document_truncated"
    DOCUMENT_SKIPPED = "document_skipped"
    GROUP_FAILED = "group_failed"
    FIELD_INACTIVE_IGNORED = "field_inactive_ignored"
    CANDIDATE_SUPERSEDED = "candidate_superseded"


VALUE_NOT_IN_QUOTE = "value_not_in_quote"


@dataclass(frozen=True)
class Evidence:
    document_id: UUID
    document_date: date  # date of the source document; drives supersession (CONTRACT §7.3)
    filename: str
    quote: str
    match: MatchKind


@dataclass(frozen=True)
class Candidate:
    """One value the LLM found for a field, already normalised, with validated evidence."""

    field: str
    value: Value
    evidence: tuple[Evidence, ...]
    rationale: str
    confidence: float
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.value is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise ValueError(f"{self.field}: candidate value must not be None")
        if not self.evidence:
            raise ValueError(f"{self.field}: a candidate needs at least one evidence entry")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"{self.field}: confidence must be within [0, 1]")


@dataclass(frozen=True)
class UpdateSuggestion:
    field: str
    action: Action
    current_value: Value | None
    proposed_value: Value
    evidence: tuple[Evidence, ...]
    rationales: tuple[str, ...]
    confidence: float  # informational only (CONTRACT §6)
    flags: tuple[str, ...]


@dataclass(frozen=True)
class ConflictCandidate:
    value: Value
    evidence: tuple[Evidence, ...]
    rationales: tuple[str, ...]
    confidence: float
    flags: tuple[str, ...]
    is_current: bool  # equals the field's current value


@dataclass(frozen=True)
class Conflict:
    field: str
    current_value: Value | None
    candidates: tuple[ConflictCandidate, ...]


@dataclass(frozen=True)
class PipelineWarning:
    """Not named ``Warning``: that would shadow the builtin."""

    code: WarningCode
    message: str
    field: str | None = None
    group: str | None = None
    document_id: UUID | None = None


@dataclass(frozen=True)
class SuggestionResult:
    suggestions: tuple[UpdateSuggestion, ...]
    conflicts: tuple[Conflict, ...]
    warnings: tuple[PipelineWarning, ...]


# --- reconciliation (CONTRACT §7.3) ----------------------------------------------------------


@dataclass(frozen=True)
class _Merged:
    value: Value
    evidence: tuple[Evidence, ...]
    rationales: tuple[str, ...]
    confidence: float
    flags: tuple[str, ...]


def _unique[T](items: Iterable[T]) -> tuple[T, ...]:
    """Drop repeats, keeping first-seen order."""
    return tuple(dict.fromkeys(items))


def _latest(group: Sequence[Candidate]) -> date:
    """Date of the latest document supporting a value."""
    return max(e.document_date for c in group for e in c.evidence)


def _merge(group: Sequence[Candidate]) -> _Merged:
    # first candidate wins ties: max() returns the first maximal element
    best = max(group, key=lambda c: c.confidence)
    evidence: dict[tuple[UUID, str], Evidence] = {}
    for candidate in group:
        for entry in candidate.evidence:
            evidence.setdefault((entry.document_id, entry.quote), entry)
    rationales = _unique(r for c in group if (r := c.rationale.strip()))
    flags = _unique(f for c in group for f in c.flags)
    return _Merged(best.value, tuple(evidence.values()), rationales, best.confidence, flags)


def reconcile(
    active_fields: Sequence[ActiveField],
    candidates: Iterable[Candidate],
    current_values: Mapping[str, Value],
    mode: Mode,
) -> SuggestionResult:
    if mode is Mode.EXTRACTION and current_values:
        raise ValueError("current values are not accepted in extraction mode")
    by_field: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_field.setdefault(candidate.field, []).append(candidate)
    active_names = {a.definition.name for a in active_fields}
    unknown = sorted(set(by_field) - active_names)
    if unknown:
        raise ValueError(f"candidates for fields that are not active: {', '.join(unknown)}")

    suggestions: list[UpdateSuggestion] = []
    conflicts: list[Conflict] = []
    warnings: list[PipelineWarning] = []
    for active in active_fields:
        name = active.definition.name
        spec = active.definition.value_spec
        found = by_field.get(name)
        if not found:
            continue
        current = current_values.get(name)
        current_key = None if current is None else equality_key(spec, current)

        groups: dict[object, list[Candidate]] = {}
        for candidate in found:
            groups.setdefault(equality_key(spec, candidate.value), []).append(candidate)

        # Supersession guardrail: a value whose latest supporting document is older than the latest
        # supporting document of another value has been replaced by it.
        newest = max(_latest(g) for g in groups.values())
        for key in [k for k, g in groups.items() if _latest(g) < newest]:
            old = _merge(groups.pop(key))
            warnings.append(
                PipelineWarning(
                    WarningCode.CANDIDATE_SUPERSEDED,
                    f"{name}: {format_value(spec, old.value)} was superseded by a value from a "
                    f"later document",
                    field=name,
                    group=active.definition.group,
                )
            )

        if len(groups) == 1:
            ((key, group),) = groups.items()
            if current is not None and key == current_key:
                continue
            merged = _merge(group)
            suggestions.append(
                UpdateSuggestion(
                    field=name,
                    action=Action.ADD if current is None else Action.CHANGE,
                    current_value=current,
                    proposed_value=merged.value,
                    evidence=merged.evidence,
                    rationales=merged.rationales,
                    confidence=merged.confidence,
                    flags=merged.flags,
                )
            )
        else:
            conflicts.append(
                Conflict(
                    field=name,
                    current_value=current,
                    candidates=tuple(
                        ConflictCandidate(
                            merged.value,
                            merged.evidence,
                            merged.rationales,
                            merged.confidence,
                            merged.flags,
                            is_current=current is not None and key == current_key,
                        )
                        for key, group in groups.items()
                        for merged in (_merge(group),)
                    ),
                )
            )
    return SuggestionResult(tuple(suggestions), tuple(conflicts), tuple(warnings))


# --- current values (CONTRACT §5.1) ----------------------------------------------------------


class CurrentValuesError(ValueError):
    """Invalid ``current_values``. ``problems`` lists every issue found, not just the first."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid current_values:\n- " + "\n- ".join(self.problems))


@dataclass(frozen=True)
class ParsedCurrentValues:
    values: Mapping[str, Value]
    warnings: tuple[PipelineWarning, ...]


def parse_current_values(
    catalog: Catalog,
    active_fields: Sequence[ActiveField],
    raw: Mapping[str, object],
    options: ParseOptions = DEFAULT_OPTIONS,
) -> ParsedCurrentValues:
    """Normalise the caller's current values.

    Unknown field → error; inactive field → ignored with a ``field_inactive_ignored`` warning;
    unparseable value → error. ``null`` means "no current value" and is dropped silently.
    """
    active = {a.definition.name: a.definition for a in active_fields}
    values: dict[str, Value] = {}
    warnings: list[PipelineWarning] = []
    problems: list[str] = []
    for name, raw_value in raw.items():
        if catalog.get(name) is None:
            problems.append(f"{name}: unknown field")
            continue
        definition = active.get(name)
        if definition is None:
            warnings.append(
                PipelineWarning(
                    WarningCode.FIELD_INACTIVE_IGNORED,
                    f"{name} is not active for this request; its current value was ignored",
                    field=name,
                )
            )
            continue
        if raw_value is None:
            continue
        try:
            values[name] = parse_value(definition.value_spec, raw_value, options)
        except ValueParseError as exc:
            problems.append(f"{name}: {exc}")
    if problems:
        raise CurrentValuesError(problems)
    return ParsedCurrentValues(values, tuple(warnings))
