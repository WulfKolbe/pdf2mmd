r"""Tests for the pdfdrill lines.json emitter — 784.

What this file pins is the boundary between two programs. pdf2mmd reads the
glyphs; pdfdrill's twenty docmodel modules build `Table`, `Section`,
`CodeListing`, `Formula` objects from TYPED LINES, and every projector reads
those objects. So the only thing that has to be right here is the line: its
type, its text, and its rectangle in pdfdrill's coordinate system.

Measured on 2609.24972 (24pp) through the same pipeline, non-arXiv lane:

    pdfdrill's own chars_to_lines   128 objects   127 boxed
    pdf2mmd's glyphlines            417 objects   416 boxed
                                    + Section 45, Formula 160,
                                      CodeListing 2, Diagram 1
"""
import linesjson
from docmodel_six import GlyphNode, LineNode, PageNode
from texmap import project


def g(ch, x, y=100.0, size=10.0, font="TEST+NimbusRomNo9L-Regu"):
    return GlyphNode(
        id=f"g{ch}{x:.0f}", page=1,
        rect=(x, y, x + 0.5 * size, y + size),
        text=ch, cid=ord(ch), glyphname=None, fontname=font,
        family="text", size=size, tex=project("text", None),
        matrix=(size, 0, 0, size, x, y))


def words(text, x0=72.0, y=700.0, size=10.0):
    """Lay words out with a real word space between them, as TeX does —
    positioned, never an emitted space glyph."""
    gs, x = [], x0
    for i, w in enumerate(text.split()):
        if i:
            x += 0.5 * size          # the word space
        for ch in w:
            gs.append(g(ch, x, y, size))
            x += 0.5 * size
    return LineNode(id=f"l{y:.0f}", page=1, rect=(x0, y, x, y + size),
                    type="text", glyphs=gs)


def page(lines, width=595.0, height=842.0):
    return PageNode(page=1, rect=(0.0, 0.0, width, height), lines=lines)


def doc(lines, **kw):
    """`emit` takes the whole document — a list of pages."""
    return [page(lines, **kw)]


class TestTheText:
    def test_words_keep_their_spaces(self):
        """A span is a maximal run of ONE KIND, and prose splits one span per
        WORD. Joining span texts end to end deleted every space in the
        document — `RRSI:RegularizedRecursiveSelf-Improvement` — and the
        damage was invisible in the line count: pdfdrill's text matcher fell
        from 86 placed objects to 7 and nothing else changed."""
        ln = words("Our contributions are threefold")
        assert linesjson._line_text(ln) == "Our contributions are threefold"

    def test_an_empty_line_is_not_emitted(self):
        out = linesjson.emit(doc([LineNode(id="x", page=1,
                                            rect=(0, 0, 1, 1), type="text")]))
        assert out["pages"][0]["lines"] == []


class TestTheRectangle:
    def test_the_origin_moves_to_the_top_left(self):
        """pdf2mmd measures in PDF points with y UP. A pdfdrill Region for any
        non-MathPix source is PDF points, TOP-LEFT, y DOWN — the lane served
        from our own pyramid. Getting this wrong puts every box on the page
        upside down, which reads as 'the boxes are wrong' and not as 'the
        units are'."""
        r = linesjson._region((72.0, 700.0, 500.0, 710.0), page_top=842.0)
        assert r == {"top_left_x": 72.0, "top_left_y": 132.0,
                     "width": 428.0, "height": 10.0}

    def test_a_line_carries_its_own_box(self):
        out = linesjson.emit(doc([words("Our contributions are threefold")]))
        ln = out["pages"][0]["lines"][0]
        assert ln["region"]["width"] > 0 and ln["region"]["height"] > 0
        assert ln["region"]["top_left_y"] < 842.0


class TestTheTypes:
    def test_a_line_whose_kind_is_not_established_stays_text(self):
        """The rule the whole emitter rests on. A guess here does not produce
        a slightly worse box — it produces a Table, or a Section, that the
        document does not contain, and every projection carries it."""
        out = linesjson.emit(doc([words("Our contributions are threefold")]))
        assert out["pages"][0]["lines"][0]["type"] == "text"

    def test_a_diagram_is_a_region_with_no_text(self):
        """Vector art has no glyphs. MathPix carries such a thing as a typed
        line with empty text, and `diagram.py` / `picture.py` look for exactly
        that — so a rectangle we measured has somewhere to go."""
        p = page([words("caption text here")])
        p.diagrams = [(100.0, 300.0, 400.0, 600.0)]
        lines = linesjson.emit([p])["pages"][0]["lines"]
        d = next(l for l in lines if l["type"] == "diagram")
        assert d["text"] == "" and d["region"]["height"] == 300.0

    def test_the_source_stamp_is_the_routing_key(self):
        """pdfdrill routes on this name: absent from
        `commands._MERGEABLE_LINES_SOURCES`, the merged route switches off
        silently — which is how `visionocr` cost 2609.24972 every Section it
        had (782)."""
        assert linesjson.SOURCE == "pdf2mmd"
        assert linesjson.emit(doc([]))["source"] == "pdf2mmd"


class TestTheIds:
    def test_a_line_id_is_stable_across_runs(self):
        """MathPix ships a uuid per line and several pdfdrill readers key on
        it. An id that changed between runs of the same document would make
        every cached reference stale without anything reporting it."""
        p = lambda: doc([words("Our contributions are threefold")])
        a = linesjson.emit(p())["pages"][0]["lines"][0]["id"]
        b = linesjson.emit(p())["pages"][0]["lines"][0]["id"]
        assert a == b and len(a) == 32
