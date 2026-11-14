from pathlib import Path

import pytest
import yaml

from owl.domain.catalog import CatalogError
from owl.domain.taxonomy import BusinessType, Lob
from owl.domain.values import FieldType
from owl.infrastructure.catalog_yaml import load_catalog, parse_catalog

SHIPPED = Path(__file__).resolve().parents[3] / "catalog" / "fields.yaml"


def raw_field(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "policy_number",
        "type": "string",
        "description": "The policy number.",
        "applies_to": {"lob": ["property"], "business_type": ["direct"]},
        "group": "policy_terms",
        "preferred_document_types": ["contract"],
    }
    base.update(overrides)
    return base


def doc(*fields: dict[str, object], version: object = 1) -> str:
    return yaml.safe_dump({"version": version, "fields": list(fields)})


def problems(text: str) -> list[str]:
    with pytest.raises(CatalogError) as exc:
        parse_catalog(text)
    return list(exc.value.problems)


def test_parses_a_valid_catalog() -> None:
    catalog = parse_catalog(
        doc(
            raw_field(),
            raw_field(
                name="perils",
                type="list",
                item_type="enum",
                ordered=False,
                allowed_values=["fire", "flood"],
            ),
        )
    )
    assert catalog.version == 1
    assert [f.name for f in catalog.fields] == ["policy_number", "perils"]
    perils = catalog.fields[1]
    assert perils.type is FieldType.LIST
    assert perils.item_type is FieldType.ENUM
    assert perils.allowed_values == ("fire", "flood")
    assert catalog.fields[0].preferred_document_types[0].value == "contract"


def test_optional_keys_may_be_omitted_or_null() -> None:
    minimal = raw_field()
    del minimal["preferred_document_types"]
    parse_catalog(doc(minimal, raw_field(name="other", item_type=None, allowed_values=None)))


def test_rejects_duplicate_names() -> None:
    assert any("duplicate" in p for p in problems(doc(raw_field(), raw_field())))


def test_rejects_list_without_item_type() -> None:
    assert any("item_type" in p for p in problems(doc(raw_field(type="list"))))


def test_rejects_enum_without_allowed_values() -> None:
    assert any("allowed_values" in p for p in problems(doc(raw_field(type="enum"))))


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"type": "object"}, "type"),
        ({"applies_to": {"lob": ["marine"], "business_type": ["direct"]}}, "marine"),
        ({"applies_to": {"lob": ["property"], "business_type": ["facultative"]}}, "facultative"),
        ({"preferred_document_types": ["schedule"]}, "schedule"),
        ({"applies_to": {"lob": [], "business_type": ["direct"]}}, "applies_to"),
        ({"name": "BadName"}, "snake_case"),
        ({"surprise": 1}, "surprise"),
        ({"description": None}, "description"),
        ({"group": None}, "group"),
        ({"applies_to": None}, "applies_to"),
    ],
)
def test_rejects_invalid_field(override: dict[str, object], needle: str) -> None:
    assert any(needle in p for p in problems(doc(raw_field(**override))))


def test_rejects_missing_required_keys() -> None:
    incomplete = raw_field()
    del incomplete["group"]
    assert any("group" in p for p in problems(doc(incomplete)))


@pytest.mark.parametrize("version", [0, -1, "2026-11-13.1", 1.5, True, None])
def test_rejects_bad_version(version: object) -> None:
    assert any("version" in p for p in problems(doc(raw_field(), version=version)))


@pytest.mark.parametrize(
    "text",
    ["", "- a\n- b\n", "version: 1\n", "version: 1\nfields: []\n", "version: 1\nfields: x\n"],
)
def test_rejects_malformed_documents(text: str) -> None:
    assert problems(text)


def test_rejects_unknown_top_level_keys() -> None:
    text = yaml.safe_dump({"version": 1, "fields": [raw_field()], "extra": 1})
    assert any("extra" in p for p in problems(text))


def test_rejects_invalid_yaml() -> None:
    assert problems("version: [1\n")


def test_reports_every_problem_at_once() -> None:
    found = problems(
        doc(
            raw_field(type="object"),
            raw_field(name="b", type="list"),
            raw_field(name="c", type="enum"),
            version=0,
        )
    )
    assert len(found) >= 4


def test_load_catalog_from_path(tmp_path: Path) -> None:
    path = tmp_path / "fields.yaml"
    path.write_text(doc(raw_field()), encoding="utf-8")
    assert load_catalog(path).version == 1


# --- the shipped catalog ---------------------------------------------------------------------

COMBINATIONS = [(lob, bt) for lob in Lob for bt in BusinessType]


def test_shipped_catalog_loads() -> None:
    catalog = load_catalog(SHIPPED)
    assert catalog.version >= 1
    assert 25 <= len(catalog.fields) <= 45


def test_shipped_catalog_uses_every_field_type() -> None:
    catalog = load_catalog(SHIPPED)
    assert {f.type for f in catalog.fields} == set(FieldType)
    assert any(f.type is FieldType.LIST and f.ordered for f in catalog.fields)
    assert any(f.type is FieldType.LIST and not f.ordered for f in catalog.fields)


@pytest.mark.parametrize(("lob", "business_type"), COMBINATIONS)
def test_shipped_catalog_covers_each_combination(lob: Lob, business_type: BusinessType) -> None:
    active = load_catalog(SHIPPED).active_fields(lob, business_type)
    assert len(active) >= 15
    assert len({a.definition.group for a in active}) >= 4


def test_shipped_catalog_combinations_differ() -> None:
    catalog = load_catalog(SHIPPED)
    names = {
        combo: frozenset(a.definition.name for a in catalog.active_fields(*combo))
        for combo in COMBINATIONS
    }
    assert len(set(names.values())) == len(COMBINATIONS)


def test_shipped_catalog_groups_match_contract() -> None:
    catalog = load_catalog(SHIPPED)
    assert {f.group for f in catalog.fields} == {
        "parties",
        "policy_terms",
        "financials",
        "property_risk",
        "casualty_risk",
        "claims",
    }
