"""
debug_claim_extractor.py
========================
Alternative ingestion pipeline: JSON-first extraction + deterministic merge.

Step 1 — Per-document extraction
    For each document in ``cases/<claim_id>/``, call the LLM with the
    ``claim_extraction`` prompt and save the structured result to
    ``debug/processed2/<claim_id>/<doc_name>_extraction.json``.

Step 2 — Deterministic merge
    Walk all per-document extractions, score each candidate value per field
    by recency (later doc → higher weight) and frequency (more docs agree →
    higher weight), and pick the winner.  Output is saved as a flat list of
    field records to ``debug/processed2/<claim_id>/final_claim.json``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

# Ensure the project root is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathlib import Path

from src.tools.knowledge_base.claim_fields import FIELD_REGISTRY
from src.tools.llm.generator import LLMGenerator, get_generator
from src.tools.parser.models import Document
from src.tools.parser.parser import SUPPORTED_EXTENSIONS, parse_document
from src.utils.logger import get_logger

log = get_logger(__name__)

# ── Output directory (sibling of cases/, inside debug/) ──────────────────────
_DEBUG_DIR = Path(__file__).parent
_PROCESSED2_DIR = _DEBUG_DIR / "processed2"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_document(doc_path: Path, table_format: str = "markdown") -> str | None:
    """Return the text content of a document, or None on failure."""
    try:
        with open(doc_path, "rb") as f:
            data = f.read()
        ext = doc_path.suffix.lstrip(".").lower()
        doc = Document(filename=doc_path.name, extension=ext, data=data)
        parse_document(doc, table_format=table_format)
        content = doc.get_content(add_page_numbers=True)
        if not content.strip():
            log.warning("  No content extracted from %s — skipping.", doc_path.name)
            return None
        return content
    except Exception as exc:
        log.error("  Failed to parse %s: %s", doc_path.name, exc)
        return None


def _extract_json_from_response(raw: str) -> dict:
    """
    Try to parse the LLM response as JSON.
    The model might wrap it in ```json ... ``` fences — strip them first.
    """
    # Strip optional markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("  Failed to parse LLM JSON response: %s", exc)
        log.debug("  Raw response was:\n%s", raw)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Per-document extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_document(
    doc_path: Path,
    llm: LLMGenerator,
    output_dir: Path,
) -> dict | None:
    """
    Parse *doc_path*, call the LLM extraction prompt, save the result as JSON
    and return the parsed dict.  Returns None on any failure.
    """
    log.info("  Parsing document: %s", doc_path.name)
    doc_content = _parse_document(doc_path)
    if doc_content is None:
        return None

    log.info("  Calling LLM for extraction: %s", doc_path.name)
    try:
        raw = llm.generate_from_template(
            template_name="claim_extraction",
            variables={"document_content": doc_content},
            system_prompt=(
                "You are an experienced insurance claims analyst. "
                "Extract claim data from documents and return strictly valid JSON. "
                "Never add explanations or markdown outside of the JSON object."
            ),
            temperature=0.1,
        )
    except Exception as exc:
        log.error("  LLM call failed for %s: %s", doc_path.name, exc)
        return None

    try:
        extraction = _extract_json_from_response(raw)
    except Exception:
        return None

    # Add document name as metadata
    extraction["_document"] = doc_path.name

    # Save to disk
    out_path = output_dir / f"{doc_path.stem}_extraction.json"
    out_path.write_text(json.dumps(extraction, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("  ✓ Saved extraction → %s", out_path.name)

    return extraction


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Deterministic merge
# ─────────────────────────────────────────────────────────────────────────────

# Weights for the confidence score  (must sum to 1.0)
_W_RECENCY = 0.6
_W_FREQUENCY = 0.4


def _normalize_for_field(field_path: str, val: object) -> str:
    """
    Normalize *val* using the registered :class:`FieldType` for *field_path*.

    Falls back to ``str.strip().lower()`` for unknown fields so the merger
    degrades gracefully when a field is not yet in the registry.
    """
    if val is None:
        return ""
    field = FIELD_REGISTRY.get(field_path)
    if field is not None:
        normalised = field.normalize(str(val))
        return (normalised or "").lower()
    # Fallback for unregistered fields
    return str(val).strip().lower()


def _walk_leaf_fields(
    obj: dict,
    prefix: str = "",
) -> list[tuple[str, object, int | None]]:
    """
    Recursively walk an extraction dict and yield
    ``(field_path, reason, value, page)`` tuples for every leaf node.

    Leaf nodes are either:
    - A dict with keys ``{"reason", "value", "_page"}``.
    - A bare ``null`` value (shorthand used by some LLM outputs).

    ``payments_made`` lists are skipped here and handled separately.
    Internal ``_*`` keys are ignored.
    """
    results: list[tuple[str, str | None, object, int | None]] = []
    if not isinstance(obj, dict):
        return results

    for key, val in obj.items():
        if key.startswith("_") or key == "payments_made":
            continue

        child_path = f"{prefix}.{key}" if prefix else key

        if isinstance(val, dict) and "value" in val and "_page" in val:
            # Proper leaf
            results.append((child_path, val.get("reason"), val["value"], val["_page"]))
        elif val is None:
            # Bare null shorthand
            results.append((child_path, None, None, None))
        elif isinstance(val, dict):
            results.extend(_walk_leaf_fields(val, child_path))
        # Any other type (unexpected) is silently skipped

    return results


def merge_extractions_deterministic(
    extractions: list[dict],
    output_dir: Path,
) -> list[dict] | None:
    """
    Deterministically merge per-document extractions into a flat list of
    field records.

    Each record contains:
    - ``field_name``       — dot-notation path (e.g. ``"general.cause_of_loss.peril_cause"``).
    - ``field_value``      — the winning value (original casing from the latest source doc).
    - ``source_documents`` — documents that contained the winning value, in doc order.
    - ``pages``            — corresponding page numbers (parallel list to source_documents).
    - ``confidence``       — float in [0, 1] combining recency and frequency.

    Confidence formula
    ------------------
    For each unique (normalised) value candidate for a field::

        recency_score   = latest_doc_index / (n_docs - 1)   # 1.0 for the last doc
        frequency_score = n_docs_with_value / n_docs_total  # 1.0 if all docs agree
        confidence      = W_RECENCY * recency_score + W_FREQUENCY * frequency_score

    The candidate with the highest confidence wins.  When all docs are null,
    confidence is 0.0.
    """
    if not extractions:
        return None

    n_docs = len(extractions)

    # ── Collect leaf observations per field path ──────────────────────────────
    # field_path -> [(doc_idx, doc_name, reason, value, page), ...] — nulls excluded
    observations: dict[str, list[tuple[int, str, str | None, object, int | None]]] = defaultdict(list)
    all_field_paths: set[str] = set()

    for doc_idx, ext in enumerate(extractions):
        doc_name = ext.get("_document", f"doc_{doc_idx}")
        payload = {k: v for k, v in ext.items() if k != "_document"}
        for field_path, reason, value, page in _walk_leaf_fields(payload):
            all_field_paths.add(field_path)
            if value is not None:
                observations[field_path].append((doc_idx, doc_name, reason, value, page))

    # ── Score and pick winner per field ──────────────────────────────────────
    merged_fields: list[dict] = []

    for field_path in sorted(all_field_paths):
        obs = observations.get(field_path, [])

        if not obs:
            merged_fields.append(
                {
                    "field_name": field_path,
                    "field_value": None,
                    "source_documents": [],
                    "pages": [],
                    "confidence": 0.0,
                }
            )
            continue

        # Group by type-aware normalised value
        value_groups: dict[str, list[tuple[int, str, str | None, object, int | None]]] = defaultdict(list)
        for entry in obs:
            norm_key = _normalize_for_field(field_path, entry[3])
            value_groups[norm_key].append(entry)

        # Compute confidence for each candidate
        best_norm: str | None = None
        best_confidence = -1.0

        for norm_val, group in value_groups.items():
            latest_doc_idx = max(g[0] for g in group)
            recency_score = latest_doc_idx / (n_docs - 1) if n_docs > 1 else 1.0
            frequency_score = len(group) / n_docs
            confidence = _W_RECENCY * recency_score + _W_FREQUENCY * frequency_score
            if confidence > best_confidence:
                best_confidence = confidence
                best_norm = norm_val

        # Reconstruct result from winning group
        winning_group = sorted(value_groups[best_norm], key=lambda g: g[0])  # type: ignore[index]
        # Use the original value string from the *latest* occurrence
        winning_value = winning_group[-1][3]
        winning_reason = winning_group[-1][2]

        source_documents = [g[1] for g in winning_group]
        pages = [g[4] for g in winning_group]

        merged_fields.append(
            {
                "field_name": field_path,
                "reason": winning_reason,
                "field_value": winning_value,
                "source_documents": source_documents,
                "pages": pages,
                "confidence": round(best_confidence, 3),
            }
        )

    # ── payments_made — union by (date, amount) ───────────────────────────────
    seen_payments: set[tuple[str, str]] = set()
    merged_payments: list[dict] = []

    for ext in extractions:
        doc_name = ext.get("_document", "unknown")
        for payment in (ext.get("financials") or {}).get("payments_made") or []:
            key = (_normalize(payment.get("date")), _normalize(payment.get("amount")))
            if key not in seen_payments:
                seen_payments.add(key)
                merged_payments.append({**payment, "_source_document": doc_name})

    if merged_payments:
        merged_fields.append(
            {
                "field_name": "financials.payments_made",
                "field_value": merged_payments,
                "source_documents": list({p["_source_document"] for p in merged_payments}),
                "pages": [],
                "confidence": 1.0,
            }
        )

    # ── Persist ───────────────────────────────────────────────────────────────
    out_path = output_dir / "final_claim.json"
    out_path.write_text(json.dumps(merged_fields, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("✓ Deterministic merge saved → %s (%d fields)", out_path, len(merged_fields))

    return merged_fields


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    print("\n" + "=" * 60)
    print("CLAIM EXTRACTION PIPELINE  (JSON-first)")
    print("=" * 60 + "\n")

    # ── Configure ──────────────────────────────────────────────────
    claim_id = "1"  # Change this to test different claims
    claim_dir = _DEBUG_DIR / "cases" / claim_id

    if not claim_dir.exists():
        print(f"❌ Claim folder not found: {claim_dir}")
        print("   Create it and place supported documents (.pdf, .docx) inside.")
        return

    # Output directory
    output_dir = _PROCESSED2_DIR / claim_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # LLM backend
    llm = get_generator("lmstudio")

    # ── Step 1: Per-document extraction ────────────────────────────
    doc_paths = sorted(
        [
            f
            for f in claim_dir.iterdir()
            if f.is_file() and f.suffix.lstrip(".").lower() in SUPPORTED_EXTENSIONS
        ],
        key=lambda p: p.name.lower(),
    )

    if not doc_paths:
        print(f"❌ No supported documents found in: {claim_dir}")
        return

    print(f"Found {len(doc_paths)} document(s). Starting extraction …\n")

    extractions: list[dict] = []
    for idx, doc_path in enumerate(doc_paths, 1):
        print(f"[{idx}/{len(doc_paths)}] {doc_path.name}")
        result = extract_document(doc_path, llm, output_dir)
        if result is not None:
            extractions.append(result)
        else:
            print(f"  ⚠️  Skipped (extraction failed).")

    if not extractions:
        print("\n❌ No successful extractions — aborting merge step.")
        return

    print(f"\n✅ Extraction complete: {len(extractions)}/{len(doc_paths)} documents succeeded.")

    # ── Step 2: Deterministic merge ─────────────────────────────────
    print("\nRunning deterministic merge …")
    merged = merge_extractions_deterministic(extractions, output_dir)

    if merged is None:
        print("\n❌ Merge step failed.")
    else:
        final_path = output_dir / "final_claim.json"
        print(f"\n✅ Pipeline complete. Output directory:\n   {output_dir}\n")
        for f in sorted(output_dir.iterdir()):
            print(f"   📄 {f.name:40s} {f.stat().st_size:>8,} bytes")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
