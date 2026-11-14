"""The field catalog (CONTRACT §3): which fields exist and which are active for a request."""

import re
from dataclasses import dataclass

from owl.domain.taxonomy import BusinessType, DocumentType, Lob
from owl.domain.values import FieldType, ValueSpec

_SNAKE_CASE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


class CatalogError(ValueError):
    """A malformed catalog. ``problems`` lists every issue found, not just the first."""

    def __init__(self, problems: list[str] | tuple[str, ...]) -> None:
        self.problems = tuple(problems)
        super().__init__("invalid catalog:\n- " + "\n- ".join(self.problems))


@dataclass(frozen=True)
class Applicability:
    lob: frozenset[Lob]
    business_type: frozenset[BusinessType]

    def matches(self, lob: Lob, business_type: BusinessType) -> bool:
        return lob in self.lob and business_type in self.business_type


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    type: FieldType
    description: str
    applies_to: Applicability
    group: str
    preferred_document_types: tuple[DocumentType, ...] = ()
    item_type: FieldType | None = None  # list only
    ordered: bool = False  # list only
    allowed_values: tuple[str, ...] | None = None  # enum, or list of enums

    def __post_init__(self) -> None:
        if not _SNAKE_CASE.fullmatch(self.name):
            raise ValueError(f"field name {self.name!r} must be snake_case")
        if not self.description.strip():
            raise ValueError(f"{self.name}: description must not be blank")
        if not self.group.strip():
            raise ValueError(f"{self.name}: group must not be blank")
        if not self.applies_to.lob or not self.applies_to.business_type:
            raise ValueError(f"{self.name}: applies_to needs at least one lob and business_type")
        is_list = self.type is FieldType.LIST
        if is_list and self.item_type is None:
            raise ValueError(f"{self.name}: a list field needs an item_type")
        if is_list and self.item_type is FieldType.LIST:
            raise ValueError(f"{self.name}: nested lists are not supported")
        if not is_list and self.item_type is not None:
            raise ValueError(f"{self.name}: item_type is only valid for list fields")
        if not is_list and self.ordered:
            raise ValueError(f"{self.name}: ordered is only valid for list fields")
        has_enum = self.type is FieldType.ENUM or self.item_type is FieldType.ENUM
        if has_enum and not self.allowed_values:
            raise ValueError(f"{self.name}: an enum field needs allowed_values")
        if not has_enum and self.allowed_values is not None:
            raise ValueError(f"{self.name}: allowed_values is only valid for enum fields")
        if self.allowed_values is not None:
            folded = {v.casefold() for v in self.allowed_values}
            if len(folded) != len(self.allowed_values) or "" in folded:
                raise ValueError(f"{self.name}: allowed_values must be distinct, non-empty")

    @property
    def value_spec(self) -> ValueSpec:
        return ValueSpec(
            self.type,
            allowed_values=self.allowed_values,
            item_type=self.item_type,
            ordered=self.ordered,
        )


@dataclass(frozen=True)
class ActiveField:
    """A field active for one request, with its per-request short ID (``F1``, ``F2``, ...)."""

    short_id: str
    definition: FieldDefinition


@dataclass(frozen=True)
class FieldGroup:
    name: str
    fields: tuple[ActiveField, ...]


@dataclass(frozen=True)
class Catalog:
    version: int
    fields: tuple[FieldDefinition, ...]

    def __post_init__(self) -> None:
        problems: list[str] = []
        if isinstance(self.version, bool) or self.version < 1:
            problems.append("version must be a positive integer")
        if not self.fields:
            problems.append("the catalog needs at least one field")
        seen: set[str] = set()
        for definition in self.fields:
            if definition.name in seen:
                problems.append(f"duplicate field name {definition.name!r}")
            seen.add(definition.name)
        if problems:
            raise CatalogError(problems)

    def get(self, name: str) -> FieldDefinition | None:
        return next((f for f in self.fields if f.name == name), None)

    def active_fields(self, lob: Lob, business_type: BusinessType) -> tuple[ActiveField, ...]:
        """Fields applicable to the request, in catalog order, with short IDs ``F1…Fn``."""
        matching = (f for f in self.fields if f.applies_to.matches(lob, business_type))
        return tuple(ActiveField(f"F{i}", f) for i, f in enumerate(matching, start=1))


def group_fields(active: tuple[ActiveField, ...]) -> tuple[FieldGroup, ...]:
    """Group active fields by ``group``, ordered by first appearance in catalog order."""
    grouped: dict[str, list[ActiveField]] = {}
    for item in active:
        grouped.setdefault(item.definition.group, []).append(item)
    return tuple(FieldGroup(name, tuple(items)) for name, items in grouped.items())
