"""Gate tests for the golden set (PLAN step 05, CONTRACT §14.1)."""

import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pytest
from evals.schema import (
    CurrentKind,
    GoldenCase,
    Planted,
    PlantedRole,
    Tag,
)
from evals.validate_dataset import (
    CATALOG_PATH,
    DEFAULT_PATH,
    Targets,
    _same_expected,
    coverage,
    coverage_problems,
    derive_expected,
    format_coverage,
    load_cases,
    validate_case,
    validate_dataset,
)

from owl.domain.catalog import Catalog
from owl.domain.suggestions import Mode
from owl.infrastructure.catalog_yaml import load_catalog

DATASET_DIR = DEFAULT_PATH.parent


@lru_cache(maxsize=1)
def _cases() -> tuple[GoldenCase, ...]:
    return tuple(load_cases())


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(CATALOG_PATH)


CASES = _cases()
BY_ID = {c.case_id: c for c in CASES}


def case_of(tag: Tag, mode: Mode = Mode.UPDATE) -> GoldenCase:
    return next(c for c in CASES if tag in c.tags and c.mode is mode)


# --- the dataset itself ----------------------------------------------------------------------


def test_every_case_validates(catalog: Catalog) -> None:
    assert validate_dataset(CASES, catalog) == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_every_planted_quote_is_literally_in_its_document(case: GoldenCase) -> None:
    texts = {d.document_id: d.text for d in case.documents}
    for p in case.planted:
        assert p.quote in texts[p.document_id], f"{p.field}: {p.quote!r}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_expected_equals_reconcile_on_the_planted_candidates(
    case: GoldenCase, catalog: Catalog
) -> None:
    expected, _ = derive_expected(case, catalog)
    assert _same_expected(expected, case.expected)


def test_restated_old_value_cases_expect_the_single_correct_suggestion(catalog: Catalog) -> None:
    restated = [c for c in CASES if Tag.RESTATED_OLD_VALUE in c.tags]
    assert restated
    for case in restated:
        fields = {
            p.field
            for p in case.planted
            if p.role in (PlantedRole.SUPERSEDED, PlantedRole.RESTATED_OLD)
        }
        in_conflicts = {c.field for c in case.expected.conflicts}
        assert not fields & in_conflicts or Tag.CONFLICT in case.tags
        for s in case.expected.suggestions:
            if s.field in fields:
                assert s.proposed_value == case.true_record[s.field]


def test_conflict_cases_expect_conflicts_that_include_the_true_value() -> None:
    conflicts = [c for c in CASES if Tag.CONFLICT in c.tags]
    assert conflicts
    for case in conflicts:
        assert case.expected.conflicts
        for conflict in case.expected.conflicts:
            assert case.true_record[conflict.field] in conflict.values
            assert len(conflict.values) >= 2


def test_extraction_cases_expect_only_additions_and_no_current_values() -> None:
    for case in CASES:
        if case.mode is Mode.EXTRACTION:
            assert not case.current_values
            assert all(s.action.value == "add" for s in case.expected.suggestions)
            assert all(s.current_value is None for s in case.expected.suggestions)


def test_silent_cases_expect_nothing() -> None:
    silent = [c for c in CASES if all(p.role is not PlantedRole.TRUE for p in c.planted)]
    assert len(silent) >= 2
    for case in silent:
        assert case.expected.suggestions == []
        assert case.expected.conflicts == []


def test_coverage_targets_are_met(catalog: Catalog, capsys: pytest.CaptureFixture[str]) -> None:
    report = format_coverage(coverage(CASES, catalog))
    with capsys.disabled():
        print("\n" + report)
    assert coverage_problems(CASES, catalog) == []


def test_dataset_is_frozen() -> None:
    recorded = (DATASET_DIR / "cases.sha256").read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256((DATASET_DIR / "cases.jsonl").read_bytes()).hexdigest()
    assert actual == recorded, "v1 is frozen: regenerate into a new version directory instead"


def test_json_schema_file_is_in_sync() -> None:
    on_disk = json.loads((DATASET_DIR / "case.schema.json").read_text(encoding="utf-8"))
    assert on_disk == GoldenCase.model_json_schema()


def test_ids_are_unique(catalog: Catalog) -> None:
    assert len({c.case_id for c in CASES}) == len(CASES)
    assert len({d.document_id for c in CASES for d in c.documents}) == sum(
        len(c.documents) for c in CASES
    )


# --- the validator rejects broken cases ------------------------------------------------------


def _problems(case: GoldenCase, catalog: Catalog) -> list[str]:
    return validate_case(case, catalog)


def _first_planted(case: GoldenCase, role: PlantedRole) -> tuple[int, Planted]:
    return next((i, p) for i, p in enumerate(case.planted) if p.role is role)


def _replace_planted(case: GoldenCase, index: int, **changes: object) -> GoldenCase:
    planted = list(case.planted)
    planted[index] = planted[index].model_copy(update=changes)
    return case.model_copy(update={"planted": planted})


def test_rejects_a_quote_that_is_not_in_the_document(catalog: Catalog) -> None:
    case = CASES[0]
    i, _ = _first_planted(case, PlantedRole.TRUE)
    broken = _replace_planted(case, i, quote="this text is nowhere in the bundle")
    assert any("literally present" in p for p in _problems(broken, catalog))


def test_rejects_a_stale_expected_output(catalog: Catalog) -> None:
    case = next(c for c in CASES if c.expected.suggestions)
    expected = case.expected.model_copy(update={"suggestions": case.expected.suggestions[1:]})
    broken = case.model_copy(update={"expected": expected})
    assert any("expected output" in p for p in _problems(broken, catalog))


def test_rejects_a_true_planting_that_differs_from_the_record(catalog: Catalog) -> None:
    case = next(c for c in CASES if any(p.field == "policy_number" for p in c.planted))
    i = next(
        i
        for i, p in enumerate(case.planted)
        if p.field == "policy_number" and p.role is PlantedRole.TRUE
    )
    broken = _replace_planted(case, i, value="XX-0000")
    assert any("differs from the true record" in p for p in _problems(broken, catalog))


def test_rejects_unsorted_documents(catalog: Catalog) -> None:
    case = next(
        c
        for c in CASES
        if len(c.documents) > 2 and c.documents[0].document_date != c.documents[-1].document_date
    )
    broken = case.model_copy(update={"documents": list(reversed(case.documents))})
    assert any("chronological" in p for p in _problems(broken, catalog))


def test_rejects_a_conflicting_quote_with_a_different_date(catalog: Catalog) -> None:
    case = case_of(Tag.CONFLICT)
    _, planted = _first_planted(case, PlantedRole.CONFLICTING)
    docs = [
        d.model_copy(update={"document_date": d.document_date + timedelta(days=1)})
        if d.document_id == planted.document_id
        else d
        for d in case.documents
    ]
    broken = case.model_copy(update={"documents": docs})
    assert any(
        "dated like the true one" in p or "chronological" in p for p in _problems(broken, catalog)
    )


def test_rejects_a_superseded_quote_that_is_not_older(catalog: Catalog) -> None:
    case = next(c for c in CASES if any(p.role is PlantedRole.SUPERSEDED for p in c.planted))
    i, planted = _first_planted(case, PlantedRole.SUPERSEDED)
    true_doc = next(
        p.document_id
        for p in case.planted
        if p.field == planted.field and p.role is PlantedRole.TRUE
    )
    broken = _replace_planted(case, i, document_id=true_doc)
    assert any("superseded" in p for p in _problems(broken, catalog))


def test_rejects_a_wrong_current_kind(catalog: Catalog) -> None:
    case = next(
        c
        for c in CASES
        if c.mode is Mode.UPDATE and CurrentKind.CORRECT in c.current_kinds.values()
    )
    name = next(n for n, k in case.current_kinds.items() if k is CurrentKind.CORRECT)
    kinds = {**case.current_kinds, name: CurrentKind.WRONG}
    broken = case.model_copy(update={"current_kinds": kinds})
    assert any("does not match kind" in p for p in _problems(broken, catalog))


def test_rejects_current_values_in_extraction_mode(catalog: Catalog) -> None:
    case = next(c for c in CASES if c.mode is Mode.EXTRACTION)
    broken = case.model_copy(update={"current_values": {"policy_number": "X"}})
    assert any("extraction mode" in p for p in _problems(broken, catalog))


def test_rejects_unknown_fields(catalog: Catalog) -> None:
    case = CASES[0]
    broken = case.model_copy(update={"true_record": {**case.true_record, "nope": "x"}})
    assert any("nope" in p for p in _problems(broken, catalog))


def test_rejects_tags_that_do_not_match_the_content(catalog: Catalog) -> None:
    case = next(c for c in CASES if not c.tags)
    broken = case.model_copy(update={"tags": [Tag.CONFLICT]})
    assert any("conflict" in p for p in _problems(broken, catalog))
    case = case_of(Tag.CONFLICT)
    broken = case.model_copy(update={"tags": [t for t in case.tags if t is not Tag.CONFLICT]})
    assert any("conflict" in p for p in _problems(broken, catalog))


def test_rejects_duplicate_case_ids_and_document_ids(catalog: Catalog) -> None:
    problems = validate_dataset([CASES[0], CASES[0]], catalog)
    assert any("duplicate case_id" in p for p in problems)
    assert any("used more than once" in p for p in problems)


def test_coverage_reports_missing_targets(catalog: Catalog) -> None:
    few = [c for c in CASES if Tag.CONFLICT not in c.tags][:10]
    problems = coverage_problems(few, catalog)
    assert any(p.startswith("cases: 10, need") for p in problems)
    assert any(p.startswith("tag conflict") for p in problems)
    strict = Targets(min_cases=10_000)
    assert coverage_problems(CASES, catalog, strict)


def test_jsonl_lines_round_trip() -> None:
    for line in Path(DEFAULT_PATH).read_text(encoding="utf-8").splitlines():
        case = GoldenCase.model_validate_json(line)
        assert json.loads(case.model_dump_json()) == json.loads(line)


def test_document_dates_are_plausible() -> None:
    for case in CASES:
        for d in case.documents:
            assert date(2020, 1, 1) <= d.document_date <= date(2030, 1, 1)
