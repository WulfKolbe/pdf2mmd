r"""The line CLASSIFIER behind the lines.json — 784/796.

pdfdrill's docmodel is built by twenty modules that turn TYPED lines into
`Table`, `Section`, `CodeListing`, `Formula` objects, and they key off SIXTEEN
types. `LineNode.type` has two — "text" and "formula" — so a stream carrying
only those can produce paragraphs and nothing else. Measured on 1107.2723:

    before   554 lines   text 531, formula 23
    after    554 lines   text 515, math 23, section_header 15, equation 1

and the geometry is untouched: 554 of 554 lines keep their rectangle, which is
the half that was already right and must stay right.
"""
import collections

import docmodel_six as docmodel
import project_mmd as mmd
from docmodel_six import GlyphNode, LineNode, PageNode
from texmap import project


def g(ch, x, y=700.0, size=10.0, font="TEST+NimbusRomNo9L-Regu"):
    return GlyphNode(
        id=f"g{ch}{x:.0f}", page=1, rect=(x, y, x + 0.5 * size, y + size),
        text=ch, cid=ord(ch), glyphname=None, fontname=font,
        family="text", size=size, tex=project("text", None),
        matrix=(size, 0, 0, size, x, y))


def line(text, *, x0=72.0, y=700.0, size=10.0, kind="text"):
    gs, x = [], x0
    for i, w in enumerate(text.split()):
        if i:
            x += 0.5 * size
        for ch in w:
            gs.append(g(ch, x, y, size))
            x += 0.5 * size
    return LineNode(id=f"l{y:.0f}", page=1, rect=(x0, y, x, y + size),
                    type=kind, glyphs=gs)


def page(lines, w=595.0, h=842.0):
    return PageNode(page=1, rect=(0.0, 0.0, w, h), lines=lines)


class TestTheFallback:
    def test_a_formula_line_keeps_its_verdict(self):
        """THE REGRESSION THIS EXISTS FOR. The glyph-family count asks whether
        60% of a line is set in a maths font, which a sentence with one inline
        expression in it is not. Dropping to a literal "text" when that test
        fails lost all 23 formula lines of 1107.2723 — a classifier that knew
        less than the node it was classifying. `formula` is spelled `math`
        because that is the word pdfdrill's modules read."""
        p = page([line("x plus y", kind="formula")])
        assert mmd.classify_lines([p])[(1, 0)][0] == "math"

    def test_ordinary_prose_stays_text(self):
        p = page([line("We propose a method that learns a metric")])
        assert mmd.classify_lines([p])[(1, 0)][0] == "text"

    def test_a_line_with_no_glyphs_is_text_not_a_crash(self):
        p = page([LineNode(id="x", page=1, rect=(0, 0, 1, 1), type="text")])
        assert mmd.classify_lines([p])[(1, 0)] == ("text", {})


class TestTheVocabulary:
    def test_every_type_emitted_is_one_pdfdrill_reads(self):
        """A type nothing measures would produce a Section the document does
        not contain, and every projection would carry it."""
        p = page([line("We propose a method that learns a metric"),
                  line("x plus y", y=680.0, kind="formula")])
        for (t, _extra) in mmd.classify_lines([p]).values():
            assert t in mmd.LINE_TYPES, t


class TestTheEmitter:
    def test_the_type_reaches_the_lines_json(self):
        p = page([line("x plus y", kind="formula")])
        out = docmodel.to_lines_json([p])
        assert out["pages"][0]["lines"][0]["type"] == "math"

    def test_every_line_still_carries_a_rectangle(self):
        """The geometry is the half that was always right. A classifier that
        costs it is a regression however good its types are."""
        p = page([line("We propose a method that learns a metric"),
                  line("x plus y", y=680.0, kind="formula")])
        lines = docmodel.to_lines_json([p])["pages"][0]["lines"]
        assert len(lines) == 2
        for ln in lines:
            r = ln["region"]
            assert r["width"] > 0 and r["height"] > 0

    def test_a_classifier_failure_costs_the_types_not_the_geometry(self, monkeypatch):
        """`to_lines_json` falls back to the node's own type when the
        classifier raises, because a lines.json with weak types is a working
        document and a lines.json with no rectangles is not."""
        monkeypatch.setattr(mmd, "classify_lines",
                            lambda pages: (_ for _ in ()).throw(RuntimeError("boom")))
        p = page([line("x plus y", kind="formula")])
        out = docmodel.to_lines_json([p])["pages"][0]["lines"][0]
        assert out["type"] == "formula"          # the node's own verdict
        assert out["region"]["width"] > 0
