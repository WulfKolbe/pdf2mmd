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


def content(recs):
    """The emitted records that are LINES, not containers. `to_lines_json`
    now puts the column containers first, so a test that means "the first
    line" has to say so."""
    return [r for r in recs if r["type"] != "column"]


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
        assert content(out["pages"][0]["lines"])[0]["type"] == "math"

    def test_every_line_still_carries_a_rectangle(self):
        """The geometry is the half that was always right. A classifier that
        costs it is a regression however good its types are."""
        p = page([line("We propose a method that learns a metric"),
                  line("x plus y", y=680.0, kind="formula")])
        lines = content(docmodel.to_lines_json([p])["pages"][0]["lines"])
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
        out = content(docmodel.to_lines_json([p])["pages"][0]["lines"])[0]
        assert out["type"] == "formula"          # the node's own verdict
        assert out["region"]["width"] > 0


class TestContainment:
    """The one level of nesting MathPix has and we had none of: 1244 lines
    with a `parent_id` against our 0. It is not decoration — it is the reading
    order of a two-column paper, stated rather than left for each consumer to
    rediscover from x-coordinates."""

    def _two_columns(self):
        # Every threshold in `columns()` is corpus-measured and this fixture
        # has to clear all of them: FIVE lines before a cluster of line-starts
        # is a column at all (a stray equation number at the right margin is
        # three), and then EIGHT before it is a real share of the page
        # (wzlxjtu-009 put 4 lines in its supposed second column against 29 in
        # the first, and reading that page as two-column cost 3 equations).
        # …and each column must be at least a fifth of the page WIDE, because
        # a run of equation numbers at the right margin clusters like a column
        # and is 15pt across. So the fixture needs real line lengths, not
        # labels.
        body = "running text of a real column that is wide enough to be one"
        left = [line(f"{body} left {i}", x0=54.0, y=700.0 - 12 * i)
                for i in range(10)]
        right = [line(f"{body} right {i}", x0=331.0, y=700.0 - 12 * i)
                 for i in range(10)]
        return page(left + right)

    def test_a_column_container_is_emitted_with_its_children(self):
        p = self._two_columns()
        recs = docmodel.to_lines_json([p])["pages"][0]["lines"]
        cols = [r for r in recs if r["type"] == "column"]
        assert len(cols) == 2, [r["type"] for r in recs]
        assert sum(len(c["children_ids"]) for c in cols) == 20

    def test_every_content_line_names_a_parent_that_exists(self):
        """A dangling parent is worse than a flat list: a reader building a
        tree in one pass silently drops the child."""
        p = self._two_columns()
        recs = docmodel.to_lines_json([p])["pages"][0]["lines"]
        ids = {r["id"] for r in recs}
        content = [r for r in recs if r["type"] != "column"]
        assert content and all(r.get("parent_id") in ids for r in content)

    def test_the_container_carries_a_real_rectangle(self):
        """A container is a MEASUREMENT, not a label — the union of the lines
        it holds, so a reader can draw it and check it against the page."""
        p = self._two_columns()
        recs = docmodel.to_lines_json([p])["pages"][0]["lines"]
        for c in (r for r in recs if r["type"] == "column"):
            assert c["region"]["width"] > 0 and c["region"]["height"] > 0
            assert c["text"] == "" and c["conversion_output"] is False

    def test_the_parent_comes_before_its_children(self):
        """MathPix orders them this way and a one-pass tree builder needs it:
        meet the parent before the children it names."""
        p = self._two_columns()
        recs = docmodel.to_lines_json([p])["pages"][0]["lines"]
        first_content = next(i for i, r in enumerate(recs) if r["type"] != "column")
        assert all(r["type"] == "column" for r in recs[:first_content])

    def test_a_page_with_no_lines_gets_no_container(self):
        assert docmodel.to_lines_json([page([])])["pages"][0]["lines"] == []


class TestRunningHeaders:
    """`Signal & Image Processing … Vol.2, No.2, June 2011` stands at the top of
    all 16 pages of 1107.2723 and arrived as 16 Paragraphs, because nothing
    typed it and `text` becomes prose."""

    def _doc(self, n=6, header="Journal of Measured Things Volume 2 Number 2"):
        pages = []
        for k in range(1, n + 1):
            body = [line(f"body line {i} of page {k}", y=600.0 - 12 * i)
                    for i in range(10)]
            hdr = line(header, y=820.0)
            folio = line(str(100 + k), x0=300.0, y=30.0)
            p = page(body + [hdr, folio])
            p.page = k
            for ln in p.lines:
                ln.page = k
            pages.append(p)
        return pages

    def test_a_repeating_header_is_page_info(self):
        t = mmd.classify_lines(self._doc())
        hdr = [v for (pg, i), v in t.items() if pg == 1 and v[0] == "page_info"]
        assert hdr, "the header repeated on every page is still prose"

    def test_a_bare_folio_is_page_info_even_though_it_never_repeats(self):
        """The repeat test cannot see a page number — it differs on every page.
        A line in the margin band that is nothing but digits is one anyway."""
        pages = self._doc()
        t = mmd.classify_lines(pages)
        folio = t[(3, len(pages[2].lines) - 1)]
        assert folio[0] == "page_info", folio

    def test_body_text_in_the_middle_of_the_page_is_never_page_info(self):
        t = mmd.classify_lines(self._doc())
        assert t[(1, 0)][0] != "page_info"

    def test_two_pages_abstain(self):
        """A line on both pages of a two-page document is as likely to be a
        coincidence as a header, and being wrong deletes a paragraph."""
        assert all(v[0] != "page_info"
                   for v in mmd.classify_lines(self._doc(n=2)).values())


class TestTitle:
    """796 typed a title set on three lines as THREE `Section`s. A title on
    three lines is one title."""

    def _front(self):
        big = [line("TOPOGRAPHIC FEATURE EXTRACTION", y=800.0, size=18.0),
               line("FOR", y=778.0, size=18.0),
               line("BENGALI AND HINDI CHARACTER IMAGES", y=756.0, size=18.0)]
        rest = [line(f"body line {i} set in the document text size", y=700.0 - 12 * i)
                for i in range(10)]
        return [page(big + rest)]

    def test_the_run_of_largest_lines_is_the_title(self):
        t = mmd.classify_lines(self._front())
        assert [t[(1, i)][0] for i in range(3)] == ["title"] * 3

    def test_the_run_stops_at_the_first_smaller_line(self):
        """Consecutive is what makes it ONE title rather than every large line
        on the page."""
        t = mmd.classify_lines(self._front())
        assert t[(1, 3)][0] != "title"

    def test_a_page_with_no_size_contrast_has_no_title(self):
        """Calling the first lines of a uniformly-set page a title deletes them
        from the prose."""
        flat = [page([line(f"body line {i} all one size", y=700.0 - 12 * i)
                      for i in range(10)])]
        assert all(v[0] != "title" for v in mmd.classify_lines(flat).values())

    def test_a_whitespace_line_neither_starts_nor_ends_the_title(self):
        """Two of them bracketed 1107.2723's title and arrived as `title` lines
        with a body of " "."""
        pages = self._front()
        pages[0].lines.insert(0, line("   ", y=810.0, size=18.0))
        t = mmd.classify_lines(pages)
        assert t[(1, 0)][0] != "title"
        assert [t[(1, i)][0] for i in (1, 2, 3)] == ["title"] * 3
