from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.knowledge_base.ingester import ClaimIngester
from src.tools.parser.models import BoundingBox, TextChunk

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_claim_dir(tmp_path: Path) -> Path:
    """Temporary claim directory: tmp/claims/C001/"""
    claim_dir = tmp_path / "claims" / "C001"
    claim_dir.mkdir(parents=True)
    return claim_dir


@pytest.fixture
def mock_llm() -> MagicMock:
    return MagicMock()


@pytest.fixture
def ingester(tmp_claim_dir: Path, mock_llm: MagicMock) -> ClaimIngester:
    return ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)


def _good_llm_response(diary: str = "# Diary\nUpdated.") -> str:
    """Helper: build a well-formed LLM response."""
    return (
        f"<!-- BEGIN_DIARY -->\n{diary}\n<!-- END_DIARY -->\n\n"
        "<!-- BEGIN_LEDGER_ENTRY -->\n"
        "| 1 | — | C001 | Initialise Claim ID |\n"
        "<!-- END_LEDGER_ENTRY -->\n"
    )


# ─── _parse_llm_response ──────────────────────────────────────────────────────


def test_parse_response_all_sections() -> None:
    raw = (
        "Preamble text.\n"
        "<!-- BEGIN_DIARY -->\n# Claim Diary\nUpdated content.\n<!-- END_DIARY -->\n"
        "<!-- BEGIN_LEDGER_ENTRY -->\n| — | C001 | Reason text |\n<!-- END_LEDGER_ENTRY -->\n"
    )
    diary, ledger = ClaimIngester._parse_llm_response(raw)

    assert "# Claim Diary" in diary
    assert "| — | C001 | Reason text |" in ledger


def test_parse_response_empty_body_sections() -> None:
    raw = (
        "<!-- BEGIN_DIARY -->\n# Diary\n<!-- END_DIARY -->\n"
        "<!-- BEGIN_LEDGER_ENTRY -->\n<!-- END_LEDGER_ENTRY -->\n"
    )
    diary, ledger = ClaimIngester._parse_llm_response(raw)

    assert diary == "# Diary"
    assert ledger == ""


def test_parse_response_missing_sections() -> None:
    diary, ledger = ClaimIngester._parse_llm_response("No sections here.")

    assert diary == ""
    assert ledger == ""


def test_parse_response_case_insensitive() -> None:
    raw = "<!-- BEGIN_diary -->\nContent\n<!-- END_diary -->"
    diary, _ = ClaimIngester._parse_llm_response(raw)
    assert diary == "Content"


def test_parse_response_extra_whitespace_in_delimiters() -> None:
    raw = "<!--  BEGIN_DIARY  -->\nHello\n<!--  END_DIARY  -->"
    diary, _ = ClaimIngester._parse_llm_response(raw)
    assert diary == "Hello"


# ─── _init_files ──────────────────────────────────────────────────────────────


def test_init_files_creates_from_templates(ingester: ClaimIngester) -> None:
    ingester.processed_dir.mkdir(parents=True, exist_ok=True)
    ingester._init_files()

    assert ingester.diary_path.exists()
    assert ingester.ledger_path.exists()

    assert "# Claim Diary" in ingester.diary_path.read_text()
    assert "# Processing Ledger" in ingester.ledger_path.read_text()


def test_init_files_always_overwrites_existing(ingester: ClaimIngester) -> None:
    ingester.processed_dir.mkdir(parents=True, exist_ok=True)
    ingester.diary_path.write_text("OLD CONTENT", encoding="utf-8")

    ingester._init_files()

    # Should be back to the template, not OLD CONTENT
    assert "# Claim Diary" in ingester.diary_path.read_text()
    assert "OLD CONTENT" not in ingester.diary_path.read_text()


# ─── _list_documents ──────────────────────────────────────────────────────────


def test_list_documents_alphabetical(tmp_claim_dir: Path, mock_llm: MagicMock) -> None:
    (tmp_claim_dir / "c_invoice.docx").write_bytes(b"")
    (tmp_claim_dir / "a_claim.pdf").write_bytes(b"")
    (tmp_claim_dir / "b_report.pdf").write_bytes(b"")
    (tmp_claim_dir / "readme.txt").write_text("ignored")

    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    docs = ingester._list_documents()

    assert len(docs) == 3
    assert [d.name for d in docs] == ["a_claim.pdf", "b_report.pdf", "c_invoice.docx"]


def test_list_documents_excludes_unsupported(
    tmp_claim_dir: Path, mock_llm: MagicMock
) -> None:
    (tmp_claim_dir / "doc.pdf").write_bytes(b"")
    (tmp_claim_dir / "doc.txt").write_text("unsupported")
    (tmp_claim_dir / "doc.png").write_bytes(b"")
    (tmp_claim_dir / "doc.docx").write_bytes(b"")

    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    docs = ingester._list_documents()

    assert len(docs) == 2
    assert {d.suffix for d in docs} == {".pdf", ".docx"}


# ─── processed_dir location ───────────────────────────────────────────────────


def test_processed_dir_is_sibling_of_claims(
    tmp_claim_dir: Path, mock_llm: MagicMock
) -> None:
    """processed/C001 must be a sibling directory next to claims/C001."""
    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    assert ingester.processed_dir.name == "C001"
    assert ingester.processed_dir.parent.name == "processed"
    # Both live under the same grandparent
    assert ingester.processed_dir.parent.parent == ingester.claim_dir.parent.parent


# ─── _update_ledger ───────────────────────────────────────────────────────────


def test_update_ledger_no_changes(ingester: ClaimIngester) -> None:
    ingester.processed_dir.mkdir(parents=True, exist_ok=True)
    ingester._init_files()
    original = ingester.ledger_path.read_text()

    ingester._update_ledger("report.pdf", ledger_rows="")

    # Should be unchanged
    assert ingester.ledger_path.read_text() == original


@patch("src.tools.knowledge_base.ingester.parse_document")
def test_run_end_to_end(
    mock_parse: MagicMock,
    tmp_claim_dir: Path,
    mock_llm: MagicMock,
) -> None:
    """Full run with mocked parser and LLM — verifies file contents."""
    (tmp_claim_dir / "claim.pdf").write_bytes(b"%PDF-1.4 dummy")

    # Parser populates doc.chunks
    def _side_effect(doc, **kwargs):
        doc.chunks = [
            TextChunk(
                page_number=1,
                order=0,
                content="Claim document content.",
                bbox=BoundingBox(x0=0, y0=0, x1=100, y1=20),
            )
        ]
        return doc

    mock_parse.side_effect = _side_effect
    mock_llm.generate_from_template.return_value = _good_llm_response()

    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    ingester.run()

    # Diary was overwritten with LLM content
    assert "# Diary" in ingester.diary_path.read_text()
    assert "Updated." in ingester.diary_path.read_text()

    # Ledger tracks the document
    ledger = ingester.ledger_path.read_text()
    assert "claim.pdf" in ledger
    assert "Initialise Claim ID" in ledger


@patch("src.tools.knowledge_base.ingester.parse_document")
def test_run_skips_empty_content(
    mock_parse: MagicMock,
    tmp_claim_dir: Path,
    mock_llm: MagicMock,
) -> None:
    """If the parser extracts no content, the update LLM must not be called, but summary still runs."""
    (tmp_claim_dir / "empty.pdf").write_bytes(b"%PDF-1.4 dummy")

    def _side_effect(doc, **kwargs):
        doc.chunks = []
        return doc

    mock_parse.side_effect = _side_effect
    # Mock for summary
    mock_llm.generate_from_template.return_value = (
        "<!-- BEGIN_SUMMARY -->\n| | |\n<!-- END_SUMMARY -->"
    )

    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    ingester.run()

    # Should only be called once (for summary), NOT for update (which would be called in _process_document)
    assert mock_llm.generate_from_template.call_count == 1
    assert (
        mock_llm.generate_from_template.call_args[1]["template_name"] == "claim_summary"
    )


@patch("src.tools.knowledge_base.ingester.parse_document")
def test_run_generates_summary_table(
    mock_parse: MagicMock,
    tmp_claim_dir: Path,
    mock_llm: MagicMock,
) -> None:
    """The run() method should call _generate_summary() after processing all documents."""
    (tmp_claim_dir / "report.pdf").write_bytes(b"dummy pdf")

    def _side_effect(doc, **kwargs):
        doc.chunks = [
            TextChunk(
                page_number=1, order=0, content="Content", bbox=BoundingBox(0, 0, 1, 1)
            )
        ]
        return doc

    mock_parse.side_effect = _side_effect

    # Mock sequence: 1. Update response, 2. Summary response
    mock_llm.generate_from_template.side_effect = [
        _good_llm_response(),
        "<!-- BEGIN_SUMMARY -->\n| Field | Value | Doc | Page | Reason |\n|---|---|---|---|---|\n| Claim ID | C001 | report.pdf | 1 | Set |\n<!-- END_SUMMARY -->",
    ]

    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    ingester.run()

    assert ingester.summary_path.exists()
    content = ingester.summary_path.read_text()
    assert "| Claim ID | C001 | report.pdf | 1 | Set |" in content
    assert "BEGIN_SUMMARY" not in content  # Should be peeled off


@patch("src.tools.knowledge_base.ingester.parse_document")
def test_run_skips_empty_llm_diary(
    mock_parse: MagicMock,
    tmp_claim_dir: Path,
    mock_llm: MagicMock,
) -> None:
    """If LLM returns no DIARY block, files must not be updated."""
    (tmp_claim_dir / "doc.pdf").write_bytes(b"%PDF-1.4 dummy")

    def _side_effect(doc, **kwargs):
        doc.chunks = [
            TextChunk(
                page_number=1,
                order=0,
                content="Some content.",
                bbox=BoundingBox(x0=0, y0=0, x1=100, y1=20),
            )
        ]
        return doc

    mock_parse.side_effect = _side_effect
    # LLM returns nothing parseable
    mock_llm.generate_from_template.return_value = "I could not process this document."

    ingester = ClaimIngester(claim_dir=tmp_claim_dir, llm=mock_llm)
    ingester.processed_dir.mkdir(parents=True, exist_ok=True)
    ingester._init_files()
    original_diary = ingester.diary_path.read_text()

    ingester.run()

    # Diary must be unchanged
    assert ingester.diary_path.read_text() == original_diary
