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

import statistics
from dataclasses import dataclass, field

import texmap

#: An advance is a whole number of cells to within this fraction of one.
#: Measured over the gold set: the worst residual on a true grid is 0.07
#: cells (rounding in the PDF's own coordinates); the first non-grid --
#: a proportional face wrongly measured as monospace -- is 0.31.
GRID_TOL = 0.18

#: At least this many advances must land on the grid for the block to be one.
GRID_SHARE = 0.9


@dataclass
class ListingLine:
    """One row of the grid."""
    id: str
    indent: int                  # cells from column 0
    text: str                    # WITHOUT the indent, gutter already dropped
    number: int | None = None    # the line number the page printed, if any
    colors: list = field(default_factory=list)   # (start, end, rgb) in `text`
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

    @property
    def colors(self) -> list:
        """Distinct colours used, in first-seen order."""
        seen: list = []
        for ln in self.lines:
            for _s, _e, rgb in ln.colors:
                if rgb not in seen:
                    seen.append(rgb)
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


def _color_runs(glyphs, cell: float) -> list:
    """(start, end, rgb) over `text` for every non-black run.

    Indices are into the text `grid_text` built from the same glyphs and the
    same cell, so the spaces it inserted are counted: a colour that started one character late painted the wrong
    token, which is worse than no colour at all.
    """
    runs: list = []
    pos = 0
    prev = None
    for i, g in enumerate(glyphs):
        if prev is not None:
            pos += max(0, int(round((g.rect[0] - prev.rect[0])
                                    / cell)) - 1)
        start = pos
        pos += len(g.text)
        prev = g
        rgb = g.color
        if rgb is None or max(rgb) < 0.02 and min(rgb) < 0.02:
            rgb = None
        if runs and runs[-1][2] == rgb and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], pos, rgb)
        else:
            runs.append((start, pos, rgb))
    return [(a, b, c) for a, b, c in runs if c is not None]


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
    return [r if r[-1].verbatim else r[:-1] for r in out
            if len(r) >= 2 and (r[-1].verbatim or len(r) > 2)]


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


def accumulate(page) -> list:
    """Every listing on the page, with the properties that would set it."""
    out: list = []
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
        for ln in run:
            gs = sorted(ln.glyphs, key=lambda g: g.rect[0])
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
            lst.ids.add(ln.id)
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
        out.append(lst)
    return out


def listing_of(page, line):
    """The listing this line belongs to, or None."""
    for lst in getattr(page, "listings", ()):
        if line.id in lst.ids:
            return lst
    return None
