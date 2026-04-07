from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ─── BoundingBox ─────────────────────────────────────────────────────────────


@dataclass
class BoundingBox:
    """
    Coordinates in PDF points using pdfplumber convention (origin = top-left).
    x0, y0 = top-left corner
    x1, y1 = bottom-right corner
    """

    x0: float
    y0: float  # top edge (pdfplumber "top")
    x1: float
    y1: float  # bottom edge (pdfplumber "bottom")

    def as_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


# ─── Chunk types ─────────────────────────────────────────────────────────────


class ChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


@dataclass
class Chunk:
    """
    Base class for a single content unit extracted from a document page.
    """

    page_number: int
    bbox: BoundingBox
    order: int

    @property
    def chunk_type(self) -> ChunkType:
        """Return the ChunkType (overridden by subclasses)."""
        raise NotImplementedError

    def as_dict(self) -> dict[str, Any]:
        """Base dictionary representation used for serialisation."""
        return {
            "page_number": self.page_number,
            "order": self.order,
            "chunk_type": self.chunk_type.value,
            "bbox": self.bbox.as_dict(),
        }

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        raise NotImplementedError


@dataclass
class TextChunk(Chunk):
    """
    A chunk representing a block of text.
    """

    content: str

    @property
    def chunk_type(self) -> ChunkType:
        return ChunkType.TEXT

    def as_dict(self) -> dict[str, Any]:
        d = super().as_dict()
        d["content"] = self.content
        return d

    def __str__(self) -> str:
        content_summary = self.content.replace("\n", " ")
        if len(content_summary) > 100:
            content_summary = content_summary[:97] + "..."
        return f"Page {self.page_number:02d} | Order {self.order:02d} | TEXT  | {content_summary}"

    def __repr__(self) -> str:
        return self.__str__()


@dataclass
class TableChunk(Chunk):
    """
    A chunk representing a table extracted from the document.
    """

    content: str  # markdown or JSON representation
    num_rows: int
    num_cols: int

    @property
    def chunk_type(self) -> ChunkType:
        return ChunkType.TABLE

    def as_dict(self) -> dict[str, Any]:
        d = super().as_dict()
        d["content"] = self.content
        d["num_rows"] = self.num_rows
        d["num_cols"] = self.num_cols
        return d

    def __str__(self) -> str:
        content_summary = f"Table ({self.num_rows}x{self.num_cols})"
        if self.content:
            first_line = self.content.split("\n")[0]
            content_summary += f" {first_line}"
        if len(content_summary) > 100:
            content_summary = content_summary[:97] + "..."
        return f"Page {self.page_number:02d} | Order {self.order:02d} | TABLE | {content_summary}"

    def __repr__(self) -> str:
        return self.__str__()


@dataclass
class ImageChunk(Chunk):
    """
    A chunk representing an image extracted from the document.
    """

    content: bytes | None
    image_width: float
    image_height: float
    image_index: int

    @property
    def chunk_type(self) -> ChunkType:
        return ChunkType.IMAGE

    def as_dict(self) -> dict[str, Any]:
        d = super().as_dict()
        d["content"] = None  # bytes not serialisable; omit
        d["image_width"] = self.image_width
        d["image_height"] = self.image_height
        d["image_index"] = self.image_index
        d["has_bytes"] = self.content is not None
        return d

    def __str__(self) -> str:
        content_summary = f"Image index={self.image_index} size={self.image_width}x{self.image_height}"
        return f"Page {self.page_number:02d} | Order {self.order:02d} | IMAGE | {content_summary}"

    def __repr__(self) -> str:
        return self.__str__()


# ─── Document ────────────────────────────────────────────────────────────────


@dataclass
class Document:
    """
    Unified representation of a document, containing both raw source data
    and the structured chunks extracted during parsing.
    """

    filename: str
    extension: str
    data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)
    chunks: list[Chunk] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Return a serialisable dictionary representation of the document."""
        return {
            "filename": self.filename,
            "extension": self.extension,
            "metadata": self.metadata,
            "chunks": [c.as_dict() for c in self.chunks],
        }

    def get_content(self, add_page_numbers: bool = False) -> str:
        """
        Concatenate all text and table content in order.
        Images are skipped in the string representation.

        Parameters
        ----------
        add_page_numbers:
            When ``True``, each chunk is prefixed with ``[Page N]`` so that
            downstream consumers (e.g. an LLM) can cite the source page.
        """
        parts = []
        for chunk in self.chunks:
            if isinstance(chunk, (TextChunk, TableChunk)):
                if chunk.content:
                    if add_page_numbers:
                        parts.append(f"[Page {chunk.page_number}]\n{chunk.content}")
                    else:
                        parts.append(chunk.content)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Beautiful printing
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        header = f"Document: {self.filename} ({len(self.chunks)} chunks extracted)"
        separator = "-" * 120
        table_header = f"{'PAGE':<4} | {'ORDER':<5} | {'TYPE':<5} | {'CONTENT SUMMARY'}"

        lines = [header, separator, table_header, separator]
        for chunk in self.chunks:
            lines.append(str(chunk))

        return "\n".join(lines)

    def __repr__(self) -> str:
        return self.__str__()
