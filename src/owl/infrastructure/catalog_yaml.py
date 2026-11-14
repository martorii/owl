"""YAML loader for the field catalog. Reports every problem at once as a ``CatalogError``."""

from collections import Counter
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from owl.domain.catalog import Applicability, Catalog, CatalogError, FieldDefinition
from owl.domain.taxonomy import BusinessType, DocumentType, Lob
from owl.domain.values import FieldType

_TOP_KEYS = {"version", "fields"}
_REQUIRED = ("name", "type", "description", "applies_to", "group")
_OPTIONAL = ("item_type", "ordered", "allowed_values", "preferred_document_types")


def load_catalog(path: Path) -> Catalog:
    return parse_catalog(path.read_text(encoding="utf-8"))


def parse_catalog(text: str) -> Catalog:
    problems: list[str] = []
    try:
        document: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError([f"invalid YAML: {exc}"]) from exc
    if not isinstance(document, dict):
        raise CatalogError(["the document must be a mapping with 'version' and 'fields'"])
    top: dict[str, Any] = document

    problems.extend(f"unknown top-level key {k!r}" for k in top if k not in _TOP_KEYS)
    version = top.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        problems.append(f"version must be a positive integer, got {version!r}")
        version = 0
    raw_fields = top.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise CatalogError([*problems, "'fields' must be a non-empty list"])

    definitions: list[FieldDefinition] = []
    names: list[str] = []
    for index, raw in enumerate(raw_fields):
        label = f"fields[{index}]"
        if isinstance(raw, dict) and isinstance(raw.get("name"), str):
            label += f" ({raw['name']})"
            names.append(raw["name"])
        built = _build_field(raw, label, problems)
        if built is not None:
            definitions.append(built)

    problems.extend(
        f"duplicate field name {name!r}" for name, count in Counter(names).items() if count > 1
    )
    if problems:
        raise CatalogError(problems)
    return Catalog(version=version, fields=tuple(definitions))


def _build_field(raw: Any, label: str, problems: list[str]) -> FieldDefinition | None:
    if not isinstance(raw, dict):
        problems.append(f"{label}: must be a mapping")
        return None
    entry: dict[str, Any] = raw
    before = len(problems)

    def problem(message: str) -> None:
        problems.append(f"{label}: {message}")

    for key in entry:
        if key not in _REQUIRED and key not in _OPTIONAL:
            problem(f"unknown key {key!r}")
    for key in _REQUIRED:
        if key not in entry:
            problem(f"missing required key {key!r}")

    name = _text(entry, "name", problem)
    description = _text(entry, "description", problem)
    group = _text(entry, "group", problem)
    field_type = _enum(entry.get("type"), FieldType, "type", problem) if "type" in entry else None
    item_type = (
        _enum(entry["item_type"], FieldType, "item_type", problem)
        if entry.get("item_type") is not None
        else None
    )
    ordered = entry.get("ordered", False)
    if ordered is None:
        ordered = False
    if not isinstance(ordered, bool):
        problem(f"ordered must be true or false, got {ordered!r}")
        ordered = False
    allowed = _allowed_values(entry.get("allowed_values"), problem)
    doc_types = _list_of(
        entry.get("preferred_document_types"), DocumentType, "document type", problem
    )
    applies_to = _applicability(entry.get("applies_to"), problem)

    if len(problems) > before or None in (name, description, group, field_type, applies_to):
        return None
    assert name is not None
    assert description is not None
    assert group is not None
    assert field_type is not None
    assert applies_to is not None
    try:
        return FieldDefinition(
            name=name,
            type=field_type,
            description=description,
            applies_to=applies_to,
            group=group,
            preferred_document_types=tuple(doc_types),
            item_type=item_type,
            ordered=ordered,
            allowed_values=allowed,
        )
    except ValueError as exc:
        problem(str(exc))
        return None


def _text(entry: dict[str, Any], key: str, problem: Callable[[str], None]) -> str | None:
    if key not in entry:
        return None
    value = entry[key]
    if not isinstance(value, str) or not value.strip():
        problem(f"{key} must be a non-empty string")
        return None
    return value.strip() if key == "name" else value


def _enum[E: StrEnum](
    value: Any, enum: type[E], key: str, problem: Callable[[str], None]
) -> E | None:
    try:
        return enum(value)
    except ValueError:
        options = ", ".join(e.value for e in enum)
        problem(f"unknown {key} {value!r} (expected one of: {options})")
        return None


def _list_of[E: StrEnum](
    value: Any, enum: type[E], what: str, problem: Callable[[str], None]
) -> list[E]:
    if value is None:
        return []
    if not isinstance(value, list):
        problem(f"{what}s must be a list")
        return []
    result: list[E] = []
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        parsed = _enum(item, enum, what, problem)
        if parsed is not None:
            result.append(parsed)
    return result


def _allowed_values(value: Any, problem: Callable[[str], None]) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):  # pyright: ignore[reportUnknownVariableType]
        problem("allowed_values must be a list of strings")
        return None
    return tuple(value)  # pyright: ignore[reportUnknownArgumentType]


def _applicability(value: Any, problem: Callable[[str], None]) -> Applicability | None:
    if not isinstance(value, dict):
        problem("applies_to must be a mapping with 'lob' and 'business_type'")
        return None
    mapping: dict[str, Any] = value
    for key in mapping:
        if key not in ("lob", "business_type"):
            problem(f"applies_to: unknown key {key!r}")
    lobs = _list_of(mapping.get("lob"), Lob, "lob", problem)
    business_types = _list_of(mapping.get("business_type"), BusinessType, "business_type", problem)
    if not lobs or not business_types:
        problem("applies_to needs at least one lob and one business_type")
        return None
    return Applicability(frozenset(lobs), frozenset(business_types))
