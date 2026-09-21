#!/usr/bin/env python3
r"""test_table — the table pipeline, fed deliberately malformed elements.

    python3 -m pytest test_table.py -q

The table is a MEASUREMENT instrument: if it drops an equation quietly, every
number taken from it is wrong and nothing says so. These tests assert the two
properties that matter --

    every gold equation gets a row
    every block a source emitted is either matched or listed

-- and that malformed input is REFUSED VISIBLY rather than swallowed.

Needs $PDFDRILL_SRC for `report_tex` (the preamble and the compile gate) and
xelatex for the probe tests, which skip without it.
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("eq", _here / "eqtable_716.py")
eq = importlib.util.module_from_spec(_s)
_s.loader.exec_module(eq)


#: Things a reader really does emit, each of which has broken something.
MALFORMED = [
    r"\frac{a}{",                      # unbalanced brace
    r"\left( a + b",                   # \left with no \right
    r"a \right)",                      # \right with no \left
    r"\bogusmacro{x}",                 # undefined command
    r"x \text{50% done}",              # a comment character
    r"m_{ij} = \text{#}\{k\}",         # a parameter character
    r"\text{gb_imp}",                  # a subscript in text mode
    r"\begin{aligned}&\log x\end{aligned}",   # leading & under mathtools
    r"\begin{aligned}a\\ \end{aligned}",      # trailing row separator
    r"",                               # empty
    r"   ",                            # whitespace only
    r"\bigl( \bigr)",                  # a pair with nothing in it
    "x" * 4000,                        # very long
    r"\alpha \uE000 \beta",            # a private-use codepoint
]


def test_key_never_raises_and_never_returns_none():
    for s in MALFORMED:
        k = eq.key(s)
        assert isinstance(k, str)


def test_md_blocks_survives_a_malformed_document(tmp_path):
    md = tmp_path / "page.md"
    md.write_text("\n\n".join(["intro"] + ["$$\n%s\n$$" % s for s in MALFORMED]),
                  encoding="utf-8")
    blocks = eq.md_blocks(md)
    # the empty and whitespace-only ones are dropped; everything else survives
    assert len(blocks) == len([s for s in MALFORMED if s.strip()])


def test_gold_equations_survives_a_malformed_source(tmp_path):
    gt = tmp_path / "1_gt.tex"
    gt.write_text("\\begin{document}\n"
                  + "\n".join(r"\begin{equation}%s\end{equation}" % s
                              for s in MALFORMED)
                  + "\n\\end{document}\n", encoding="utf-8")
    gold = eq.gold_equations(gt)
    assert len(gold) == len([s for s in MALFORMED if s.strip()])


def test_every_gold_equation_is_matched_or_left_unmatched():
    """`align` must account for every gold equation and every block.

    Not "most of them": a gold equation with no match gets None, and a block
    claimed by nobody appears in the leftovers. Anything else is an equation
    that has quietly left the table.
    """
    gold = [("equation", s) for s in MALFORMED if s.strip()]
    blocks = [s for s in MALFORMED if s.strip()] + [r"\zeta = 1"]
    at, left = eq.align(gold, blocks)
    assert len(at) == len(gold)                       # a verdict for each
    claimed = {h[0] for h in at if h is not None}
    assert claimed.isdisjoint(left)                   # no block counted twice
    assert set(left) | claimed <= set(range(len(blocks)))


def test_a_cell_is_never_blank():
    """A blank cell cannot be told from "did not render", which is the
    confusion this table exists to prevent."""
    for s in MALFORMED:
        c = eq.cell(s)
        assert c.strip(), repr(s)
        if s.strip():
            assert ("FitMath" in c) or ("will not typeset" in c), repr(s)


def test_cell_text_of_a_row_out_of_range_falls_back_to_the_block():
    blocks = [r"\begin{aligned}a\\ b\end{aligned}"]
    assert eq.cell_text(blocks, (0, 99)) == blocks[0]
    assert eq.cell_text(blocks, None) == ""


def test_a_percent_sign_cannot_reach_the_table_unescaped():
    """`%` starts a comment: written plain it eats the rest of the row.
    It cost 29 errors and 21 demoted rows once."""
    c = eq.cell(r"x \text{50\% done}")
    body = c.split("%")
    # every `%` in the emitted cell is preceded by a backslash
    assert all(p.endswith("\\") for p in body[:-1]), c


@pytest.mark.skipif(shutil.which("xelatex") is None, reason="needs xelatex")
def test_the_probe_convicts_the_guilty_and_clears_the_innocent():
    pre = ("\\documentclass{article}\n\\usepackage{amsmath}\n"
           "\\begin{document}")
    good = [r"a=b", r"\frac{1}{2}", r"\text{\#}", r"\alpha+\beta"]
    bad = [r"\bogusmacro{x}"]
    work = Path(tempfile.mkdtemp())
    found = eq.unrenderable_cells(good + bad, pre, work)
    assert found == set(bad), found


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_an_unrenderable_cell_does_not_end_the_row():
    r"""757 — inside a longtable cell `\\` ENDS THE ROW. The unrenderable
    form used it, and looked safe only because it had never appeared in
    anything but the last column. In wzlxjtu-041's unmatched table it landed
    in the third, and `(will not typeset)` came out under the No column with
    every following cell shifted one to the left."""
    # `\bogusmacro{x}` passes the STATIC gate -- only the compile probe
    # catches it. An unbalanced brace is what `display_safe` refuses.
    for bad in (r"\frac{a}{", r"\left( a + b", r"x \text{50% done}"):
        c = eq.cell(bad)
        assert "will not typeset" in c, bad
        assert "\\\\" not in c, c
