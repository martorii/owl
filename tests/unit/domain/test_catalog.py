import pytest

from owl.domain.catalog import (
    Applicability,
    Catalog,
    CatalogError,
    FieldDefinition,
    group_fields,
)
from owl.domain.taxonomy import BusinessType, DocumentType, Lob
from owl.domain.values import FieldType, ValueSpec

ALL = Applicability(frozenset(Lob), frozenset(BusinessType))


def field(
    name: str = "some_field",
    type: FieldType = FieldType.STRING,
    group: str = "g",
    applies_to: Applicability = ALL,
    **extra: object,
) -> FieldDefinition:
    return FieldDefinition(
        name=name,
        type=type,
        description="A field.",
        applies_to=applies_to,
        group=group,
        **extra,  # type: ignore[arg-type]
    )


def only(lob: Lob, business_type: BusinessType) -> Applicability:
    return Applicability(frozenset({lob}), frozenset({business_type}))


# --- FieldDefinition invariants --------------------------------------------------------------


@pytest.mark.parametrize("name", ["Bad", "1abc", "with space", "double__underscore", "", "a-b"])
def test_rejects_non_snake_case_names(name: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        field(name=name)


def test_list_needs_item_type() -> None:
    with pytest.raises(ValueError, match="item_type"):
        field(type=FieldType.LIST)
    with pytest.raises(ValueError, match="nested"):
        field(type=FieldType.LIST, item_type=FieldType.LIST)


def test_item_type_only_for_lists() -> None:
    with pytest.raises(ValueError, match="item_type"):
        field(type=FieldType.STRING, item_type=FieldType.STRING)


def test_ordered_only_for_lists() -> None:
    with pytest.raises(ValueError, match="ordered"):
        field(ordered=True)


def test_enum_needs_allowed_values() -> None:
    with pytest.raises(ValueError, match="allowed_values"):
        field(type=FieldType.ENUM)
    with pytest.raises(ValueError, match="allowed_values"):
        field(type=FieldType.LIST, item_type=FieldType.ENUM)


def test_allowed_values_only_for_enums() -> None:
    with pytest.raises(ValueError, match="allowed_values"):
        field(type=FieldType.STRING, allowed_values=("a",))


def test_allowed_values_must_be_distinct() -> None:
    with pytest.raises(ValueError, match="distinct"):
        field(type=FieldType.ENUM, allowed_values=("A", "a"))


def test_empty_applicability_rejected() -> None:
    with pytest.raises(ValueError, match="applies_to"):
        field(applies_to=Applicability(frozenset(), frozenset(BusinessType)))
    with pytest.raises(ValueError, match="applies_to"):
        field(applies_to=Applicability(frozenset(Lob), frozenset()))


def test_blank_description_or_group_rejected() -> None:
    with pytest.raises(ValueError, match="description"):
        FieldDefinition(name="x", type=FieldType.STRING, description=" ", applies_to=ALL, group="g")
    with pytest.raises(ValueError, match="group"):
        field(group="")


def test_value_spec() -> None:
    f = field(
        type=FieldType.LIST,
        item_type=FieldType.ENUM,
        allowed_values=("a", "b"),
        ordered=True,
    )
    assert f.value_spec == ValueSpec(
        FieldType.LIST, allowed_values=("a", "b"), item_type=FieldType.ENUM, ordered=True
    )


def test_preferred_document_types_default_empty() -> None:
    assert field().preferred_document_types == ()
    f = field(preferred_document_types=(DocumentType.EMAIL,))
    assert f.preferred_document_types == (DocumentType.EMAIL,)


# --- Catalog ---------------------------------------------------------------------------------


def test_catalog_rejects_duplicates_and_bad_version() -> None:
    with pytest.raises(CatalogError, match="duplicate"):
        Catalog(version=1, fields=(field(name="a"), field(name="a")))
    for bad in (0, -1):
        with pytest.raises(CatalogError, match="version"):
            Catalog(version=bad, fields=(field(),))
    with pytest.raises(CatalogError, match="at least one"):
        Catalog(version=1, fields=())


def make_catalog() -> Catalog:
    return Catalog(
        version=3,
        fields=(
            field("a", group="parties"),
            field("b", group="terms", applies_to=only(Lob.PROPERTY, BusinessType.DIRECT)),
            field("c", group="parties", applies_to=only(Lob.CASUALTY, BusinessType.DIRECT)),
            field("d", group="terms", applies_to=ALL),
            field("e", group="risk", applies_to=only(Lob.PROPERTY, BusinessType.REINSURANCE)),
        ),
    )


@pytest.mark.parametrize(
    ("lob", "business_type", "expected"),
    [
        (Lob.PROPERTY, BusinessType.DIRECT, ["a", "b", "d"]),
        (Lob.CASUALTY, BusinessType.DIRECT, ["a", "c", "d"]),
        (Lob.PROPERTY, BusinessType.REINSURANCE, ["a", "d", "e"]),
        (Lob.CASUALTY, BusinessType.REINSURANCE, ["a", "d"]),
    ],
)
def test_active_fields_per_combination(
    lob: Lob, business_type: BusinessType, expected: list[str]
) -> None:
    active = make_catalog().active_fields(lob, business_type)
    assert [a.definition.name for a in active] == expected


def test_short_ids_follow_catalog_order_within_active_set() -> None:
    active = make_catalog().active_fields(Lob.PROPERTY, BusinessType.REINSURANCE)
    assert [(a.short_id, a.definition.name) for a in active] == [
        ("F1", "a"),
        ("F2", "d"),
        ("F3", "e"),
    ]


def test_groups_are_stable_and_ordered_by_first_appearance() -> None:
    active = make_catalog().active_fields(Lob.PROPERTY, BusinessType.DIRECT)
    groups = group_fields(active)
    assert [g.name for g in groups] == ["parties", "terms"]
    assert [[a.definition.name for a in g.fields] for g in groups] == [["a"], ["b", "d"]]
    # short IDs are those of the whole active set, not renumbered per group
    assert [a.short_id for g in groups for a in g.fields] == ["F1", "F2", "F3"]
    assert group_fields(active) == groups


def test_lookup_by_name() -> None:
    catalog = make_catalog()
    assert catalog.get("c") is not None
    assert catalog.get("zzz") is None
    assert catalog.version == 3
