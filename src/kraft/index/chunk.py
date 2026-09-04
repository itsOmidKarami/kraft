"""Markdown chunking for vector search (04 §7, Effort 4C).

Whole-document embedding loses granularity on specs and plans, which run long.
Headings are the natural semantic unit, so split on those first and only window
a section that is too big to embed on its own.

The two constants are the tuning knobs 04 §10 left open; they are deliberately
module-level so changing them is a one-line edit once there is a real corpus.
"""

from __future__ import annotations

import re

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

_HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)


def _split_on_headings(text: str) -> list[str]:
    starts = [m.start() for m in _HEADING.finditer(text)]
    if not starts:
        return [text]
    # Text before the first heading is a section in its own right; dropping it
    # would silently lose a document's preamble.
    bounds = ([0] if starts[0] != 0 else []) + starts
    return [text[a:b] for a, b in zip(bounds, bounds[1:] + [len(text)], strict=True)]


def _window(section: str, size: int, overlap: int) -> list[str]:
    """Window an over-long section, preferring paragraph boundaries."""
    paragraphs = [p for p in section.split("\n\n") if p.strip()]
    out: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > size:
            out.append(current.strip())
            current = current[-overlap:] if overlap else ""
        if len(para) > size:
            # A single paragraph bigger than the window has no boundary to use,
            # so cut it on raw character offsets rather than emit one huge chunk.
            if current.strip():
                out.append(current.strip())
                current = ""
            step = size - overlap
            for i in range(0, len(para), step):
                piece = para[i : i + size]
                if piece.strip():
                    out.append(piece.strip())
            continue
        current = f"{current}\n\n{para}" if current else para
    if current.strip():
        out.append(current.strip())
    return out


def chunk_markdown(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split markdown into embeddable chunks. Empty input yields no chunks."""
    if not text or not text.strip():
        return []
    chunks: list[str] = []
    for section in _split_on_headings(text):
        if not section.strip():
            continue
        if len(section) <= size:
            chunks.append(section.strip())
        else:
            chunks.extend(_window(section, size, overlap))
    return chunks
