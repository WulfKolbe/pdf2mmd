r"""listings — the properties of a code listing, read off the page.

781 — A LISTING IS A GRID, AND NOTHING READ IT AS ONE.

`LineNode.verbatim` has known since 748 that a line is set in a typewriter
face, and both projectors used that knowledge only to choose a wrapper: the
markdown fenced it, the LaTeX projection did not even do that and ran the
code into a paragraph. The TEXT of the line was built by `_run_text` either
way, which reconstructs word breaks from a gap THRESHOLD -- one space for
any gap wider than a fraction of the type size.

That is right for prose and wrong for code, and it is wrong in the one place
where whitespace is content:

    for (j in 0..M)
      b1[j] = a*f1[i,j]

reaches `_run_text` as two lines starting at x=56.69 and x=73.63, and comes
out flush left, because leading space is not a GAP BETWEEN GLYPHS at all --
there is no glyph to its left. Measured over the gold set before this
module, indentation survived on 24% of lines.

A monospace font gives the exact measurement the threshold was standing in
for. Every character occupies one cell, so the advance between two adjacent
glyphs is a whole number of cells:

    lst-004, 5pt ttfamily        cell 4.23pt
      'float'  f->l->o->a->t     4.23 4.23 4.23 4.23      adjacent
      'b1[M], b2[M]'   ,->b      8.47                     one space
      'for' indented 2           16.83 from the gutter    two cells
      'b1[j]' indented 4         25.31                    four cells

so the number of spaces between two glyphs is `round(dx/cell) - 1`, and the
indent of a line is `round((x0 - left)/cell)` where `left` is the column-0
edge of the block. Both are measurements, not thresholds: a cell that does
not divide the advances evenly is not a grid, and this module abstains.

What it accumulates is what `lstlisting` would have to be told to set the
page again -- the cell, the type size, the line numbers and their step, the
colours, the background -- so that the projection has something to project.
The docmodel stores it on the page (`PageNode.listings`); the projectors
read it there and never re-measure.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import lstlangs
import texmap

#: An advance is a whole number of cells to within this fraction of one.
#: Measured over the gold set: the worst residual on a true grid is 0.07
#: cells (rounding in the PDF's own coordinates); the first non-grid --
#: a proportional face wrongly measured as monospace -- is 0.31.
GRID_TOL = 0.18

#: At least this many advances must land on the grid for the block to be one.
GRID_SHARE = 0.9


@dataclass
class Run:
    """A styled stretch of one line, located in its TEXT, not its glyphs.

    781e — THE COLOURING IS A STYLE TABLE, NOT A MARK IN THE CODE.

    The first projection wrote `moredelim` markers into the body -- `!<for>!`
    -- which is exactly backwards for a listing: what a listing is FOR is a
    body a compiler could be handed. The style belongs beside the code, the
    way `lstlisting` itself says it: `keywordstyle`, `commentstyle`,
    `stringstyle` and a list of words.
    """
    start: int
    end: int
    rgb: tuple
    bold: bool = False
    italic: bool = False
    kind: str = "unknown"        # keyword | comment | string | unknown


@dataclass
class ListingLine:
    """One row of the grid."""
    id: str
    indent: int                  # cells from column 0
    text: str                    # WITHOUT the indent, gutter already dropped
    number: int | None = None    # the line number the page printed, if any
    colors: list = field(default_factory=list)   # list[Run], over `text`
    #: Blank code lines, named by the numbers the page printed for them.
    #: A blank line emits no code glyph at all, so the only thing on the
    #: page that says it exists is its number in the gutter -- and that
    #: number is grouped into the row above or below it, because it stands
    #: in the same column. See `_split_gutter`.
    blank_before: list = field(default_factory=list)
    blank_after: list = field(default_factory=list)


@dataclass
class Listing:
    """What `lstlisting` would have to be told to set this block again."""
    lines: list[ListingLine] = field(default_factory=list)
    ids: set = field(default_factory=set)        # LineNode ids covered
    cell: float = 0.0            # character advance, pt
    left: float = 0.0            # x of column 0
    size: float = 10.0           # basicstyle size, pt
    font: str = ""               # the monospace face the page used
    numbers: bool = False        # `numbers=left`
    firstnumber: int = 1
    stepnumber: int = 1
    background: tuple | None = None
    #: The listing's rectangle -- the frame the author DREW around it when
    #: there is one, and the glyph extent otherwise. `framed` says which:
    #: only the drawn frame is independent of the font, and only it
    #: separates a listing from its gutter and its caption.
    rect: tuple | None = None
    framed: bool = False
    #: What the page says the language is, and on what evidence.
    language: str = ""
    language_source: str = ""    # keywords | declared | detected | ""

    @property
    def code(self) -> str:
        """THE PROGRAM. Plain text, no markup, tabs and spaces as measured.

        This is the field a compiler or an interpreter could be handed, and
        the reason a listing is not an equation: mathematics wants markup,
        and code wants to be left alone.
        """
        return self.text()

    @property
    def keywords(self) -> list:
        """(word, rgb, bold, italic) for every word the page STYLED.

        `keywordstyle` paints a word iff it is in the keyword list of the
        language the author named, so this is both the style table and the
        strongest evidence on the page about which language that was --
        see `out/lstkeywords.py`.
        """
        out, seen = [], set()
        for ln in self.lines:
            for r in ln.colors:
                if r.kind != "keyword":
                    continue
                w = ln.text[r.start:r.end].strip()
                key = (w, r.rgb, r.bold, r.italic)
                if w and key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    def style_of(self, kind: str):
        """(rgb, bold, italic) the page used for comments / strings, or None."""
        seen: dict = {}
        for ln in self.lines:
            for r in ln.colors:
                if r.kind == kind:
                    seen[(r.rgb, r.bold, r.italic)] = seen.get(
                        (r.rgb, r.bold, r.italic), 0) + 1
        if not seen:
            return None
        return max(seen, key=seen.get)

    @property
    def colors(self) -> list:
        """Distinct colours used, in first-seen order."""
        seen: list = []
        for ln in self.lines:
            for r in ln.colors:
                if r.rgb is not None and r.rgb not in seen:
                    seen.append(r.rgb)
        return seen

    def rows(self) -> list:
        """(number, text) for every line the page shows, blanks included."""
        out: list = []
        for ln in self.lines:
            for n in ln.blank_before:
                out.append((n, ""))
            out.append((ln.number, " " * ln.indent + ln.text))
            for n in ln.blank_after:
                out.append((n, ""))
        return out

    def text(self) -> str:
        return "\n".join(t for _n, t in self.rows())


# --------------------------------------------------------------- the grid
def _mono(glyphs) -> list:
    return [g for g in glyphs if texmap.is_monospace(g.fontname)]


def cell_width(glyphs) -> float | None:
    """The character cell of a monospace run, or None if it is not a grid.

    The cell is the SMALLEST advance that occurs often, not the average: a
    listing whose lines are mostly single words would otherwise measure its
    cell as the word gap. Every other advance must then be a whole number of
    it -- that is the test that says this really is a grid.
    """
    adv = []
    for a, b in zip(glyphs, glyphs[1:]):
        d = b.rect[0] - a.rect[0]
        if d > 0.2:
            adv.append(d)
    if len(adv) < 4:
        return None
    base = min(statistics.multimode([round(d, 1) for d in adv]))
    if base <= 0.2:
        return None
    on = sum(1 for d in adv if abs(d / base - round(d / base)) <= GRID_TOL)
    if on < GRID_SHARE * len(adv):
        return None
    # Refine on the advances that ARE one cell: the mode is rounded to 0.1pt
    # and the indent of a 40-line block multiplies that error by six.
    ones = [d for d in adv if abs(d / base - 1.0) <= GRID_TOL]
    return statistics.median(ones) if ones else base


def grid_text(glyphs, cell: float) -> str:
    """The run's text with every space it shows, counted in cells."""
    if not glyphs:
        return ""
    out = [glyphs[0].text]
    for a, b in zip(glyphs, glyphs[1:]):
        n = int(round((b.rect[0] - a.rect[0]) / cell)) - 1
        if n > 0:
            out.append(" " * n)
        out.append(b.text)
    return "".join(out)


#: How a comment opens, across the languages the gold set carries. A
#: comment is recognised by SHAPE, not by the language: it opens with one
#: of these and runs to the end of the line, which no keyword ever does.
_COMMENT_OPEN = ("//", "#", "%", "--", ";", "/*", "<!--", "!", "'")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


def _kind(text: str, run) -> str:
    """keyword | string | unknown for ONE run. Comments are decided by line.

    A string is quoted at both ends; a keyword is one bare word. Anything
    else is left unknown rather than guessed -- a style table with a wrong
    entry recolours the wrong token on every page that uses it.
    """
    body = text[run.start:run.end].strip()
    if not body:
        return "unknown"
    if len(body) >= 2 and body[0] == body[-1] and body[0] in "\"'`":
        return "string"
    if _IDENT.match(body):
        return "keyword"
    return "unknown"


def _classify(text: str, runs: list) -> list:
    """Name every run on a line, COMMENTS FIRST.

    A comment is a property of the LINE, not of a run. The grid inserts the
    spaces it measured, so a styled comment arrives as one run per word:

        `// set the gain`  ->  '//'  'set'  'the'  'gain'

    and each of `set`, `the`, `gain` is a bare word, so per-run
    classification files three English words in the keyword table -- which
    then recolours them as keywords on every page that table is used for.

    What separates a comment from a run of keywords is that the comment
    REACHES THE END OF THE LINE and opens with a marker, and that no
    keyword sequence does both. So the tail is taken first and merged into
    one run; whatever is left is classified run by run. `public static
    void` stays three keywords, because it opens with none of the markers.
    """
    if not runs:
        return runs
    end = len(text.rstrip())
    for i, r in enumerate(runs):
        if not text[r.start:r.end].strip().startswith(_COMMENT_OPEN):
            continue
        tail = runs[i:]
        style = (tail[0].rgb, tail[0].bold, tail[0].italic)
        if tail[-1].end < end:
            continue
        if any((t.rgb, t.bold, t.italic) != style for t in tail):
            continue
        merged = Run(r.start, tail[-1].end, r.rgb, r.bold, r.italic, "comment")
        runs = runs[:i] + [merged]
        break
    for r in runs:
        if r.kind == "unknown":
            r.kind = _kind(text, r)
    return runs


def _color_runs(glyphs, cell: float) -> list:
    """(start, end, rgb) over `text` for every non-black run.

    Indices are into the text `grid_text` built from the same glyphs and the
    same cell, so the spaces it inserted are counted: a colour that started one character late painted the wrong
    token, which is worse than no colour at all.
    """
    runs: list = []
    pos = 0
    prev = None
    for g in glyphs:
        if prev is not None:
            pos += max(0, int(round((g.rect[0] - prev.rect[0]) / cell)) - 1)
        start = pos
        pos += len(g.text)
        prev = g
        rgb = g.color
        if rgb is None or (max(rgb) < 0.02 and min(rgb) < 0.02):
            rgb = None
        bold = texmap.is_bold(g.fontname)
        italic = texmap.is_italic(g.fontname)
        if (runs and runs[-1].rgb == rgb and runs[-1].end == start
                and runs[-1].bold == bold and runs[-1].italic == italic):
            runs[-1].end = pos
        else:
            runs.append(Run(start, pos, rgb, bold, italic))
    # A run in the basic style is not a style: it is the listing.
    return [r for r in runs if r.rgb is not None or r.bold or r.italic]


# ----------------------------------------------------------- the gutter
def _gutter(glyphs, size: float):
    """(number glyphs, code glyphs) for a line that shows a line number.

    `lstlisting` sets the number in `numberstyle`, which is a SMALLER size
    than the code and a different face, and puts it in its own column to the
    left of every code glyph. Both must hold: a comment set in \\tiny is not
    a line number, and neither is a small superscript inside the code.
    """
    lead = []
    for g in glyphs:
        if g.text.strip().isdigit() and g.size < 0.85 * size:
            lead.append(g)
        else:
            break
    if not lead:
        return [], list(glyphs)
    rest = glyphs[len(lead):]
    if not rest:
        return [], list(glyphs)
    return lead, rest


def _split_gutter(lead, code, size: float):
    """(before, number, after) -- the gutter holds MORE than this line.

    A blank code line puts nothing on the page but its number, in the same
    column as every other number, so `_group_lines` folds it into the
    neighbouring row. lst-021 came back with `12`, `89` and `1134` where the
    page shows 1, 8 and 11: two numbers of adjacent lines read as one number
    of two digits, and the blank lines between them gone.

    The baselines separate them, and nothing else does -- x cannot, because
    that is exactly what they share:

        '1' x=48.3 base=725.34      the number of THIS line
        '2' x=48.3 base=718.72      a blank line below it
        '/' x=56.7 base=725.34      the code

    A digit on the code's own baseline is this line's number; one below it
    belongs to a blank line that follows, one above to a blank line before.
    """
    if not lead:
        return [], None, []
    base = statistics.median([g.baseline for g in code])
    tol = 0.5 * size
    bands: dict = {}
    for g in lead:
        key = round((g.baseline - base) / max(tol, 0.1))
        bands.setdefault(key, []).append(g)
    own = _number(bands.pop(0, []))
    before = [_number(bands[k]) for k in sorted(bands) if k > 0]
    after = [_number(bands[k]) for k in sorted(bands, reverse=True) if k < 0]
    return ([x for x in before if x is not None], own,
            [x for x in after if x is not None])


def _number(lead) -> int | None:
    t = "".join(g.text for g in lead).strip()
    return int(t) if t.isdigit() else None


# ------------------------------------------------------------- the frame
#: A frame rule is thin. `_is_plain_rule` has already said so; this is the
#: side it is thin on.
def _horizontal(r) -> bool:
    return (r.rect[3] - r.rect[1]) <= 2.0 and (r.rect[2] - r.rect[0]) > 20.0


def _vertical(r) -> bool:
    return (r.rect[2] - r.rect[0]) <= 2.0 and (r.rect[3] - r.rect[1]) > 2.0


def _columns(vert) -> list:
    """(x, y_bottom, y_top) for each stack of vertical segments.

    `frame=single` does NOT draw one tall rule down each side. listings sets
    the frame line by line, so a twelve-line listing has twelve segments per
    side, each one line high and abutting the next:

        lst-027   x=52.91   714.39..725.34
                  x=52.91   703.43..714.39
                  x=52.91   692.47..703.43

    Read segment by segment, no side of the frame is ever as tall as the
    box; stacked, they are.
    """
    by_x: dict = {}
    for r in vert:
        by_x.setdefault(round(r.rect[0], 0), []).append(r)
    out = []
    for seg in by_x.values():
        seg.sort(key=lambda r: r.rect[1])
        # The KEY is rounded so segments a hundredth apart stack; the x that
        # comes back is the one actually drawn.
        x = min(r.rect[0] for r in seg)
        lo, hi = seg[0].rect[1], seg[0].rect[3]
        for r in seg[1:]:
            if r.rect[1] <= hi + 1.0:
                hi = max(hi, r.rect[3])
            else:
                out.append((x, lo, hi))
                lo, hi = r.rect[1], r.rect[3]
        out.append((x, lo, hi))
    return out


def frames(rules, span_pt: float = 10.0) -> list:
    r"""Rectangles drawn on the page that could enclose a listing.

    The frame is the one boundary that does not depend on the font, and it
    is the only thing that separates a listing from what surrounds it: the
    glyph extent cannot. lst-027 --

        frame     52.91 688.48 559.09 729.33
        glyphs    48.32 688.28 566.86 739.09

    -- is wider on the left because `numbers=left` sets the gutter OUTSIDE
    the frame, wider on the right because of an escaped `\label`, and taller
    because the caption sits above the box. A reader that takes the glyph
    extent for the listing takes the caption with it.

    A pair of horizontal rules of the same width, far enough apart, is the
    candidate; vertical stacks at either end extend it sideways where the
    author asked for a full box. `frame=tb` draws only the pair, which is
    why the verticals are optional.
    """
    horiz = sorted((r for r in rules if _horizontal(r)), key=lambda r: -r.rect[1])
    cols = _columns([r for r in rules if _vertical(r)])
    out: list = []
    for i, a in enumerate(horiz):
        for b in horiz[i + 1:]:
            if (abs(a.rect[0] - b.rect[0]) > 2.0
                    or abs(a.rect[2] - b.rect[2]) > 2.0):
                continue
            lo, hi = b.rect[1], a.rect[3]
            if hi - lo < 1.5 * span_pt:
                continue
            x0, x1 = a.rect[0], a.rect[2]
            # A side counts when its stack covers the box to within one line:
            # `framesep` leaves a gap of about 4pt at each end.
            for cx, cy0, cy1 in cols:
                if cy0 > lo + span_pt or cy1 < hi - span_pt:
                    continue
                if abs(cx - x0) < 2.0 * span_pt:
                    x0 = min(x0, cx)
                elif abs(cx - x1) < 2.0 * span_pt:
                    x1 = max(x1, cx)
            out.append((x0, lo, x1, hi))
            break         # the NEXT rule down closes this box, not a later one
    return out


# --------------------------------------------------------------- the pass
def _continues(ln) -> bool:
    """A short typewriter line inside an open block is still code.

    `LineNode.verbatim` needs three alphanumerics before it will call a line
    monospace, because two letters are not evidence of a face. That is the
    right rule for opening a block and the wrong one for continuing it: a
    listing whose third line is `}` or `*` or `%` would be cut in half by
    it. lst-001 and lst-002 are two content lines with exactly such a line
    between them, and produced no block at all.
    """
    return bool(ln.glyphs) and all(texmap.is_monospace(g.fontname)
                                   for g in ln.glyphs if g.text.strip())


def _columns_of(run, spans) -> list:
    """Split a run of lines into COLUMNS, by horizontal overlap.

    781g — A BLOCK THAT CROSSED THE GUTTER MEASURED ITS INDENT FROM THE
    OTHER COLUMN. Page 5 of 1804.10694v5 sets four listings in two columns;
    read in band order, a verbatim run picks up lines from both, and `left`
    -- the x of column 0 -- then comes from whichever column starts further
    left. Every right-column line came back indented by 56 spaces.

    Indentation can never be mistaken for a column here, because the test
    is not x0 but OVERLAP: inside one column the lines overlap, since a
    long line spans the indent of every other. Between columns nothing
    overlaps at all -- the left column ends before the right begins. So the
    split is the classic interval partition, and it is exact:

        listing 3   x0 314.0 316.5 395.7          one column
        listing 2   x0  60.5  70.7  73.2 314.0 ... two

    A part of one line is not a column; those lines stay with the block
    they came from rather than becoming a listing of their own.
    """
    order = sorted(range(len(run)), key=lambda i: spans[i][0])
    parts, cur, edge = [], [order[0]], spans[order[0]][1]
    for i in order[1:]:
        if spans[i][0] > edge:
            parts.append(cur)
            cur, edge = [i], spans[i][1]
        else:
            cur.append(i)
            edge = max(edge, spans[i][1])
    parts.append(cur)
    if len(parts) < 2 or any(len(x) < 2 for x in parts):
        return [run]
    # Reading order inside each column is the order they arrived in.
    return [[run[i] for i in sorted(part)] for part in parts]


def _blocks(page) -> list:
    """Maximal runs of verbatim lines, in reading order."""
    out: list = []
    run: list = []
    for ln in page.lines:
        if ln.rotated:
            keep = False
        elif ln.verbatim:
            keep = True
        else:
            keep = bool(run) and _continues(ln)
        if keep:
            run.append(ln)
        else:
            if len(run) >= 2:
                out.append(run)
            run = []
    if len(run) >= 2:
        out.append(run)
    # A trailing continuation line is not evidence of anything on its own.
    out = [r if r[-1].verbatim else r[:-1] for r in out
           if len(r) >= 2 and (r[-1].verbatim or len(r) > 2)]
    split: list = []
    for r in out:
        spans = [(min(g.rect[0] for g in ln.glyphs),
                  max(g.rect[2] for g in ln.glyphs)) for ln in r]
        split += [c for c in _columns_of(r, spans) if len(c) >= 2]
    return split


def _fill_under(page, rect):
    """The page's own background colour behind a block, if it has one."""
    best = None
    for f in getattr(page, "fills", []):
        r = f.rect
        if (r[0] <= rect[0] + 2 and r[2] >= rect[2] - 2
                and r[1] <= rect[1] + 2 and r[3] >= rect[3] - 2):
            if f.color is not None and min(f.color) < 0.995:
                best = f.color
    return best


def _row_pitch(run) -> float:
    """The distance between two rows of this block, measured."""
    base = sorted({round(statistics.median([g.baseline for g in ln.glyphs]), 2)
                   for ln in run if ln.glyphs}, reverse=True)
    gaps = [a - b for a, b in zip(base, base[1:]) if a - b > 0.5]
    return statistics.median(gaps) if gaps else 0.0


def _merge_rows(run) -> list:
    """[(line, glyphs)] with lines that share a ROW merged into one.

    781g — A GLYPH SET 1.4pt LOW IS NOT A LINE OF ITS OWN.

    `listings` does not set every character on the row's baseline. In
    1804.10694v5 the multiplication star of `i0*32+i1` is placed 1.39pt
    below the code it belongs to, and line grouping -- which has no idea
    it is looking at a grid -- made it a line:

        base 676.88   'int i = i0 32+i1'
        base 675.49   '*'

    so the listing came back with the star on its own row and a hole where
    it should be, four times on one page. Against the author's own source
    that was 4 of 22 lines wrong, and it was the ONLY thing wrong.

    A block knows its row pitch -- here 7.17pt -- and 1.39 is a fifth of
    it. Nothing closer than half a pitch can be a separate row.
    """
    pitch = _row_pitch(run)
    if pitch <= 0:
        return [(ln, list(ln.glyphs)) for ln in run]
    rows: list = []
    for ln in run:
        if not ln.glyphs:
            continue
        base = statistics.median([g.baseline for g in ln.glyphs])
        for row in rows:
            if abs(row[2] - base) < 0.5 * pitch:
                row[1].extend(ln.glyphs)
                break
        else:
            rows.append([ln, list(ln.glyphs), base])
    return [(r[0], sorted(r[1], key=lambda g: g.rect[0])) for r in rows]


def accumulate(page) -> list:
    """Every listing on the page, with the properties that would set it."""
    out: list = []
    boxes: list = []
    for run in _blocks(page):
        glyphs = [g for ln in run for g in ln.glyphs]
        mono = _mono(glyphs)
        if len(mono) < 8:
            continue
        size = statistics.median([g.size for g in mono])
        cell = cell_width([g for ln in run
                           for g in sorted(ln.glyphs, key=lambda g: g.rect[0])
                           if texmap.is_monospace(g.fontname)])
        if cell is None:
            continue
        rows: list = []
        numbered = 0
        for ln, gs in _merge_rows(run):
            lead, code = _gutter(gs, size)
            if lead:
                numbered += 1
            while code and not code[0].text.strip():
                code = code[1:]     # a leading space glyph IS the indent
            while code and not code[-1].text.strip():
                code = code[:-1]
            if not code:
                continue
            rows.append((ln, lead, code))
        if not rows:
            continue
        left = min(c[0].rect[0] for _ln, _lead, c in rows)
        lst = Listing(cell=cell, left=left, size=size,
                      font=statistics.mode([g.fontname for g in mono]),
                      numbers=numbered >= 0.8 * len(rows))
        for ln, lead, code in rows:
            text = grid_text(code, cell)
            indent = int(round((code[0].rect[0] - left) / cell))
            before, own, after = _split_gutter(lead, code, size)
            lst.lines.append(ListingLine(
                id=ln.id, indent=max(0, indent), text=text.rstrip(),
                number=own, blank_before=before, blank_after=after,
                colors=_color_runs(code, cell)))
            _row = lst.lines[-1]
            # UNINDENTED: `_color_runs` counts from the first code glyph, so
            # a run's offsets index `text`, not the indented row. Handing
            # `_classify` the indented string shifted every slice by the
            # indent and filed `[`, `{` and `}` as keywords -- which reached
            # `morekeywords={[1]{[,{,}}}` and made the .tex uncompilable.
            _row.colors = _classify(_row.text, _row.colors)
            lst.ids.add(ln.id)
        lst.ids |= {ln.id for ln in run}
        nums = [n for x in lst.lines
                for n in (x.blank_before + [x.number] + x.blank_after)
                if n is not None]
        if lst.numbers and nums:
            lst.firstnumber = nums[0]
            steps = [b - a for a, b in zip(nums, nums[1:]) if b > a]
            lst.stepnumber = min(steps) if steps else 1
        rect = (min(g.rect[0] for g in glyphs), min(g.rect[1] for g in glyphs),
                max(g.rect[2] for g in glyphs), max(g.rect[3] for g in glyphs))
        lst.background = _fill_under(page, rect)
        lst.rect = rect
        lst.language, lst.language_source = _language(lst)
        boxes.append(rect)
        out.append(lst)
    # The frames are assigned once every block is known: a frame is a
    # listing's only when it is around that listing ALONE.
    for i, lst in enumerate(out):
        drawn = _encloses(getattr(page, "frames", ()), boxes[i],
                          [b for j, b in enumerate(boxes) if j != i])
        if drawn:
            lst.rect, lst.framed = drawn, True
    return out


#: The coverage below which the words seen do not name a language. A
#: listing that colours `for` and `float` is covered 100% by Modula-2,
#: Java and C alike; saying one of them would be inventing evidence.
_LANG_COVER = 0.75


def _language(lst) -> tuple:
    """(language, source) from the words the page STYLED, or ("", "").

    781e — ASK THE PACKAGE THAT PAINTED THE PAGE.

    `keywordstyle` colours a word if and only if that word is in the
    keyword list of the language the author named, so the coloured words
    are a SUBSET of one language's keywords and the language can be looked
    up rather than guessed. `listings` ships 94 of those lists; `lstlangs`
    reads them out, the same move `texmap` makes with `mathabx.dcl`.

    Each style group is tried alone -- a page has a keyword colour, a
    comment colour and a string colour, and only one of them is keywords.
    Two languages that fit equally well is an abstention: measured over the
    112 gold listings whose declared language `listings` also defines, this
    answered 34 and was right 34 times, abstaining on the rest. A reader
    that is silent when the page is silent is worth more than one that
    guesses.
    """
    table = lstlangs.load()
    groups: dict = {}
    for word, rgb, bold, italic in lst.keywords:
        groups.setdefault((rgb, bold, italic), set()).add(word)
    best, cover = "", 0.0
    for words in groups.values():
        if len(words) < 2:
            continue
        r = lstlangs.rank(words, table, top=2)
        if not r or (len(r) > 1 and abs(r[1][1] - r[0][1]) < 1e-9):
            continue
        if r[0][1] > cover:
            best, cover = r[0][0], r[0][1]
    return (best, "keywords") if cover >= _LANG_COVER else ("", "")


def _inside(frame, rect) -> bool:
    return (frame[0] <= rect[0] + 2 and frame[2] >= rect[2] - 2
            and frame[1] <= rect[1] + 2 and frame[3] >= rect[3] - 2)


def _encloses(frames, rect, others) -> tuple | None:
    """The smallest drawn frame around `rect` ALONE, or None.

    781g — A BOX AROUND TWO LISTINGS IS NOT EITHER LISTING'S FRAME.

    Page 5 of 1804.10694v5 draws three page-wide rules that band the
    figures, and each band holds two listings side by side. Taking the
    smallest enclosing rectangle gave BOTH of them the same rect and
    called both framed -- which is exactly the over-claim the frame was
    introduced to avoid, since the whole point of a drawn boundary is that
    it says which glyphs belong to WHICH block.

    So a frame is this listing's only when it contains this listing and no
    other. Smallest still wins among those, because a listing in a table
    cell sits inside two rectangles and the inner one is its own.
    """
    fit = [f for f in frames
           if _inside(f, rect) and not any(_inside(f, o) for o in others)]
    if not fit:
        return None
    return min(fit, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))


def listing_of(page, line):
    """The listing this line belongs to, or None."""
    for lst in getattr(page, "listings", ()):
        if line.id in lst.ids:
            return lst
    return None
