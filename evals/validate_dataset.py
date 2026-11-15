"""Golden-set validator (CONTRACT §14.1): per-case checks, expected-output derivation, coverage.

Run as ``uv run python -m evals.validate_dataset [path/to/cases.jsonl]``.
"""

import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from evals.schema import (
    CurrentKind,
    Expected,
    ExpectedConflict,
    ExpectedSuggestion,
    GoldenCase,
    PlantedRole,
    Tag,
)
from owl.domain.bundle import BundleError, Document, DocumentBundle
from owl.domain.catalog import Catalog
from owl.domain.evidence import MatchKind
from owl.domain.serialisation import serialise_value
from owl.domain.suggestions import (
    Candidate,
    CurrentValuesError,
    Evidence,
    Mode,
    SuggestionResult,
    WarningCode,
    parse_current_values,
    reconcile,
)
from owl.domain.taxonomy import BusinessType, DocumentType, Lob
from owl.domain.values import FieldType, Value, ValueParseError, equality_key, parse_value
from owl.infrastructure.catalog_yaml import load_catalog

DEFAULT_PATH = Path(__file__).parent / "datasets" / "v1" / "cases.jsonl"
CATALOG_PATH = Path(__file__).parent.parent / "catalog" / "fields.yaml"

_CANDIDATE_ROLES = (PlantedRole.TRUE, PlantedRole.CONFLICTING, PlantedRole.SUPERSEDED)


def load_cases(path: Path = DEFAULT_PATH) -> list[GoldenCase]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [GoldenCase.model_validate_json(line) for line in lines]


def _json_key(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True)


# --- expected output, derived from the planted candidates ------------------------------------


def derive_expected(case: GoldenCase, catalog: Catalog) -> tuple[Expected, SuggestionResult]:
    """``reconcile`` applied to the ideal candidates and the current values (CONTRACT §14.1)."""
    active = catalog.active_fields(case.lob, case.business_type)
    specs = {a.definition.name: a.definition.value_spec for a in active}
    docs = {d.document_id: d for d in case.documents}
    candidates = [
        Candidate(
            p.field,
            parse_value(specs[p.field], p.value),
            (
                Evidence(
                    p.document_id,
                    docs[p.document_id].document_date,
                    docs[p.document_id].filename,
                    p.quote,
                    MatchKind.EXACT,
                ),
            ),
            "planted",
            1.0,
        )
        for p in case.planted
        if p.role in _CANDIDATE_ROLES
    ]
    current: dict[str, Value] = {}
    if case.mode is Mode.UPDATE:
        current = dict(parse_current_values(catalog, active, case.current_values).values)
    result = reconcile(active, candidates, current, case.mode)

    def ser(name: str, value: Value | None) -> JsonValue:
        if value is None:
            return None
        return cast("JsonValue", serialise_value(specs[name], value))

    expected = Expected(
        suggestions=[
            ExpectedSuggestion(
                field=s.field,
                action=s.action,
                current_value=ser(s.field, s.current_value),
                proposed_value=ser(s.field, s.proposed_value),
            )
            for s in result.suggestions
        ],
        conflicts=[
            ExpectedConflict(
                field=c.field,
                current_value=ser(c.field, c.current_value),
                values=[ser(c.field, x.value) for x in c.candidates],
            )
            for c in result.conflicts
        ],
    )
    return expected, result


def _same_expected(a: Expected, b: Expected) -> bool:
    def norm(e: Expected) -> Any:
        data = e.model_dump(mode="json")
        for conflict in data["conflicts"]:
            conflict["values"] = sorted(conflict["values"], key=_json_key)
        return data

    return norm(a) == norm(b)


def _rule_expected(case: GoldenCase) -> dict[str, str]:
    """An independent oracle: field -> add / change / conflict, from the case's own construction."""
    out: dict[str, str] = {}
    planted_true = {p.field for p in case.planted if p.role is PlantedRole.TRUE}
    conflicting = {p.field for p in case.planted if p.role is PlantedRole.CONFLICTING}
    for name in case.true_record:
        if name not in planted_true:
            continue
        if name in conflicting:
            out[name] = "conflict"
            continue
        kind = case.current_kinds.get(name, CurrentKind.MISSING)
        if kind is CurrentKind.MISSING:
            out[name] = "add"
        elif kind is CurrentKind.WRONG:
            out[name] = "change"
    return out


# --- per-case validation ---------------------------------------------------------------------


def validate_case(case: GoldenCase, catalog: Catalog) -> list[str]:
    """Every problem found in one case (empty if the case is sound)."""
    problems: list[str] = []
    active = {
        a.definition.name: a.definition for a in catalog.active_fields(case.lob, case.business_type)
    }
    specs = {name: d.value_spec for name, d in active.items()}

    def parse(name: str, raw: JsonValue, where: str) -> Value | None:
        if name not in specs:
            problems.append(f"{where}: field {name!r} is not an active field")
            return None
        try:
            return parse_value(specs[name], raw)
        except ValueParseError as exc:
            problems.append(f"{where}: {exc}")
            return None

    # documents
    try:
        bundle = DocumentBundle(
            tuple(
                Document(d.document_id, d.document_date, d.text, d.filename, d.document_type)
                for d in case.documents
            )
        )
        if [d.document_id for d in bundle.documents] != [d.document_id for d in case.documents]:
            problems.append("documents are not in chronological order (oldest first)")
    except BundleError as exc:
        problems.extend(exc.problems)
    docs = {d.document_id: d for d in case.documents}

    # true record: parses, and is stored in canonical form
    true: dict[str, Value] = {}
    for name, raw in case.true_record.items():
        value = parse(name, raw, f"true_record[{name}]")
        if value is not None:
            true[name] = value
            if serialise_value(specs[name], value) != raw:
                problems.append(f"true_record[{name}]: not in canonical form")

    # planted quotes
    true_dates: dict[str, list[Any]] = {}
    for i, p in enumerate(case.planted):
        where = f"planted[{i}] ({p.field}, {p.role.value})"
        doc = docs.get(p.document_id)
        if doc is None:
            problems.append(f"{where}: unknown document {p.document_id}")
            continue
        if p.quote not in doc.text:
            problems.append(f"{where}: quote is not literally present in the document")
        value = parse(p.field, p.value, where)
        if value is None:
            continue
        spec = specs[p.field]
        equals_true = p.field in true and equality_key(spec, value) == equality_key(
            spec, true[p.field]
        )
        if p.role is PlantedRole.TRUE:
            if p.field not in true:
                problems.append(f"{where}: field is not in the true record")
            elif not equals_true:
                problems.append(f"{where}: value differs from the true record")
            true_dates.setdefault(p.field, []).append(doc.document_date)
        elif p.role is not PlantedRole.RESTATED_OLD and equals_true:
            problems.append(f"{where}: a {p.role.value} value must differ from the true record")
    for i, p in enumerate(case.planted):
        if p.role in (PlantedRole.CONFLICTING, PlantedRole.SUPERSEDED) and p.document_id in docs:
            latest = max(true_dates.get(p.field, []), default=None)
            date = docs[p.document_id].document_date
            if latest is None:
                problems.append(f"planted[{i}]: {p.role.value} value without a true planting")
            elif p.role is PlantedRole.CONFLICTING and date != latest:
                problems.append(
                    f"planted[{i}]: a conflicting quote must be dated like the true one"
                )
            elif p.role is PlantedRole.SUPERSEDED and date >= latest:
                problems.append(f"planted[{i}]: a superseded quote must be in an earlier document")

    # current values and kinds
    if case.mode is Mode.EXTRACTION:
        if case.current_values or case.current_kinds:
            problems.append("extraction mode takes no current values or kinds")
    else:
        try:
            parsed = parse_current_values(
                catalog, catalog.active_fields(case.lob, case.business_type), case.current_values
            )
            if parsed.warnings:
                problems.append("current_values contain inactive fields")
            for name, kind in case.current_kinds.items():
                if name not in true:
                    problems.append(f"current_kinds[{name}]: field is not in the true record")
                    continue
                spec = specs[name]
                current = parsed.values.get(name)
                if kind is CurrentKind.MISSING:
                    ok = current is None
                elif current is None:
                    ok = False
                else:
                    same = equality_key(spec, current) == equality_key(spec, true[name])
                    ok = not same if kind is CurrentKind.WRONG else same
                if not ok:
                    problems.append(
                        f"current_kinds[{name}]: value does not match kind {kind.value!r}"
                    )
                raw = case.current_values.get(name)
                variant = kind is CurrentKind.CORRECT_FORMAT_VARIANT
                if variant and raw == case.true_record.get(name):
                    problems.append(
                        f"current_kinds[{name}]: a format variant must be spelled differently"
                    )
            problems.extend(
                f"current_values[{n}]: not covered by current_kinds"
                for n in case.current_values
                if n not in case.current_kinds
            )
            problems.extend(
                f"current_kinds[{n}]: missing for a true-record field"
                for n in true
                if n not in case.current_kinds
            )
        except CurrentValuesError as exc:
            problems.extend(exc.problems)

    # tags agree with the content
    roles = {p.role for p in case.planted}
    planted_true = {p.field for p in case.planted if p.role is PlantedRole.TRUE}
    checks: dict[Tag, bool] = {
        Tag.CONFLICT: PlantedRole.CONFLICTING in roles,
        Tag.RESTATED_OLD_VALUE: bool(roles & {PlantedRole.SUPERSEDED, PlantedRole.RESTATED_OLD}),
        Tag.DISTRACTOR: PlantedRole.DISTRACTOR in roles,
        Tag.NO_EVIDENCE: len(planted_true) < len(case.true_record),
        Tag.LONG_BUNDLE: len(case.documents) >= 8,
    }
    for tag, holds in checks.items():
        if (tag in case.tags) != holds:
            problems.append(
                f"tag {tag.value!r} {'missing' if holds else 'set without matching content'}"
            )

    # expected output
    if not problems:
        try:
            expected, result = derive_expected(case, catalog)
        except (ValueError, KeyError) as exc:  # pragma: no cover - defensive
            problems.append(f"cannot derive the expected output: {exc}")
        else:
            if not _same_expected(expected, case.expected):
                problems.append(
                    "expected output differs from reconcile applied to the planted candidates"
                )
            rule = _rule_expected(case)
            got = {s.field: s.action.value for s in expected.suggestions}
            got.update({c.field: "conflict" for c in expected.conflicts})
            if got != rule:
                problems.append(
                    f"expected output differs from the by-construction rule: {rule} vs {got}"
                )
            warned = {
                w.field for w in result.warnings if w.code is WarningCode.CANDIDATE_SUPERSEDED
            }
            superseded = {p.field for p in case.planted if p.role is PlantedRole.SUPERSEDED}
            if warned != superseded:
                problems.append("superseded plantings and candidate_superseded warnings disagree")
    return problems


def validate_dataset(cases: Sequence[GoldenCase], catalog: Catalog) -> list[str]:
    problems: list[str] = []
    ids = Counter(c.case_id for c in cases)
    problems.extend(f"duplicate case_id {i}" for i, n in ids.items() if n > 1)
    uuids = Counter(d.document_id for c in cases for d in c.documents)
    problems.extend(f"document_id {u} used more than once" for u, n in uuids.items() if n > 1)
    for case in cases:
        problems.extend(f"{case.case_id}: {p}" for p in validate_case(case, catalog))
    return problems


# --- coverage --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Targets:
    min_cases: int = 60
    min_per_cell_and_mode: int = 7  # (lob, business_type, mode)
    min_per_tag: int = 6
    min_documents_per_type: int = 6
    min_per_current_kind: int = 10
    min_per_field_type: int = 5  # true plantings
    min_silent_cases_per_mode: int = 1  # no true planting at all


@dataclass(frozen=True)
class Coverage:
    cases: int
    per_cell_mode: dict[tuple[str, str, str], int]
    per_tag: dict[str, int]
    per_tag_cell: dict[tuple[str, str, str], int]
    per_document_type: dict[str, int]
    per_current_kind: dict[str, int]
    per_field_type: dict[str, int]
    per_field: dict[str, int]
    silent_per_mode: dict[str, int]


def coverage(cases: Sequence[GoldenCase], catalog: Catalog) -> Coverage:
    types = {f.name: f.type.value for f in catalog.fields}
    per_cell_mode = Counter((c.lob.value, c.business_type.value, c.mode.value) for c in cases)
    per_tag = Counter(t.value for c in cases for t in c.tags)
    per_tag_cell = Counter(
        (t.value, c.lob.value, c.business_type.value) for c in cases for t in c.tags
    )
    per_doc = Counter(d.document_type.value for c in cases for d in c.documents)
    per_kind = Counter(k.value for c in cases for k in c.current_kinds.values())
    true_plantings = [p for c in cases for p in c.planted if p.role is PlantedRole.TRUE]
    per_field = Counter(p.field for p in true_plantings)
    per_type = Counter(types[p.field] for p in true_plantings)
    silent = Counter(
        c.mode.value for c in cases if not any(p.role is PlantedRole.TRUE for p in c.planted)
    )
    return Coverage(
        len(cases),
        dict(per_cell_mode),
        dict(per_tag),
        {(a, b, c): n for (a, b, c), n in per_tag_cell.items()},
        dict(per_doc),
        dict(per_kind),
        dict(per_type),
        dict(per_field),
        dict(silent),
    )


def coverage_problems(
    cases: Sequence[GoldenCase], catalog: Catalog, targets: Targets = Targets()
) -> list[str]:
    cov = coverage(cases, catalog)
    problems: list[str] = []

    def need(label: str, have: int, want: int) -> None:
        if have < want:
            problems.append(f"{label}: {have}, need {want}")

    need("cases", cov.cases, targets.min_cases)
    for lob in Lob:
        for bt in BusinessType:
            for mode in Mode:
                key = (lob.value, bt.value, mode.value)
                need(
                    f"{key[0]}/{key[1]}/{key[2]} cases",
                    cov.per_cell_mode.get(key, 0),
                    targets.min_per_cell_and_mode,
                )
            for tag in Tag:
                need(
                    f"tag {tag.value} in {lob.value}/{bt.value}",
                    cov.per_tag_cell.get((tag.value, lob.value, bt.value), 0),
                    1,
                )
    for tag in Tag:
        need(f"tag {tag.value} cases", cov.per_tag.get(tag.value, 0), targets.min_per_tag)
    for doc_type in DocumentType:
        need(
            f"document type {doc_type.value}",
            cov.per_document_type.get(doc_type.value, 0),
            targets.min_documents_per_type,
        )
    for kind in CurrentKind:
        need(
            f"current kind {kind.value}",
            cov.per_current_kind.get(kind.value, 0),
            targets.min_per_current_kind,
        )
    for field_type in FieldType:
        need(
            f"field type {field_type.value} true plantings",
            cov.per_field_type.get(field_type.value, 0),
            targets.min_per_field_type,
        )
    for f in catalog.fields:
        need(f"plantings of field {f.name}", cov.per_field.get(f.name, 0), 1)
    for mode in Mode:
        need(
            f"fully silent {mode.value}-mode cases",
            cov.silent_per_mode.get(mode.value, 0),
            targets.min_silent_cases_per_mode,
        )
    return problems


def format_coverage(cov: Coverage) -> str:
    lines = [f"cases: {cov.cases}"]
    lines.append("tags: " + ", ".join(f"{k}={v}" for k, v in sorted(cov.per_tag.items())))
    lines.append(
        "document types: " + ", ".join(f"{k}={v}" for k, v in sorted(cov.per_document_type.items()))
    )
    lines.append(
        "current kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(cov.per_current_kind.items()))
    )
    lines.append(
        "field types (true plantings): "
        + ", ".join(f"{k}={v}" for k, v in sorted(cov.per_field_type.items()))
    )
    lines.append(
        "silent cases: " + ", ".join(f"{k}={v}" for k, v in sorted(cov.silent_per_mode.items()))
    )
    for (lob, bt, mode), n in sorted(cov.per_cell_mode.items()):
        lines.append(f"  {lob}/{bt}/{mode}: {n}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else DEFAULT_PATH
    catalog = load_catalog(CATALOG_PATH)
    try:
        cases = load_cases(path)
    except ValidationError as exc:
        print(exc)
        return 1
    problems = validate_dataset(cases, catalog) + coverage_problems(cases, catalog)
    print(format_coverage(coverage(cases, catalog)))
    for problem in problems:
        print("PROBLEM:", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
