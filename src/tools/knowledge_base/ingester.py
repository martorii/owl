from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from src.tools.llm.generator import LLMGenerator
from src.tools.parser.models import Document
from src.tools.parser.parser import SUPPORTED_EXTENSIONS, parse_document
from src.utils.logger import get_logger

log = get_logger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

# Matches <!-- BEGIN_X --> ... <!-- END_X --> with backreference to ensure
# the closing tag matches the opening tag. DOTALL so content can span lines.
_SECTION_RE = re.compile(
    r"<!--\s*BEGIN_(?P<section>\w+)\s*-->"
    r"(?P<content>.*?)"
    r"<!--\s*END_(?P=section)\s*-->",
    re.DOTALL | re.IGNORECASE,
)

# ─── ClaimIngester ────────────────────────────────────────────────────────────


class ClaimIngester:
    """
    Processes all documents inside a ``claims/<claim_id>/`` folder and
    maintains three living markdown files in ``processed/<claim_id>/``:

    - ``diary.md``     — running summary of the claim state.
    - ``ledger.md``    — audit log: which docs were processed and what changed.

    Parameters
    ----------
    claim_dir:
        Path to the folder containing the raw claim documents,
        e.g. ``Path("claims/C001")``.
    llm:
        Any :class:`~src.tools.llm.generator.LLMGenerator` instance.
    table_format:
        Table format passed to the document parser (``"markdown"`` or ``"json"``).
    """

    def __init__(
        self,
        claim_dir: str | Path,
        llm: LLMGenerator,
        table_format: str = "markdown",
    ) -> None:
        self.claim_dir = Path(claim_dir).resolve()
        # processed/ lives as a sibling of the claims/ directory
        self.processed_dir = (
            self.claim_dir.parent.parent / "processed" / self.claim_dir.name
        )
        self.llm = llm
        self.table_format = table_format

        self.diary_path = self.processed_dir / "diary.md"
        self.ledger_path = self.processed_dir / "ledger.md"
        self.summary_path = self.processed_dir / "summary_table.md"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Process all supported documents in alphabetical order."""
        log.info("Starting claim ingestion for: %s", self.claim_dir)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self._init_files()

        documents = self._list_documents()
        if not documents:
            log.warning("No supported documents found in %s", self.claim_dir)
            return

        log.info("Found %d document(s) to process.", len(documents))
        for idx, doc_path in enumerate(documents, 1):
            log.info("[%d/%d] Processing: %s", idx, len(documents), doc_path.name)
            self._process_document(doc_path)

        log.info("Generating summary table …")
        self._generate_summary()

        log.info("Claim ingestion complete. Output: %s", self.processed_dir)

    # ------------------------------------------------------------------
    # Private pipeline steps
    # ------------------------------------------------------------------

    def _list_documents(self) -> list[Path]:
        """Return supported document files sorted alphabetically (case-insensitive)."""
        files = [
            f
            for f in self.claim_dir.iterdir()
            if f.is_file() and f.suffix.lstrip(".").lower() in SUPPORTED_EXTENSIONS
        ]
        return sorted(files, key=lambda p: p.name.lower())

    def _process_document(self, doc_path: Path) -> None:
        """Parse → call LLM → update the three markdown files."""
        # 1. Parse
        doc_content = self._parse_document(doc_path)
        if doc_content is None:
            return

        # 2. Read current diary
        diary_md = self.diary_path.read_text(encoding="utf-8")

        # 3. Call LLM
        try:
            raw_response = self._call_llm(doc_content, diary_md)
        except Exception as exc:
            log.error("  LLM call failed for %s: %s", doc_path.name, exc)
            return

        # 4. Parse LLM response
        diary_update, ledger_rows = self._parse_llm_response(raw_response)

        if not diary_update.strip():
            log.warning(
                "  LLM returned an empty DIARY for %s — skipping file updates.",
                doc_path.name,
            )
            return

        # 5. Write
        self._write_files(doc_path.name, diary_update, ledger_rows)
        log.info("  ✓ Files updated for %s.", doc_path.name)

    def _parse_document(self, doc_path: Path) -> str | None:
        """Return the text content of a document, or None on failure."""
        try:
            with open(doc_path, "rb") as f:
                data = f.read()
            ext = doc_path.suffix.lstrip(".").lower()
            doc = Document(filename=doc_path.name, extension=ext, data=data)
            parse_document(doc, table_format=self.table_format)
            content = doc.get_content(add_page_numbers=True)
            if not content.strip():
                log.warning("  No content extracted from %s — skipping.", doc_path.name)
                return None
            return content
        except Exception as exc:
            log.error("  Failed to parse %s: %s", doc_path.name, exc)
            return None

    def _call_llm(self, doc_content: str, diary: str) -> str:
        """Send document content + current diary to the LLM."""
        return self.llm.generate_from_template(
            template_name="claim_update",
            variables={"diary": diary, "document_content": doc_content},
            system_prompt=(
                "You are an experienced insurance claims analyst. "
                "Your task is to analyse incoming claim documents and maintain an "
                "accurate, up-to-date claim file as well as a ledger of changes. "
                "Be precise, concise, and objective."
            ),
            temperature=0.2,
        )

    @staticmethod
    def _parse_llm_response(raw: str) -> tuple[str, str]:
        """
        Extract the sections from the LLM response using HTML comment
        delimiters.

        Returns
        -------
        tuple[str, str]
            ``(diary_md, ledger_rows)``
        """
        sections: dict[str, str] = {
            m.group("section").upper(): m.group("content").strip()
            for m in _SECTION_RE.finditer(raw)
        }
        return (
            sections.get("DIARY", ""),
            sections.get("LEDGER_ENTRY", ""),
        )

    def _write_files(
        self,
        doc_name: str,
        diary_md: str,
        ledger_rows: str,
    ) -> None:
        """Overwrite diary, update ledger."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 1. Diary — full overwrite
        self.diary_path.write_text(diary_md, encoding="utf-8")

        # 2. Ledger — append entries to the unified table
        self._update_ledger(doc_name, ledger_rows)

    def _update_ledger(self, doc_name: str, ledger_rows: str) -> None:
        """Append incoming change rows to the single table in ledger.md."""
        lines = [line.strip() for line in ledger_rows.splitlines() if "|" in line]
        if not lines:
            log.warning(f"No ledger rows found in LLM response for {doc_name}.")
            return

        with open(self.ledger_path, "a", encoding="utf-8") as f:
            for line in lines:
                # LLM output format: | Page | Field Name | Old Value | New Value |
                # Strip leading/trailing whitespace and pipes
                clean_line = line.strip().strip("|")
                cells = [c.strip() for c in clean_line.split("|")]

                # There must be as many cells as columns provided by LLM (4)
                if len(cells) != 4:
                    log.warning("  Invalid ledger row: %s", line)
                    continue

                page = cells[0]
                field_name = cells[1]
                old = cells[2]
                new = cells[3]

                f.write(f"| {doc_name} | {page} | {field_name} | {old} | {new} |\n")

    def _init_files(self) -> None:
        """Initialise (or overwrite) diary and ledger from templates."""
        log.debug("Initialising files from templates for a fresh run …")
        for dest, tpl_name in [
            (self.diary_path, "diary_template.md"),
            (self.ledger_path, "ledger_template.md"),
        ]:
            tpl_path = _TEMPLATES_DIR / tpl_name
            content = (
                tpl_path.read_text(encoding="utf-8")
                if tpl_path.exists()
                else f"# {tpl_name.replace('_template.md', '').capitalize()}\n"
            )
            dest.write_text(content, encoding="utf-8")
            log.debug("  ✓ %s", dest.name)

    def _generate_summary(self) -> None:
        """Call the LLM to synthesise diary + ledger into summary_table.md."""
        diary = self.diary_path.read_text(encoding="utf-8")
        ledger = self.ledger_path.read_text(encoding="utf-8")

        try:
            raw = self.llm.generate_from_template(
                template_name="claim_summary",
                variables={"diary": diary, "ledger": ledger},
                system_prompt=(
                    "You are an experienced insurance claims analyst. "
                    "Your task is to extract the most relevant fields from a claim file "
                    "and present them in a clear, concise markdown table."
                ),
                temperature=0.1,
            )
        except Exception as exc:
            log.error("  Summary LLM call failed: %s", exc)
            return

        table_md = self._parse_summary_response(raw)
        if not table_md.strip():
            log.warning("  LLM returned no summary table — skipping.")
            return

        self.summary_path.write_text(table_md, encoding="utf-8")
        log.info(
            "  ✓ summary_table.md written (%d bytes).", self.summary_path.stat().st_size
        )

    @staticmethod
    def _parse_summary_response(raw: str) -> str:
        """Extract the summary table from the LLM response."""
        m = re.search(
            r"<!--\s*BEGIN_SUMMARY\s*-->"
            r"(?P<content>.*?)"
            r"<!--\s*END_SUMMARY\s*-->",
            raw,
            re.DOTALL | re.IGNORECASE,
        )
        if not m:
            return ""
        return m.group("content").strip()
