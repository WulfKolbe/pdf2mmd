r"""Tests for the listing grid -- 781.

The measurements quoted here are `lst-004` of the gold set (1804.10694v5,
`texsrc/archive.tex` line 18), read at 5pt `numberstyle` and 8pt `ttfamily`:

    cell 4.23pt    one space  8.47 (2 cells)
                   indent 2  16.83 (4 cells from the gutter glyph)
                   indent 4  25.31 (6 cells)
"""
import listings as L
import project_mmd as M
import texmap
from docmodel_six import GlyphNode, LineNode, PageNode
from texmap import project

CELL = 4.23


def g(ch, x, size=8.0, font="TEST+NimbusMonL-Regu", color=None, y=100.0):
    return GlyphNode(
        id="g%s%.0f" % (ch, x), page=1,
        rect=(x, y, x + 0.6 * size, y + size),
        text=ch, cid=ord(ch), glyphname=None, fontname=font,
        family="text", size=size, tex=project("text", None),
        matrix=(size, 0, 0, size, x, y), color=color)


def row(text, x0, y=100.0, size=8.0, color=None, font="TEST+NimbusMonL-Regu"):
    """A line of monospace glyphs laid out on the 4.23pt grid."""
    gs = [g(c, x0 + i * CELL, size, font, color, y)
          for i, c in enumerate(text) if c != " "]
    return LineNode(id="l%.0f" % y, page=1, rect=(x0, y, x0 + len(text) * CELL,
                                                  y + size),
                    type="text", glyphs=gs)


class TestTheCell:
    def test_cell_is_the_advance(self):
        ln = row("float b1", 56.69)
        assert abs(L.cell_width(ln.glyphs) - CELL) < 0.05

    def test_a_proportional_run_is_not_a_grid(self):
        """Advances that are not whole multiples of anything: abstain."""
        gs = []
        x = 50.0
        for i, c in enumerate("proportional text here"):
            if c == " ":
                x += 2.3
                continue
            gs.append(g(c, x))
            x += 3.1 + 0.9 * (i % 3)
        assert L.cell_width(gs) is None

    def test_too_few_advances_to_measure(self):
        assert L.cell_width(row("ab", 50.0).glyphs) is None


class TestTheSpacesTheGridShows:
    def test_one_space_is_one_empty_cell(self):
        assert L.grid_text(row("a = 1", 50.0).glyphs, CELL) == "a = 1"

    def test_a_run_of_spaces_is_counted(self):
        assert L.grid_text(row("a    b", 50.0).glyphs, CELL) == "a    b"

    def test_no_space_between_adjacent_glyphs(self):
        assert L.grid_text(row("float", 50.0).glyphs, CELL) == "float"


def _page(rows):
    p = PageNode(page=1, rect=(0, 0, 595, 842), lines=rows)
    p.listings = L.accumulate(p)
    return p


class TestTheIndentIsAMeasurement:
    def test_indent_counted_from_column_zero(self):
        """The very loss this module exists for: leading space is not a gap
        between glyphs, so `_run_text` could never see it."""
        p = _page([row("for (i in 0..N)", 56.69, y=300.0),
                   row("for (j in 0..M)", 56.69 + 2 * CELL, y=290.0),
                   row("b1[j] = a*f1[i,j]", 56.69 + 4 * CELL, y=280.0)])
        assert len(p.listings) == 1
        assert [x.indent for x in p.listings[0].lines] == [0, 2, 4]

    def test_the_block_text_carries_it(self):
        p = _page([row("for (i in 0..N)", 56.69, y=300.0),
                   row("b1[j] = 1", 56.69 + 2 * CELL, y=290.0)])
        assert p.listings[0].text() == "for (i in 0..N)\n  b1[j] = 1"


class TestTheGutterIsNotCode:
    def _numbered(self):
        rows = []
        for i, (text, ind) in enumerate([("for (i in 0..N)", 0),
                                         ("for (j in 0..M)", 2),
                                         ("b1[j] = 1", 4)]):
            y = 300.0 - 10 * i
            ln = row(text, 56.69 + ind * CELL, y=y)
            # the line number: SMALLER type, to the left of every code glyph
            ln.glyphs.insert(0, g(str(i + 1), 48.32, size=5.0, y=y))
            rows.append(ln)
        return rows

    def test_the_number_is_read_and_kept_apart(self):
        lst = _page(self._numbered()).listings[0]
        assert lst.numbers
        assert [x.number for x in lst.lines] == [1, 2, 3]
        assert lst.firstnumber == 1 and lst.stepnumber == 1

    def test_the_number_is_not_in_the_code(self):
        lst = _page(self._numbered()).listings[0]
        assert lst.lines[0].text == "for (i in 0..N)"

    def test_the_number_does_not_move_column_zero(self):
        """The gutter sits left of the code, so measuring the indent from it
        would make every line indented by the same wrong amount."""
        lst = _page(self._numbered()).listings[0]
        assert [x.indent for x in lst.lines] == [0, 2, 4]

    def test_a_listing_without_numbers_says_so(self):
        p = _page([row("for (i in 0..N)", 56.69, y=300.0),
                   row("b1[j] = 1", 56.69, y=290.0)])
        assert not p.listings[0].numbers


class TestColour:
    def test_a_coloured_run_is_located_in_the_text(self):
        ln = row("for (i in 0..N)", 56.69, y=300.0)
        for gl in ln.glyphs[:3]:
            gl.color = (1.0, 0.4, 0.0)
        p = _page([ln, row("b1[j] = 1", 56.69, y=290.0)])
        lst = p.listings[0]
        assert lst.colors == [(1.0, 0.4, 0.0)]
        s, e, rgb = lst.lines[0].colors[0]
        assert lst.lines[0].text[s:e] == "for"

    def test_black_is_not_a_colour(self):
        p = _page([row("for (i in 0..N)", 56.69, y=300.0),
                   row("b1[j] = 1", 56.69, y=290.0)])
        assert p.listings[0].colors == []


class TestTheBlock:
    def test_a_short_line_inside_a_block_does_not_end_it(self):
        """lst-001: two content lines with `%` between them produced no block
        at all, because `verbatim` wants three alphanumerics."""
        p = _page([row("for (i in 0..N)", 56.69, y=300.0),
                   row("%", 56.69, y=290.0),
                   row("b1[j] = 1", 56.69, y=280.0)])
        assert len(p.listings) == 1
        assert len(p.listings[0].lines) == 3

    def test_one_line_is_not_a_listing(self):
        p = _page([row("for (i in 0..N)", 56.69, y=300.0)])
        assert p.listings == []

    def test_prose_is_not_a_listing(self):
        p = _page([row("the quick brown fox", 56.69, y=300.0,
                       font="TEST+NimbusRomNo9L-Regu"),
                   row("jumped over the dog", 56.69, y=290.0,
                       font="TEST+NimbusRomNo9L-Regu")])
        assert p.listings == []


class TestTheLatexProjection:
    def _lst(self):
        ln = row("for (i in 0..N)", 56.69, y=300.0)
        for gl in ln.glyphs[:3]:
            gl.color = (1.0, 0.4, 0.0)
        return _page([ln, row("b1[j] = a*f1[i,j]", 56.69 + 2 * CELL,
                              y=290.0)]).listings[0]

    def test_it_is_a_listing_environment(self):
        tex = M._listing_tex(self._lst())
        assert r"\begin{lstlisting}" in tex and r"\end{lstlisting}" in tex

    def test_the_indent_reaches_the_file(self):
        tex = M._listing_tex(self._lst())
        assert "\n  b1[j] = a*f1[i,j]" in tex

    def test_keepspaces_or_latex_respaces_it(self):
        tex = M._listing_tex(self._lst())
        assert "keepspaces=true" in tex and "columns=fullflexible" in tex

    def test_colour_is_carried_by_an_invisible_delimiter(self):
        """Not `escapeinside`: an escape leaves listing mode, and then every
        `_`, `#` and `&` in the run would have to be escaped."""
        tex = M._listing_tex(self._lst())
        assert r"moredelim={**[is]" in tex
        assert "!<for>!" in tex

    def test_the_moredelim_value_is_braced(self):
        r"""Unbraced, the `]` inside it ends the environment's own optional
        argument and LaTeX produces NO PDF:
        `! File ended while scanning use of \lst@Delim@delim.`"""
        tex = M._listing_tex(self._lst())
        assert "moredelim=**" not in tex

    def test_a_marker_that_collides_is_not_used(self):
        ln = row("for (i !< 0..N)", 56.69, y=300.0)
        for gl in ln.glyphs[:3]:
            gl.color = (1.0, 0.4, 0.0)
        lst = _page([ln, row("b1[j] = 1", 56.69, y=290.0)]).listings[0]
        tex = M._listing_tex(lst)
        assert "!<for>!" not in tex
        assert "?<for>?" in tex


class TestTheFontNameThatOnlyTheMeasurementKnew:
    def test_sftt_is_typewriter(self):
        """cm-super's T1 typewriter. lst-278: 21,928 advances, 51 letters,
        advance/size 0.531 on 100.0% of them."""
        assert texmap.is_monospace("VWKTCM+SFTT0900")

    def test_sfrm_is_not(self):
        assert not texmap.is_monospace("XADWKO+SFRM0500")
