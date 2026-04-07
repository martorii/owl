from __future__ import annotations

import io
import json
import re
from abc import ABC, abstractmethod
from typing import Any, Literal

import pdfplumber
from pypdf import PdfReader

from src.tools.parser.models import (
    BoundingBox,
    Chunk,
    Document,
    ImageChunk,
    TableChunk,
    TextChunk,
)
from src.utils.logger import get_logger

log = get_logger(__name__)


# ─── Abstract base ────────────────────────────────────────────────────────────


class DocumentParser(ABC):
    """
    Base class for all document format parsers.

    Subclasses declare `extensions` as class-level sets, then
    implement `parse(data) -> ParsedDocument`.
    """

    extensions: set[str] = set()
    label: str = "unknown"

    def supports(self, filename: str = "") -> bool:
        if filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext in self.extensions:
                return True
        return False

    @abstractmethod
    def parse(
        self, doc: Document, table_format: Literal["markdown", "json"] = "markdown"
    ) -> Document:
        return doc

    def __repr__(self) -> str:
        return f"<DocumentParser:{self.label}>"

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalize extracted text for better downstream processing."""
        if not text:
            return ""
        # 1. Normalize vertical whitespace: no more than 2 newlines in a row
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 2. Normalize horizontal whitespace: collapse multiple spaces/tabs
        text = re.sub(r"[ \t]+", " ", text)
        # 3. Handle word splitting at line breaks
        text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
        # 4. Split merged words that look like camelCase or glued text
        text = re.sub(r"([a-zäöüß])([A-ZÄÖÜ])", r"\1 \2", text)
        # 5. Remove leading/trailing whitespace from each line
        lines = [line.strip() for line in text.split("\n")]
        return "\n".join(lines).strip()

    @staticmethod
    def table_to_markdown(rows: list[list[Any]]) -> str:
        """Convert a list of rows into a GitHub-friendly markdown table."""
        if not rows:
            return ""
        num_cols = max(len(row) for row in rows)

        def _fmt(val: Any) -> str:
            s = str(val) if val is not None else ""
            return s.replace("|", "\\|").replace("\n", " ").strip()

        lines = []
        # Header
        header_row = rows[0]
        padding = [""] * (num_cols - len(header_row))
        header_cells = [_fmt(c) for c in header_row + padding]
        lines.append("| " + " | ".join(header_cells) + " |")
        # Separator
        lines.append("| " + " | ".join(["---"] * num_cols) + " |")
        # Body
        for row in rows[1:]:
            padding = [""] * (num_cols - len(row))
            cells = [_fmt(c) for c in row + padding]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    @staticmethod
    def table_to_json(rows: list[list[Any]]) -> str:
        """Convert a list of rows into a JSON string."""
        if not rows:
            return "[]"
        # Optional: convert to list of dicts if first row is header
        # For now, just a list of lists is fine as it's the rawest form
        return json.dumps(rows, indent=2, ensure_ascii=False)

    @staticmethod
    def sort_chunks_by_vertical_position(chunks: list[Chunk]) -> list[Chunk]:
        """Return a list of chunks ordered by (page_number, bbox.y0)."""
        return sorted(chunks, key=lambda c: (c.page_number, c.bbox.y0))

    @staticmethod
    def _merge_consecutive_text_chunks(chunks: list[Chunk]) -> list[Chunk]:
        """
        Merge consecutive TextChunks into single blocks, splitting whenever a
        non-text chunk (TABLE / IMAGE) or a page-number boundary is encountered.
        """
        if not chunks:
            return []

        merged_chunks: list[Chunk] = []
        current: TextChunk | None = None

        for chunk in chunks:
            if isinstance(chunk, TextChunk):
                if current is None:
                    # Start a new text block
                    current = chunk
                elif chunk.page_number != current.page_number:
                    # Page jump — flush current and start fresh
                    merged_chunks.append(current)
                    current = chunk
                else:
                    # Same page, same run — append
                    current.content = f"{current.content}\n{chunk.content}"
                    current.bbox.x0 = min(current.bbox.x0, chunk.bbox.x0)
                    current.bbox.y0 = min(current.bbox.y0, chunk.bbox.y0)
                    current.bbox.x1 = max(current.bbox.x1, chunk.bbox.x1)
                    current.bbox.y1 = max(current.bbox.y1, chunk.bbox.y1)
            else:
                # Delimiter found (table or image)
                if current is not None:
                    merged_chunks.append(current)
                    current = None
                merged_chunks.append(chunk)

        if current is not None:
            merged_chunks.append(current)

        return merged_chunks


# ─── PDF ─────────────────────────────────────────────────────────────────────


class PdfParser(DocumentParser):
    extensions = {"pdf"}
    label = "PDF"

    def parse(
        self, doc: Document, table_format: Literal["markdown", "json"] = "markdown"
    ) -> Document:
        """
        Parse a PDF document from bytes and return the updated :class:`Document`.

        Chunks are extracted per page (text blocks, tables, images) and stored
        in doc.chunks. Chunks are sorted by reading order.

        Parameters
        ----------
        doc:
            The document container.
        table_format:
            The format for table content: "markdown" or "json".

        Returns
        -------
        Document
            The input document object with its 'chunks' attribute populated.
        """

        log.debug("Opening document %r with pdfplumber …", doc.filename)
        plumber_pdf = pdfplumber.open(io.BytesIO(doc.data))

        log.debug("Opening document with pypdf …")
        pypdf_reader = PdfReader(io.BytesIO(doc.data))

        total_pages = len(plumber_pdf.pages)
        log.debug("Total pages detected: %d", total_pages)

        document_chunks: list[Chunk] = []

        for page_idx in range(total_pages):
            page_number = page_idx + 1
            log.debug("── Processing page %d / %d ──", page_number, total_pages)

            plumber_page = plumber_pdf.pages[page_idx]
            pypdf_page = pypdf_reader.pages[page_idx]

            # Collect raw chunks for this page, then sort vertically and
            # assign per-page order indices before appending to the document.
            page_chunks: list[Chunk] = []

            # 1. TABLE EXTRACTION (must come before text so we can mask them)
            table_chunks, table_bboxes = self._extract_tables(
                plumber_page, page_number, table_format=table_format
            )
            page_chunks.extend(table_chunks)

            # 2. TEXT EXTRACTION (words outside table regions only)
            text_chunks = self._extract_text(plumber_page, page_number, table_bboxes)
            page_chunks.extend(text_chunks)

            # 3. PICTURE / IMAGE EXTRACTION
            image_chunks = self._extract_pictures(plumber_page, pypdf_page, page_number)
            page_chunks.extend(image_chunks)

            # Sort by vertical position
            page_chunks.sort(key=lambda f: f.bbox.y0)

            # Merge consecutive text chunks (delimited by tables, images, or page start/end)
            page_chunks = self._merge_consecutive_text_chunks(page_chunks)

            for order_idx, chunk in enumerate(page_chunks):
                chunk.order = order_idx

            document_chunks.extend(page_chunks)

            text_count = sum(1 for c in page_chunks if isinstance(c, TextChunk))
            table_count = sum(1 for c in page_chunks if isinstance(c, TableChunk))
            image_count = sum(1 for c in page_chunks if isinstance(c, ImageChunk))
            log.debug(
                "Page %d done — text_chunks=%d, table_chunks=%d, image_chunks=%d",
                page_number,
                text_count,
                table_count,
                image_count,
            )

        plumber_pdf.close()
        # Ensure final global sort across all pages and assign to the doc
        doc.chunks = self.sort_chunks_by_vertical_position(document_chunks)
        log.debug("Parsing complete. Pages processed: %d", total_pages)
        return doc

    # _merge_consecutive_text_chunks is defined on DocumentParser (base class)

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_not_inside_table(
        obj: dict[str, Any], table_bboxes: list[tuple[float, float, float, float]]
    ) -> bool:
        """Return True if the object does not intersect any table bbox."""
        # We filter 'char' objects. Most other objects like 'rect', 'line'
        # don't contain text content but might be part of tables.
        if obj.get("object_type") != "char":
            return True

        # x0, top, x1, bottom
        x0, y0, x1, y1 = obj["x0"], obj["top"], obj["x1"], obj["bottom"]

        for tx0, ty0, tx1, ty1 in table_bboxes:
            # Intersection check: standard AABB overlap
            overlap_x = x0 < tx1 and x1 > tx0
            overlap_y = y0 < ty1 and y1 > ty0
            if overlap_x and overlap_y:
                return False
        return True

    def _extract_text(
        self,
        plumber_page: Any,
        page_number: int,
        table_bboxes: list[tuple[float, float, float, float]],
    ) -> list[TextChunk]:
        """
        Extract text lines as individual chunks, skipping any content that
        falls inside a table bounding box.

        We filter the page objects (characters) using the table bounding boxes
        before extracting words to ensure no table content is included.
        """
        log.debug(
            "  [text] Extracting characters from page %d outside tables …", page_number
        )

        # Perform character-level filtering using the helper method
        clean_page = plumber_page.filter(
            lambda obj: self._is_not_inside_table(obj, table_bboxes)
        )

        x_tolerance = 1.1
        y_tolerance = 2.0

        filtered_words = clean_page.extract_words(
            x_tolerance=x_tolerance,
            y_tolerance=y_tolerance,
            keep_blank_chars=False,
            use_text_flow=True,
        )

        if not filtered_words:
            log.debug("  [text] No words found on page %d outside tables.", page_number)
            return []

        # Group words into lines by ``top`` value (within y_tolerance)
        lines: list[list[dict]] = []
        for word in filtered_words:
            placed = False
            for line in lines:
                if abs(word["top"] - line[0]["top"]) <= y_tolerance:
                    line.append(word)
                    placed = True
                    break
            if not placed:
                lines.append([word])

        chunks: list[TextChunk] = []
        for line_words in lines:
            text = " ".join(w["text"] for w in line_words)
            x0 = min(w["x0"] for w in line_words)
            y0 = min(w["top"] for w in line_words)
            x1 = max(w["x1"] for w in line_words)
            y1 = max(w["bottom"] for w in line_words)
            bbox = BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)
            chunks.append(
                TextChunk(
                    page_number=page_number,
                    order=0,  # assigned later after sort
                    content=self._clean_text(text),
                    bbox=bbox,
                )
            )

        log.debug(
            "  [text] Extracted %d text line chunks on page %d.",
            len(chunks),
            page_number,
        )
        return chunks

    def _extract_tables(
        self,
        plumber_page: Any,
        page_number: int,
        table_format: Literal["markdown", "json"] = "markdown",
    ) -> tuple[list[TableChunk], list[tuple[float, float, float, float]]]:
        """
        Extract tables together with their bounding boxes.

        Returns a tuple of (chunks, bboxes) where bboxes is a list of
        (x0, top, x1, bottom) tuples used to mask out table regions
        during text extraction.
        """
        log.debug("  [tables] Extracting tables from page %d …", page_number)

        plumber_tables = plumber_page.find_tables()

        if not plumber_tables:
            log.debug("  [tables] No tables found on page %d.", page_number)
            return [], []

        chunks: list[TableChunk] = []
        bboxes: list[tuple[float, float, float, float]] = []

        for t_idx, plumber_table in enumerate(plumber_tables):
            raw_bbox = plumber_table.bbox  # (x0, top, x1, bottom)
            bbox = BoundingBox(
                x0=raw_bbox[0],
                y0=raw_bbox[1],
                x1=raw_bbox[2],
                y1=raw_bbox[3],
            )
            rows: list[list[Any]] = plumber_table.extract()
            num_rows = len(rows)
            num_cols = max((len(r) for r in rows), default=0)

            if table_format == "json":
                content = self.table_to_json(rows)
            else:
                content = self.table_to_markdown(rows)

            chunks.append(
                TableChunk(
                    page_number=page_number,
                    order=0,  # assigned later
                    content=content,
                    bbox=bbox,
                    num_rows=num_rows,
                    num_cols=num_cols,
                )
            )
            bboxes.append((raw_bbox[0], raw_bbox[1], raw_bbox[2], raw_bbox[3]))
            log.debug(
                "  [tables] Table %d: %d rows x %d cols, bbox=%s",
                t_idx + 1,
                num_rows,
                num_cols,
                bbox.as_dict(),
            )

        log.debug("  [tables] Total tables on page %d: %d", page_number, len(chunks))
        return chunks, bboxes

    def _extract_pictures(
        self,
        plumber_page: Any,
        pypdf_page: Any,
        page_number: int,
    ) -> list[ImageChunk]:
        """
        Extract images and their bounding boxes.

        pdfplumber exposes image metadata (coordinates, width, height).
        pypdf is used to pull the actual image bytes.
        """
        log.debug("  [pictures] Extracting images from page %d …", page_number)

        plumber_images = plumber_page.images
        if not plumber_images:
            log.debug("  [pictures] No images found on page %d.", page_number)
            return []

        # Build image-bytes list from pypdf (order matches pdfplumber order)
        pypdf_images: list[Any] = []
        try:
            pypdf_images = list(pypdf_page.images)
        except Exception as exc:
            log.warning("  [pictures] Could not extract image bytes via pypdf: %s", exc)

        chunks: list[ImageChunk] = []

        for img_idx, img_meta in enumerate(plumber_images):
            bbox = BoundingBox(
                x0=img_meta.get("x0", 0.0),
                y0=img_meta.get("top", 0.0),
                x1=img_meta.get("x1", 0.0),
                y1=img_meta.get("bottom", 0.0),
            )

            img_bytes: bytes | None = None
            if img_idx < len(pypdf_images):
                try:
                    img_bytes = pypdf_images[img_idx].data
                except Exception as exc:
                    log.debug(
                        "  [pictures] Could not read bytes for image %d: %s",
                        img_idx,
                        exc,
                    )

            chunks.append(
                ImageChunk(
                    page_number=page_number,
                    order=0,  # assigned later
                    content=img_bytes,
                    bbox=bbox,
                    image_width=img_meta.get("width", 0.0),
                    image_height=img_meta.get("height", 0.0),
                    image_index=img_idx,
                )
            )
            log.debug(
                "  [pictures] Image %d: size=(%s x %s), bbox=%s, bytes=%s",
                img_idx,
                img_meta.get("width"),
                img_meta.get("height"),
                bbox.as_dict(),
                f"{len(img_bytes)} bytes" if img_bytes else "unavailable",
            )

        log.debug("  [pictures] Total images on page %d: %d", page_number, len(chunks))
        return chunks


# ─── Word (.docx) ────────────────────────────────────────────────────────────


class DocxParser(DocumentParser):
    """
    Parser for Microsoft Word (.docx) files.

    Iterates the document body in XML order so that text, tables and images
    appear in their natural reading sequence.  Because Word has no notion of
    absolute coordinates, synthetic bounding boxes are assigned using the
    sequential element order as the ``y0`` value (one unit ≈ one body element).

    Page detection relies on explicit ``<w:br w:type="page"/>`` run-level
    breaks and paragraph-level ``<w:sectPr>`` section breaks of type
    ``nextPage``, ``evenPage``, or ``oddPage``.
    """

    extensions = {"docx"}
    label = "DOCX"

    # Standard letter-page width in points used for synthetic x1
    _PAGE_WIDTH_PT: float = 612.0
    # Approximate line height in points for synthetic text bbox height
    _LINE_HEIGHT_PT: float = 12.0

    def parse(
        self, doc: Document, table_format: Literal["markdown", "json"] = "markdown"
    ) -> Document:
        """
        Parse a .docx document and return the updated :class:`Document`.

        Parameters
        ----------
        doc:
            The document container (``doc.data`` must be raw .docx bytes).
        table_format:
            The format for table content: ``"markdown"`` or ``"json"``.

        Returns
        -------
        Document
            The input document with its ``chunks`` attribute populated.
        """
        try:
            from docx import Document as DocxDocument  # type: ignore[import-untyped]
            from docx.oxml.ns import qn  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "python-docx is required for DOCX parsing. "
                "Install it with: pip install python-docx"
            ) from exc

        log.info("Opening document %r with python-docx …", doc.filename)
        docx_doc = DocxDocument(io.BytesIO(doc.data))

        document_chunks: list[Chunk] = []
        page_number = 1
        page_chunks: list[Chunk] = []
        element_order = 0  # sequential body-element counter → synthetic y0

        body = docx_doc.element.body

        for child in body:
            # Normalise tag to local name (strip namespace URI)
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if local == "p":  # ── paragraph ──────────────────────────────
                # 1. Detect page breaks FIRST so the images/text land on the
                #    new page, not the old one.
                if self._docx_paragraph_has_page_break(child, qn):
                    page_chunks = self._merge_consecutive_text_chunks(page_chunks)
                    for order_idx, chunk in enumerate(page_chunks):
                        chunk.order = order_idx
                    document_chunks.extend(page_chunks)
                    page_chunks = []
                    page_number += 1
                    log.info(
                        "── Page break detected, advancing to page %d ──",
                        page_number,
                    )

                # 2. Images embedded in this paragraph
                img_chunks = self._extract_docx_images_from_paragraph(
                    child, docx_doc, page_number, element_order, qn
                )
                page_chunks.extend(img_chunks)

                # 3. Plain text of the paragraph
                text = self._get_docx_paragraph_text(child, qn)
                if text.strip():
                    bbox = BoundingBox(
                        x0=0.0,
                        y0=float(element_order),
                        x1=self._PAGE_WIDTH_PT,
                        y1=float(element_order) + self._LINE_HEIGHT_PT,
                    )
                    page_chunks.append(
                        TextChunk(
                            page_number=page_number,
                            order=0,  # assigned after merge
                            content=self._clean_text(text),
                            bbox=bbox,
                        )
                    )

            elif local == "tbl":  # ── table ──────────────────────────────
                tbl_chunk = self._extract_docx_table(
                    child, docx_doc, page_number, element_order, table_format, qn
                )
                if tbl_chunk is not None:
                    page_chunks.append(tbl_chunk)

            # `sectPr` as a direct body child marks the document's final
            # section — no page increment needed.

            element_order += 1

        # ── Finalize last page ──────────────────────────────────────────────
        page_chunks = self._merge_consecutive_text_chunks(page_chunks)
        for order_idx, chunk in enumerate(page_chunks):
            chunk.order = order_idx
        document_chunks.extend(page_chunks)

        doc.chunks = document_chunks
        log.info(
            "Parsing complete. Pages detected: %d, Total chunks: %d",
            page_number,
            len(document_chunks),
        )
        return doc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _docx_paragraph_has_page_break(para_element: Any, qn: Any) -> bool:
        """
        Return ``True`` if the paragraph contains an explicit page break
        (``<w:br w:type="page"/>``) or a page-producing section break
        (``<w:sectPr>`` with type ``nextPage``, ``evenPage``, or ``oddPage``).
        """
        # Run-level explicit page break
        for br in para_element.iter(qn("w:br")):
            if br.get(qn("w:type")) == "page":
                return True
        # Paragraph-level section break
        sect_pr = para_element.find(f".//{qn('w:sectPr')}")
        if sect_pr is not None:
            type_el = sect_pr.find(qn("w:type"))
            val = (
                type_el.get(qn("w:val"), "nextPage")
                if type_el is not None
                else "nextPage"
            )
            if val in ("nextPage", "evenPage", "oddPage"):
                return True
        return False

    @staticmethod
    def _get_docx_paragraph_text(para_element: Any, qn: Any) -> str:
        """Concatenate all ``<w:t>`` text runs in a paragraph element."""
        return "".join(t.text or "" for t in para_element.iter(qn("w:t")))

    def _extract_docx_table(
        self,
        tbl_element: Any,
        docx_doc: Any,
        page_number: int,
        element_order: int,
        table_format: Literal["markdown", "json"],
        qn: Any,
    ) -> TableChunk | None:
        """
        Build a :class:`TableChunk` from a ``<w:tbl>`` element.

        Duplicate cell content caused by merged cells is preserved as-is
        (python-docx repeats the value across spanned cells).
        """
        try:
            from docx.table import Table as DocxTable  # type: ignore[import-untyped]
        except ImportError:  # pragma: no cover
            return None

        try:
            table = DocxTable(tbl_element, docx_doc)
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        except Exception as exc:
            log.warning("  [tables] Failed to extract DOCX table: %s", exc)
            return None

        if not rows:
            return None

        num_rows = len(rows)
        num_cols = max((len(r) for r in rows), default=0)

        content = (
            self.table_to_json(rows)
            if table_format == "json"
            else self.table_to_markdown(rows)
        )

        # Synthetic bbox: allocate ~20 pt per row
        height = num_rows * 20.0
        bbox = BoundingBox(
            x0=0.0,
            y0=float(element_order),
            x1=self._PAGE_WIDTH_PT,
            y1=float(element_order) + height,
        )

        log.debug(
            "  [tables] Extracted DOCX table: %d rows x %d cols",
            num_rows,
            num_cols,
        )
        return TableChunk(
            page_number=page_number,
            order=0,  # assigned after merge
            content=content,
            bbox=bbox,
            num_rows=num_rows,
            num_cols=num_cols,
        )

    def _extract_docx_images_from_paragraph(
        self,
        para_element: Any,
        docx_doc: Any,
        page_number: int,
        element_order: int,
        qn: Any,
    ) -> list[ImageChunk]:
        """
        Extract inline/anchor images from a ``<w:p>`` element.

        Each ``<w:drawing>`` is inspected for an ``<a:blip r:embed=…/>``
        reference which is resolved against the document's relationship parts
        to obtain raw image bytes.  EMU extents are converted to points.
        """
        chunks: list[ImageChunk] = []

        for img_idx, drawing in enumerate(para_element.iter(qn("w:drawing"))):
            blip = drawing.find(f".//{qn('a:blip')}")
            if blip is None:
                continue

            r_embed = blip.get(qn("r:embed"))
            if not r_embed:
                continue

            img_bytes: bytes | None = None
            try:
                image_part = docx_doc.part.related_parts[r_embed]
                img_bytes = image_part.blob
            except (KeyError, AttributeError) as exc:
                log.debug("  [pictures] Could not read DOCX image bytes: %s", exc)

            # Convert EMU → points  (1 in = 914 400 EMU = 72 pt)
            img_width, img_height = 0.0, 0.0
            extent = drawing.find(f".//{qn('wp:extent')}")
            if extent is not None:
                img_width = int(extent.get("cx", 0)) / 914_400 * 72
                img_height = int(extent.get("cy", 0)) / 914_400 * 72

            bbox = BoundingBox(
                x0=0.0,
                y0=float(element_order),
                x1=img_width,
                y1=float(element_order) + img_height,
            )

            log.debug(
                "  [pictures] Extracted DOCX image %d: size=(%.1f x %.1f)",
                img_idx,
                img_width,
                img_height,
            )
            chunks.append(
                ImageChunk(
                    page_number=page_number,
                    order=0,  # assigned after merge
                    content=img_bytes,
                    bbox=bbox,
                    image_width=img_width,
                    image_height=img_height,
                    image_index=img_idx,
                )
            )

        return chunks


# ─── Registry & dispatcher ────────────────────────────────────────────────────

_PARSERS: list[DocumentParser] = []


def _register(cls: type[DocumentParser]) -> type[DocumentParser]:
    _PARSERS.append(cls())
    return cls


for _cls in [PdfParser, DocxParser]:
    _register(_cls)

SUPPORTED_EXTENSIONS = {e for p in _PARSERS for e in p.extensions}


def get_parser(filename: str = "") -> DocumentParser | None:
    """Return the first parser that claims to support the given file."""
    for parser in _PARSERS:
        if parser.supports(filename=filename):
            return parser
    return None


def parse_document(
    doc: Document, table_format: Literal["markdown", "json"] = "markdown"
) -> Document:
    """
    Dispatch to the correct DocumentParser subclass and return the updated Document.
    Raises ValueError for unsupported formats.
    """
    parser = get_parser(filename=doc.filename)
    if parser is None:
        ext = doc.extension or "?"
        supported = sorted({e for p in _PARSERS for e in p.extensions})
        raise ValueError(
            f"Unsupported file type: .{ext}. "
            f"Supported extensions: {', '.join(supported)}"
        )
    log.info("Parsing %r with %s", doc.filename, parser)
    return parser.parse(doc, table_format=table_format)
