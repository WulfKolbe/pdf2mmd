"""linesjson — pdf2mmd's reading, in the shape pdfdrill's docmodel ingests.

WHY A LINES.JSON AND NOT A `Document`
-------------------------------------
pdfdrill does not want a second document model; it wants THE document model,
built by the twenty modules in `src/docmodel/modules/` that turn typed OCR
lines into `Table`, `Diagram`, `CodeListing`, `Formula`, `Section` objects —
and then by every projector that reads those objects (LaTeX, tiddlers,
markdown, report, compare). Emitting a `Document` here would mean
reimplementing those twenty modules on this side of a venv boundary, and
maintaining two of them forever.

So this emits what those modules consume: a `lines.json`. `docmodel.main`
ingests it, the modules build the Document, and every projection works with
no new code at all. The docmodel IS generated from pdf2mmd's reading — by
the code that already knows how to generate one.

WHAT WAS MISSING, MEASURED
--------------------------
pdfdrill's own keyless reader (`chars_to_lines.py`) emits TWO line types on a
24-page paper: `text` 910, `math` 5. MathPix emits sixteen on a comparable
one — `simple_cell` 164, `list_item` 59, `table_row` 49, `table_column` 32,
`complex_cell` 30, `column` 18, `section_header` 17 … The modules key off
those types, so a two-type stream can only ever produce paragraphs. That is
the whole distance between 171 boxed objects and MathPix's 530 of 530, and
no amount of geometry work downstream closes it: there is nothing in the
stream to build a table cell out of.

Every type below comes from a measurement pdf2mmd already performs. Nothing
here guesses: a line whose kind is not established stays `text`, which is
what an unread line has always been.

COORDINATES
-----------
pdf2mmd works in PDF points with y UP (pdfminer's convention). A pdfdrill
`Region` for any non-MathPix source is PDF points, TOP-LEFT, y DOWN — the
same lane `pdfminer-chars` already uses, served from our own pyramid rather
than cdn.mathpix.com. The flip is `page_top - y1`, which is the one
`project_mmd.crop_url` has always computed and then thrown away.
"""
from __future__ import annotations

import hashlib
from typing import Any

import docmodel_six as docmodel
import project_mmd as mmd
from docmodel_six import LineNode, PageNode

#: The lines.json `source` stamp. pdfdrill routes on this name: it must be in
#: `commands._MERGEABLE_LINES_SOURCES` or the merged route silently switches
#: off, which is how `visionocr` cost 2609.24972 every Section it had (782).
SOURCE = "pdf2mmd"


def _lid(page: int, index: int) -> str:
    """A stable line id. MathPix ships a uuid per line and several pdfdrill
    readers key on it, so an id that changes between runs of the SAME document
    would make every cached reference stale."""
    return hashlib.sha1(f"{SOURCE}:{page}:{index}".encode()).hexdigest()[:32]


def _region(rect, page_top: float) -> dict:
    """A pdf2mmd rect (PDF points, y up) as a pdfdrill region (y down)."""
    x0, y0, x1, y1 = rect
    return {"top_left_x": round(x0, 2),
            "top_left_y": round(page_top - y1, 2),
            "width": round(x1 - x0, 2),
            "height": round(y1 - y0, 2)}


def _line_text(ln: LineNode) -> str:
    """The line as text, with mathematics in `$…$` where it renders.

    MathPix puts the LaTeX of an inline formula in the line's `text`, and
    `formula.py` mines `text` for `$…$` to place a Formula. Emitting the raw
    glyph run instead would hand the module a sentence with the maths spelled
    out in Unicode, which it cannot recognise as maths at all.
    """
    # A span is a maximal run of one KIND, and prose splits into one span per
    # WORD — so joining span texts end to end deletes every space in the
    # document ("RRSI:RegularizedRecursiveSelf-Improvement"). The gap BETWEEN
    # two spans is a word break by the same rule `_run_text` applies inside
    # one, measured against the line's word gap rather than the span's own
    # glyphs, which are too few to measure from.
    gap = ln.spans[0].word_gap if ln.spans else 0.0
    out: list[str] = []
    prev_right: float | None = None
    for sp in ln.spans:
        if (prev_right is not None and sp.rect[0] - prev_right > gap
                and out and not out[-1].endswith(" ")):
            out.append(" ")
        if sp.kind == "math":
            tex = docmodel.span_latex(sp)
            out.append(f"${tex}$" if tex else docmodel._run_text(sp.glyphs, gap))
        else:
            out.append(docmodel._run_text(sp.glyphs, gap))
        prev_right = sp.rect[2]
    return "".join(out).strip()


def _listing_rows(page: PageNode) -> dict[int, Any]:
    """{line index: the Listing it belongs to} for every line inside one.

    `listings.accumulate` measured these on the glyph grid — the monospace
    cell width, the frame rectangle, the language. A listing's lines are the
    one thing pdf2mmd reads BETTER than MathPix (674/676), and they arrived in
    pdfdrill as undifferentiated prose.
    """
    rows: dict[int, Any] = {}
    for lst in getattr(page, "listings", None) or []:
        a, b = getattr(lst, "line_start", -1), getattr(lst, "line_end", -1)
        if a < 0 or b < a:
            continue
        for i in range(a, b + 1):
            rows[i] = lst
    return rows


def _classify(ln: LineNode, index: int, page: PageNode, fp,
              left: float, right: float, listings: dict) -> tuple[str, dict]:
    """(line type, extra fields) — each from something already measured.

    Order matters, and it is the order of CERTAINTY. A listing is bounded by
    a drawn rectangle or a monospace grid, which does not depend on reading
    the text; a heading is a font-size rank; a display is an indent. The
    softest test — "these glyphs are in a maths family" — runs last, so it
    never overrides a harder one.
    """
    lst = listings.get(index)
    if lst is not None:
        extra: dict[str, Any] = {"listing_id": id(lst) & 0xFFFFFF}
        lang = getattr(lst, "language", None)
        if lang:
            extra["language"] = lang
        return ("code", extra)

    level = mmd.heading_level(ln, fp)
    if level:
        return ("section_header", {"level": level})

    if ln.rotated:
        # Sideways text is a stamp or a margin note, never part of the flow.
        return ("text", {"rotated": True})

    if mmd.is_display(ln, left, right):
        return ("equation", {})

    if ln.glyphs and sum(1 for g in ln.glyphs if g.is_math) >= 0.6 * len(ln.glyphs):
        return ("math", {})

    return ("text", {})


def emit(pages: list[PageNode], *, doc_id: str = "pdf2mmd") -> dict:
    """The whole document as a pdfdrill lines.json."""
    # The same two normalisations `to_markdown` runs before it reads anything:
    # an equation number belongs to its display, and a display that continues
    # on the next row is one equation. Skipping them would put a bare `(1)`
    # into the stream as its own line, which is what 750 removed.
    for p in pages:
        mmd.absorb_equation_numbers(p)
        mmd.mark_display_continuations(p)
    fp = mmd.profile(pages)

    out_pages = []
    for p in pages:
        page_top = p.rect[3]
        left = mmd._left_margin(p)
        right = mmd._right_margin(p)
        listings = _listing_rows(p)
        lines: list[dict] = []

        for i, ln in enumerate(p.lines):
            if not ln.glyphs:
                continue
            ltype, extra = _classify(ln, i, p, fp, left, right, listings)
            rec: dict[str, Any] = {
                "id": _lid(p.page, len(lines)),
                "type": ltype,
                "text": _line_text(ln),
                "text_display": "",
                "region": _region(ln.rect, page_top),
                "font_size": round(mmd.line_size(ln), 2),
                "line": len(lines) + 1,
                "column": mmd.column_of(p, ln)[0] if p.lines else 0,
                "conversion_output": True,
                "is_printed": True,
                "is_handwritten": False,
            }
            rec.update(extra)
            lines.append(rec)

        # A diagram is a REGION, not a line: vector art clustered by
        # `_diagram_regions`, with no glyphs of its own. It is emitted as a
        # typed line with empty text because that is how MathPix carries one,
        # and `diagram.py` / `picture.py` look for exactly that.
        for d in getattr(p, "diagrams", None) or []:
            lines.append({
                "id": _lid(p.page, len(lines)),
                "type": "diagram",
                "text": "",
                "text_display": "",
                "region": _region(d, page_top),
                "font_size": 0,
                "line": len(lines) + 1,
                "column": 0,
                "conversion_output": False,
                "is_printed": True,
                "is_handwritten": False,
            })

        out_pages.append({
            "page": p.page,
            "image_id": None,
            "page_width": round(p.rect[2] - p.rect[0], 2),
            "page_height": round(page_top - p.rect[1], 2),
            "languages_detected": [],
            "lines": lines,
        })

    return {"source": SOURCE, "total_pages": len(out_pages),
            "doc_id": doc_id, "pages": out_pages}
