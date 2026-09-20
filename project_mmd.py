"""project_mmd — project the docmodel to Mathpix-compatible Markdown / LaTeX.

Three things this adds over `docmodel.to_markdown`:

1. CROP LINKS in Mathpix's exact CDN syntax, so a region we cannot turn into
   LaTeX is still *visible* in any Markdown editor instead of being an HTML
   comment nobody renders:

       ![](https://cdn.mathpix.com/cropped/<id>g-<page>.jpg?height=H&width=W&top_left_y=Y&top_left_x=X)

   Verified against a real Mathpix `tex.zip`: its bundled crops are named
   `<uuid>-<page:03d>_<height>_<width>_<top_left_y>_<top_left_x>.jpg`, and page
   25's diagram file `025_885_748_204_592` matches that page's lines.json
   region h=885 w=748 y=204 x=592. Query-param order is the same.

   LaTeX uses the same URL inside \\includegraphics, as Mathpix does.

2. PAGE SEPARATORS, because Markdown and pdftotext both emit them when asked.

3. FONT / HEADING documentation. Headings are not tagged in a PDF; they are
   inferred from type size and weight relative to the page's body text, and the
   evidence is emitted so the inference can be audited rather than trusted.

COORDINATES. The docmodel works in PDF points with y increasing UPWARD. Mathpix
regions are page-image pixels with y increasing DOWNWARD from the top-left. The
conversion is done once, here, in `crop_url`.
"""
from __future__ import annotations

import os
import collections
import re
from dataclasses import dataclass

import docmodel_six as docmodel
import texmap
from docmodel_six import GlyphNode, LineNode, PageNode

# Shared with `docmodel.to_lines_json`, deliberately: a crop URL and the
# lines.json written by the same run must be in ONE coordinate space, or a
# server handed both rescales every rectangle off the page.
DEFAULT_PX_PER_PT = docmodel.PX_PER_PT


def crop_url(rect, page: PageNode, doc_id: str, base: str,
             px_per_pt: float = DEFAULT_PX_PER_PT) -> str:
    """A Mathpix-syntax crop URL for a rect given in PDF points (y up)."""
    x0, y0, x1, y1 = rect
    page_top = page.rect[3]
    left = int(round(x0 * px_per_pt))
    top = int(round((page_top - y1) * px_per_pt))
    width = max(1, int(round((x1 - x0) * px_per_pt)))
    height = max(1, int(round((y1 - y0) * px_per_pt)))
    return (f"{base.rstrip('/')}/cropped/{doc_id}g-{page.page}.jpg"
            f"?height={height}&width={width}"
            f"&top_left_y={top}&top_left_x={left}")


# ------------------------------------------------------------------ headings
@dataclass
class FontProfile:
    """The type sizes a document actually uses, measured not assumed."""
    body_size: float
    body_font: str
    sizes: collections.Counter
    fonts: collections.Counter

    # Above this ratio, size alone settles it: nothing that much larger than
    # body text is ordinary prose.
    CLEARLY_LARGER = 1.35

    def rank(self, size: float) -> int:
        """0 = body or smaller; 1,2,3 = progressively larger heading levels."""
        if size <= self.body_size * 1.08:
            return 0
        if size <= self.body_size * 1.25:
            return 1
        if size <= self.body_size * 1.6:
            return 2
        return 3

    def clearly_larger(self, size: float) -> bool:
        return size > self.body_size * self.CLEARLY_LARGER


def profile(pages: list[PageNode]) -> FontProfile:
    sizes: collections.Counter = collections.Counter()
    fonts: collections.Counter = collections.Counter()
    for p in pages:
        for ln in p.lines:
            for g in ln.glyphs:
                if g.is_math:
                    continue
                sizes[round(g.size, 1)] += 1
                fonts[g.fontname.split("+")[-1]] += 1
    if not sizes:
        return FontProfile(0.0, "", sizes, fonts)
    # Body text is the size MOST glyphs are set in, not the smallest: footnotes
    # and captions are smaller, and a document may have more of them than one
    # might guess.
    body_size = sizes.most_common(1)[0][0]
    body_font = fonts.most_common(1)[0][0] if fonts else ""
    return FontProfile(body_size, body_font, sizes, fonts)


# TeX font names do not contain the word "bold". Computer Modern's bold is
# CMBX ("bold extended"), so matching only on "bold" loses every heading in
# every Computer Modern document -- measured: 4 headings found where MathPix
# found 17, because `CMBX12` did not look bold.
_BOLD_NAME = re.compile(
    r"bold|"                       # Times-Bold, Arial,Bold, ...
    r"\bcm(bx|b|ssbx|bxti|bxsl)\d*|"  # Computer Modern bold family
    r"\bcmssdc\d*|"                 # CM sans demi condensed
    r"-(bd|bold|semibold|black|heavy)\b|"
    r"(^|[^a-z])(bd|blk)\d*$",
    re.I,
)


def _is_bold(fontname: str) -> bool:
    return bool(_BOLD_NAME.search(fontname.split("+")[-1]))


def _line_is_bold(lines) -> bool:
    """Is this heading candidate set in a bold TEXT face?

    Judged on the text glyphs only. A heading containing mathematics carries
    the maths font too -- `Exceptional Lie group $G_2$` is CMBX12 plus CMMI12
    -- and requiring every font on the line to be bold rejects exactly the
    headings that have maths in them.
    """
    fonts = {g.fontname.split("+")[-1]
             for l in lines for g in l.glyphs if not g.is_math}
    return bool(fonts) and all(_is_bold(f) for f in fonts)


def line_size(ln: LineNode) -> float:
    vis = [g for g in ln.glyphs if not g.is_math]
    return max((g.size for g in vis), default=0.0)


def heading_level(ln: LineNode, fp: FontProfile) -> int:
    """Markdown heading level for a line, or 0 for body text.

    A heading is larger than body text, or bold and short. Length matters
    because a bold run inside a paragraph is emphasis, not a heading.
    """
    # A heading may contain mathematics -- `The Groups Pin(n) and Spin(n)` is a
    # section title. Requiring type == "text" silently demoted every such
    # heading to body text.
    if not ln.glyphs or ln.rotated:
        return 0
    size = line_size(ln)
    if size <= 0:
        return 0
    rank = fp.rank(size)
    text = docmodel._run_text(ln.glyphs).strip()
    bold = _line_is_bold([ln])

    # A heading is a PHRASE. One or two characters in a bold face is a chart
    # label, a page number or an axis tick -- measured: a bold `m` and `n`
    # from inside a bar chart became `### m` and `### n`, and five single
    # digits elsewhere did the same.
    if len(text.strip()) < 3 or not any(c.isalpha() for c in text):
        return 0

    if rank == 0:
        if bold and 0 < len(text) <= 80:
            return 3
        return 0
    if len(text) > 120:
        return 0          # a large-type paragraph is not a heading

    # Between body size and CLEARLY_LARGER, size is not enough on its own.
    # A LaTeX title page sets the author block a step above body size in plain
    # roman -- `Jean Gallier`, `University of Pennsylvania`, the postal address
    # -- and calling those headings turns an address into six section titles.
    # A real section heading at that ratio is bold (measured: every heading in
    # a Springer maths book, 12pt bold against 10pt body). Above the ratio,
    # boldness stops mattering: a 20.7pt roman article title is still a title.
    if not bold and not fp.clearly_larger(size):
        return 0
    return {1: 3, 2: 2, 3: 1}[rank]


# --------------------------------------------------------------- projections
def _merge_heading_runs(pages: list[PageNode], fp: "FontProfile"):
    """Group consecutive heading lines of the same level into one heading.

    A heading that wraps is two LineNodes and one heading: `10.2.1
    "Prolongation" of Anti-de Sitter to Black Hole` / `Solutions` must not
    become two `###`. Lines are joined only when they share a level AND sit
    within 1.5 line-heights of each other, so two unrelated headings separated
    by body text stay apart.

    HEURISTIC, and known to merge a chapter number with its title
    (`Chapter 10` + `Three-Dimensional Gravity`). That reads correctly here but
    has not been checked on other documents.
    """
    out = []
    for p in pages:
        groups: list[tuple[int, list]] = []
        for ln in p.lines:
            lvl = heading_level(ln, fp)
            if groups and groups[-1][0] == lvl and lvl:
                prev = groups[-1][1][-1]
                gap = prev.rect[1] - ln.rect[3]
                if 0 <= gap <= 1.5 * max(line_size(ln), 1.0):
                    groups[-1][1].append(ln)
                    continue
            groups.append((lvl, [ln]))
        out.append((p, groups))
    return out


def _escape_text(t: str) -> str:
    """Escape characters that would be read as Markdown or maths syntax.

    A literal `$` in prose opens a maths span and silently swallows text up to
    the next one. Measured in this corpus: a stray `$` produced an unbalanced
    `$$` that made a renderer treat 681 characters of prose as an equation.
    """
    return t.replace("\\", "\\textbackslash{}").replace("$", r"\$")


# Commands that REQUIRE a following argument. Emitting one without its brace
# makes a renderer fail outright -- KaTeX answers "Missing argument for
# \widetilde" and shows an error box where the mathematics should be. A crop
# is a worse reader experience than correct LaTeX but a far better one than a
# broken renderer, so anything that fails this check is deferred instead.
_NEEDS_ARG = re.compile(
    r"\\(widetilde|widehat|overline|underline|bar|hat|vec|tilde|check|dot|"
    r"ddot|mathbb|mathbf|mathrm|mathcal|mathfrak|mathscr|sqrt|frac|text)"
    r"\s*(?!\{)")


def is_emittable(tex: str | None) -> bool:
    """Is this LaTeX structurally safe to put in the document?"""
    if not tex or not tex.strip():
        return False
    # Depth, not counts: `a}b{` balances by count and is still malformed.
    # Escaped braces are literal characters, not grouping: `\{ x \in S \}`
    # is a set, and counting its braces as grouping rejected every set in the
    # document.
    depth = 0
    i = 0
    while i < len(tex):
        ch = tex[i]
        if ch == "\\" and i + 1 < len(tex):
            i += 2                      # skip the escaped character
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    if depth != 0:
        return False
    if _NEEDS_ARG.search(tex + " "):
        return False
    return True


# Content that carries no mathematics: delimiters, spacing, punctuation and
# grouping, with nothing between them.
_NO_CONTENT = re.compile(
    r"^(?:\\(?:left|right|bigg?[lrm]?|Bigg?[lrm]?|mid|quad|qquad|,|;|:|!|\s)"
    r"|[\s(){}\[\].,;:|]|\\[{}|])*$")


_LEFT_RIGHT = re.compile(r"\\(left|right)(?![A-Za-z])")


def balance_delims(tex: str) -> str:
    r"""Close any `\left` that has no `\right`, and vice versa.

    An unmatched `\left` is a FATAL LaTeX error, not a cosmetic one: the
    whole document stops. wzlxjtu-011 emitted

        \begin{aligned}
        \left[ \tanh\!\Bigl( \\
        \sigma_{0} = \cosh \\
        \frac{1}{4} \log [
        \end{aligned}

    -- the row opens a bracket whose partner is on another row of the page.
    (`\Bigl(` beside it is fine: the `\big` family only sizes a delimiter and
    needs no partner. `\left` is the one that pairs.)

    Since 732 a fused run is emitted as `aligned`, and `\left`/`\right` may
    not span a `\\`, so every ROW has to balance by itself -- which is what
    this is applied to.

    `\left.` and `\right.` are LaTeX's own null delimiters: they pair without
    drawing anything. Repairing this way keeps the row and says nothing that
    is not on the page, where dropping it would cost the whole display and
    guessing the missing bracket would invent one.
    """
    opened = unmatched_right = 0
    for m in _LEFT_RIGHT.finditer(tex):
        if m.group(1) == "left":
            opened += 1
        elif opened:
            opened -= 1
        else:
            unmatched_right += 1
    if unmatched_right:
        tex = "\\left. " * unmatched_right + tex
    if opened:
        tex = tex + " \\right." * opened
    return tex


def has_content(tex: str | None) -> bool:
    r"""Does this LaTeX say anything, or is it only punctuation?

    Measured on the four-column comparison: of 836 display equations pdf2mmd
    emitted across 102 documents, 202 carried three tokens or fewer and 55
    were EMPTY. The rest of that tail was `igl(`, `igr)`, `iggl[` --
    a delimiter alone, on its own line, wrapped in `$$`.

    Those are not deferrals waiting to be counted. They are wrong answers
    shipped at full confidence, and the crop metric could never see them
    because a crop is a REFUSAL and these are emissions.
    """
    if not tex or not tex.strip():
        return False
    return not _NO_CONTENT.match(tex.strip())


_EQ_TAG = re.compile(r"^\(\s*[0-9]+(?:\.[0-9]+)*\s*[a-zA-Z]?\s*\)$")


def equation_number(line, right: float) -> list:
    r"""The glyphs of a right-margin equation NUMBER, or [].

    An author's `(1)` at the right margin is a LABEL, not part of the
    expression. Absorbed into the maths it produced `L^{-1}dL \in g(1)`,
    `dL = Q + P(2)` and `= K_{0\ell i}(8)` -- readings that are wrong in a
    way no renderer can catch, because they compile.

    Three conditions together, measured on a real line: the run is a
    parenthesised number, it ENDS at the right margin (525..540 against a
    margin of 540.0), and a wide gap separates it from the mathematics
    (202pt on that line -- the trailing space of a display equation, not an
    inter-symbol gap).
    """
    gs = sorted((g for g in line.glyphs if g.text.strip()),
                key=lambda g: g.rect[0])
    if len(gs) < 3:
        return []
    size = max(g.size for g in gs)
    for i in range(len(gs) - 1, 0, -1):
        run = gs[i:]
        if not _EQ_TAG.match("".join(g.text for g in run).strip()):
            continue
        if run[-1].rect[2] < right - 2.0 * size:
            return []                        # not at the margin
        if run[0].rect[0] - gs[i - 1].rect[2] < 3.0 * size:
            return []                        # not separated from the maths
        return run
    return []


def _inline(tex: str | None) -> str:
    """An inline maths span, or nothing at all.

    Empty content must never be wrapped: `$` + `` + `$` is `$$`, which pairs
    with a display delimiter elsewhere and corrupts everything between.
    """
    # NOT gated on has_content, though `$\bigl($` is the same noise as
    # `$$\bigl($$`. Tried and reverted: a refused inline span becomes a
    # CROP, so gating it moved 306 crops onto the tally and the block count
    # did not change at all -- the fusion chain broke on the crop exactly as
    # it had on the inline. The right treatment is to drop the span
    # silently, which is a change to the caller, not here.
    if not is_emittable(tex):
        return ""
    # An inline span carries the same hazard as a display row and for the
    # same reason: it is a FRAGMENT of a line, so a `\left` whose partner sits
    # in the next span reaches the document alone. wzlxjtu-011 emitted a bare
    # `$\left[$`, which is a fatal error wherever it lands.
    return f"${balance_delims(tex.strip())}$"


def _left_margin(page: PageNode) -> float:
    """Where the body text block starts on the left.

    The most common left edge among lines, not the smallest: a display
    equation is indented, and taking the minimum would make the margin the
    equation's own left edge and hide every one of them.
    """
    edges = collections.Counter(
        round(ln.rect[0]) for ln in page.lines if not ln.rotated and ln.glyphs)
    return float(edges.most_common(1)[0][0]) if edges else page.rect[0]


def is_display(ln: LineNode, left: float) -> bool:
    """True if this line is a displayed equation on its own.

    Two conditions, both needed. It must be INDENTED past the body margin,
    which is what centring a display equation does; and it must carry no
    prose, since a sentence with inline maths is not a display. An equation
    number is tolerated -- `(1.4)` sits at the right margin and is not prose.
    """
    if ln.rotated or not ln.glyphs:
        return False
    size = max(g.size for g in ln.glyphs)
    if ln.rect[0] <= left + 1.2 * size:
        return False
    for sp in ln.spans:
        if sp.kind != "text":
            continue
        word = docmodel._run_text(sp.glyphs).strip()
        if re.fullmatch(r"[(\[]?[\d.]+[)\]]?", word):
            continue                      # an equation number
        if len(re.findall(r"[A-Za-z]", word)) > 3:
            return False                  # prose: not a display
    return any(sp.kind == "math" for sp in ln.spans)


def _right_margin(page: PageNode) -> float:
    """Where the body text block ends on the right.

    Taken as the largest right edge among full-width text lines, which is the
    measure a line has to fall short of to have ended its paragraph.
    """
    edges = [ln.rect[2] for ln in page.lines
             if not ln.rotated and ln.glyphs]
    return max(edges) if edges else page.rect[2]





def _link_at(page: PageNode, rect) -> "docmodel.LinkNode | None":
    """The link annotation covering this span, if any."""
    cx = 0.5 * (rect[0] + rect[2])
    cy = 0.5 * (rect[1] + rect[3])
    for ln in page.links:
        if ln.rect[0] <= cx <= ln.rect[2] and ln.rect[1] <= cy <= ln.rect[3]:
            return ln
    return None



def _link_target(link) -> str | None:
    if link is None:
        return None
    if link.uri:
        return link.uri
    if link.dest:
        return "#" + link.dest
    return None


def _linked_text(span, page: PageNode, word_gap: float) -> str:
    """The span's text, with the part a link COVERS wrapped.

    Only the glyphs inside the annotation rectangle are wrapped. Testing the
    span's centre instead swept up whatever shared the run: a footnote marker
    became part of its URL, `[1http://tiramisu-compiler.org/]`, and a trailing
    comma landed inside the citation, `[[39],]`.

    A citation carries the author's own BibTeX key, so `[22]` becomes
    `[[22]](#cite.polly)` -- the key survives into the Markdown instead of
    being discarded with the annotation.
    """
    glyphs = sorted(span.glyphs, key=lambda g: g.rect[0])
    if not glyphs:
        return ""

    def covering(g):
        if not page.links:
            return None
        cx = 0.5 * (g.rect[0] + g.rect[2])
        cy = 0.5 * (g.rect[1] + g.rect[3])
        for ln in page.links:
            if (ln.rect[0] <= cx <= ln.rect[2]
                    and ln.rect[1] <= cy <= ln.rect[3]):
                return ln
        return None

    # A RAISED, script-size glyph with no base is a marker -- a footnote
    # reference, not an exponent. Measured: size 5.98 against a 7.97 line and
    # a baseline 2.81pt higher, with a NORMAL advance (2.989) and a 0.50pt gap
    # to the next glyph. There is no missing space to restore: the PDF has
    # none, and the right rendering is a superscript.
    size = span.line_size or max(g.size for g in glyphs)
    bases = [g.baseline for g in glyphs]
    line_base = max(set(bases), key=bases.count)

    def raised(g) -> bool:
        return (g.size <= 0.85 * size
                and g.baseline >= line_base + 0.15 * size)

    # A marker is ISOLATED. Several raised runs alternating with ordinary
    # glyphs is not a row of footnote references -- it is two text blocks at
    # different baselines that were merged into one row, and wrapping each
    # fragment produces `L<sup>H</sup>e<sup>i</sup>v<sup>g</sup>`. Marking
    # none of them keeps the damage visible as plain text instead of dressing
    # it up.
    runs_up = 0
    prev_up = False
    for g in glyphs:
        up = raised(g)
        if up and not prev_up:
            runs_up += 1
        prev_up = up
    if runs_up > 2:
        def raised(g) -> bool:      # noqa: F811 - deliberate override
            return False

    out: list[str] = []
    run: list = []
    run_key = None

    def flush():
        if not run:
            return
        text = _escape_text(docmodel._run_text(run, word_gap))
        link, is_up = run_key
        if is_up and text.strip():
            text = f"<sup>{text}</sup>"
        target = _link_target(link)
        out.append(f"[{text}]({target})" if target and text.strip() else text)
        run.clear()

    for g in glyphs:
        key = (covering(g), raised(g))
        if run_key is None or key != run_key:
            flush()
            run_key = key
        run.append(g)
    flush()
    return "".join(out)


def _mostly_inside(rect, boxes, frac: float = 0.7) -> bool:
    """Is this span mostly covered by one of the crop rectangles?

    Centre-in-box was too coarse: a small crop of a stray radical overlapped
    the word `only` enough to contain its centre, and the word vanished from
    the prose. A span is part of a picture only if the picture really covers
    it.
    """
    w = max(rect[2] - rect[0], 0.01)
    h = max(rect[3] - rect[1], 0.01)
    area = w * h
    for b in boxes:
        ox = min(rect[2], b[2]) - max(rect[0], b[0])
        oy = min(rect[3], b[3]) - max(rect[1], b[1])
        if ox > 0 and oy > 0 and (ox * oy) / area >= frac:
            return True
    return False


def _crop_clusters(page: PageNode):
    """Merge deferred rectangles that OVERLAP into one crop each.

    A matrix is one object drawn as several baseline rows inside built-up
    fences. Each row defers separately, and because the tall fence glyphs
    belong to every row's rectangle the crops overlap on the page -- four
    stacked images for one `\\begin{array}`. Overlapping rectangles are one
    region: the first span in reading order carries the merged crop and the
    rest emit nothing.

    Returns {span_id: rect} for the carriers and {span_id: None} for the rest.
    """
    spans = [sp for ln in page.lines for sp in ln.spans
             if sp.kind == "math" and docmodel.span_latex(sp) is None]
    if len(spans) < 2:
        return {sp.id: sp.rect for sp in spans}

    boxes = {sp.id: list(sp.rect) for sp in spans}
    order = sorted(spans, key=lambda s: (-s.rect[3], s.rect[0]))
    parent = {sp.id: sp.id for sp in spans}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    changed = True
    while changed:
        changed = False
        ids = list({find(sp.id) for sp in spans})
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = boxes[ids[i]], boxes[ids[j]]
                if (a[0] < b[2] and b[0] < a[2]
                        and a[1] < b[3] and b[1] < a[3]):
                    ra, rb = find(ids[i]), find(ids[j])
                    parent[rb] = ra
                    boxes[ra] = [min(a[0], b[0]), min(a[1], b[1]),
                                 max(a[2], b[2]), max(a[3], b[3])]
                    changed = True
                    break
            if changed:
                break

    out: dict = {}
    seen: set = set()
    for sp in order:
        root = find(sp.id)
        if root in seen:
            out[sp.id] = None
        else:
            seen.add(root)
            out[sp.id] = tuple(boxes[root])
    return out



# `[^\W\d_]` is "any Unicode letter". Spelling the class out as A-Za-zÀ-ÿ
# missed the LIGATURES a typesetter emits as single glyphs -- `difﬁ-cult`
# breaks after U+FB01, and 31 such words stayed broken.
_LETTER = r"[^\W\d_]"
_HYPHEN_END = re.compile(rf"({_LETTER}{{2,}})[-\u00ad]$")
_WORD_START = re.compile(rf"^({_LETTER}+)([^\w].*)?$", re.S)


def dehyphenate(text: str, vocabulary: set[str] | None = None) -> str:
    """Rejoin words broken across a line by the typesetter's hyphen.

    `sci-\nentific` is one word; the hyphen belongs to the line break, not to
    the word. Measured against pdftotext on one paper: 138 words left broken
    here, 0 there.

    A hyphen at a line end is ambiguous -- `well-\nknown` keeps its hyphen.
    The evidence that settles it is whether the document uses the HYPHENATED
    form elsewhere, mid-line, where no line break forced it: `top-left` and
    `web-based` do, `compara-ble` and `iden-tify` do not.

    Requiring evidence for the JOINED form instead was the wrong way round
    and left 45 real breaks standing -- in a six-page paper a word often
    appears exactly once, in the sentence where it happened to be broken.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    carry: str | None = None
    while i < len(lines):
        # A join can produce a line that ITSELF ends in a hyphen, because the
        # line it absorbed ended in one. Appending straight away left those
        # unexamined, so a second pass over the output found more work.
        cur = carry if carry is not None else lines[i]
        carry = None
        m = _HYPHEN_END.search(cur.rstrip())
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        n = _WORD_START.match(nxt.strip()) if m else None
        # The continuation must start LOWERCASE. Widening the class to any
        # Unicode letter (for ligatures) dropped that, and `end-\nNew` would
        # have joined across a sentence boundary.
        if (m and n and n.group(1)[:1].islower()
                and not cur.lstrip().startswith("```")):
            joined = m.group(1) + n.group(1)
            compound = f"{m.group(1).lower()}-{n.group(1).lower()}"
            if vocabulary is None or compound not in vocabulary:
                head = cur.rstrip()[: m.start()]
                rest = nxt.strip()[len(n.group(1)):]
                carry = head + joined + rest
                i += 1
                continue
        out.append(cur)
        i += 1
    if carry is not None:
        out.append(carry)
    return "\n".join(out)


def _vocabulary(text: str) -> set[str]:
    """Hyphenated compounds the document writes MID-LINE.

    A hyphen the typesetter inserted at a line break never appears mid-line;
    a real compound does. That is the evidence for keeping a hyphen.
    """
    flat = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return {m.group(0).lower() for m in
            re.finditer(rf"{_LETTER}{{2,}}-{_LETTER}{{2,}}", flat)
            if "\n" not in m.group(0)}


def _column_margin(page: PageNode, line, fallback: float) -> float:
    """The right edge that this line's own column justifies to.

    Taken from the lines that START at the same place: a block of running
    text shares a left edge and justifies to a common right edge, whatever
    the rest of the page does.
    """
    key = round(line.rect[0] / 12.0)
    edges = [round(ln.rect[2] / 6.0) * 6.0 for ln in page.lines
             if ln.glyphs and round(ln.rect[0] / 12.0) == key]
    if len(edges) < 3:
        return fallback
    # The MODE, not the maximum: justified text clusters at its margin, and
    # one long line -- a full-width heading sharing the same left edge --
    # would otherwise set the margin for the whole block.
    return max(set(edges), key=edges.count)


def _is_gutter_only(group, fence_size: float) -> bool:
    """Is this row nothing but a listing's line number?

    A blank line inside a listing still gets a number from the layout tool,
    so the row carries the gutter glyph and no code. Measured: the gutter is
    4.98pt roman at x=305 while the code is 7.97pt NimbusMonL -- a different
    size AND a different face.

    Treating such a row as ordinary text closes the code fence and reopens it
    on the next line, chopping one listing into three:

        ```
        1 // Declare the iterators i, j and c.
        2 Var i(0, N-2), j(0, M-2), c(0, 3);
        ```
        3
        ```
        4 Computation bx(i, j, c), by(i, j, c);
        ```

    The number is the layout tool's, not the author's -- which is why Mathpix
    drops it deliberately -- so the blank line is what belongs in the output.
    """
    glyphs = [g for ln in group for g in ln.glyphs]
    if not glyphs or len(glyphs) > 4:
        return False
    if not all(g.text.strip().isdigit() for g in glyphs if g.text.strip()):
        return False
    if any(texmap.is_monospace(g.fontname) for g in glyphs):
        return False
    return max(g.size for g in glyphs) <= 0.8 * max(fence_size, 1.0)


def _all_deferred(lines) -> bool:
    """True if this group is maths and NONE of it could be projected."""
    saw = False
    for ln in lines:
        for sp in ln.spans:
            if sp.kind == "math":
                saw = True
                if docmodel.span_latex(sp) is not None:
                    return False
            else:
                word = docmodel._run_text(sp.glyphs, sp.word_gap).strip()
                if len(re.findall(r"[A-Za-z]", word)) > 3:
                    return False      # prose: not a stranded formula
    return saw


def _fuse_deferred_blocks(groups, left: float):
    """Join consecutive unprojectable display rows into ONE block.

    A matrix is a single object drawn as several baseline rows inside built-up
    fences. Deferring each row separately produced a stack of crops that
    OVERLAP on the page -- four of them for one `\begin{array}` -- because the
    tall fence glyphs belong to every row's rectangle. One crop per matrix is
    both correct and readable.
    """
    out: list = []
    run: list = []

    def flush():
        if not run:
            return
        if len(run) == 1:
            out.append((0, run[0]))
        else:
            merged = [ln for lines in run for ln in lines]
            out.append((0, merged))
        run.clear()

    for lvl, lines in groups:
        if lvl == 0 and _all_deferred(lines) and lines \
                and min(ln.rect[0] for ln in lines) > left + 4.0:
            run.append(lines)
            continue
        flush()
        out.append((lvl, lines))
    flush()
    return out


def _strip_gutter(glyphs, fence_size: float):
    """Drop a listing's line-number glyphs from a code line.

    The number is the layout tool's, not the author's -- `lstlisting`
    generates it -- which is why Mathpix omits it. Dropping it is therefore a
    defensible default for some consumers, but it must be all or nothing: a
    listing showing SOME of its numbers is worse than one showing none.
    """
    kept = [g for g in glyphs
            if texmap.is_monospace(g.fontname)
            or g.size > 0.8 * max(fence_size, 1.0)]
    return kept or glyphs


def to_markdown(pages: list[PageNode], doc_id: str = "pdfdrill",
                base: str = "http://localhost:8000",
                px_per_pt: float = DEFAULT_PX_PER_PT,
                page_separator: str = "\n---\n",
                crop_deferred: bool = True,
                keep_rotated: bool = False,
                line_numbers: bool = True,
                join_hyphens: bool = True) -> str:
    """Mathpix-flavoured Markdown: LaTeX where we have it, a crop where we don't."""
    fp = profile(pages)
    grouped = {p.page: g for p, g in _merge_heading_runs(pages, fp)}
    out: list[str] = []
    for p in pages:
        if page_separator:
            out.append(page_separator)
        if p.invisible:
            out.append(f"*[page {p.page}: OCR text layer; not projected]*")
            continue
        margin = _right_margin(p)
        left = _left_margin(p)
        # The bottom of the display block last emitted, so an adjacent one
        # can join it rather than opening a new `$$`.
        last_display = None
        crops = _crop_clusters(p)
        # The page's usual LEADING. A gap wider than this is the author
        # separating things -- a caption from the paragraph after it, one
        # block from the next -- and Markdown needs a blank line to keep them
        # apart, since it joins adjacent lines by design.
        # Measured from CONSECUTIVE lines in reading order, which is
        # column-major, so both lines of a pair are in the same column.
        # Taking the sorted set of all row tops instead mixed the columns
        # together: on a two-column page the small offset BETWEEN the
        # columns became the modal gap -- 1.7pt against a real leading of
        # 10.9 -- so every line looked separated and every line became its
        # own paragraph.
        gaps = [a.rect[3] - b.rect[3]
                for a, b in zip(p.lines, p.lines[1:])
                if a.glyphs and b.glyphs and 0 < a.rect[3] - b.rect[3] < 60]
        leading = (max(set(round(g, 1) for g in gaps),
                       key=[round(g, 1) for g in gaps].count)
                   if gaps else 0.0)
        prev_top = None
        # Consecutive verbatim lines are ONE fenced block. Emitted as ordinary
        # lines each becomes its own paragraph, so a code listing arrives with
        # a blank line between every statement.
        fence_open = False
        fence_size = 0.0
        # Everything a crop covers is IN the picture. Emitting the text spans
        # of those lines as well duplicates them beside the image as stray
        # fragments -- `arbitrary ,` and a lone `0` next to a matrix that
        # already shows both.
        crop_boxes = [r for r in crops.values() if r is not None]
        # A figure belongs to a COLUMN, like a line. Placing it by y alone
        # put it at the top of the page while its caption -- correctly read
        # with the right column -- landed forty lines later. It is emitted
        # when the flow reaches a line of the same column below it.
        lefts = [ln.rect[0] for ln in p.lines if ln.glyphs]
        rights = [ln.rect[2] for ln in p.lines if ln.glyphs]
        _tw = (max(rights) - min(lefts)) if lefts else 1.0
        _mid = 0.5 * (min(lefts) + max(rights)) if lefts else 0.0

        def _col_of(rect) -> int:
            if (rect[2] - rect[0]) > 0.7 * _tw:
                return 2
            return 0 if 0.5 * (rect[0] + rect[2]) < _mid else 1

        # DISPLAY RUNS, decided in one pass BEFORE anything is emitted.
        #
        # Fusing incrementally during emission meant any material between two
        # display rows broke the chain -- an inline fragment, a crop -- and
        # incremental emission cannot see past it. Measured: 91.pdf stayed at
        # 25 blocks for 2 authored equations because `$\bigl($` sat between
        # its rows.
        #
        # Adjacency is computed over the DISPLAY groups ALONE, in page order,
        # so what lies between them is irrelevant to whether they belong to
        # one equation. This is the refactor 721 said was needed instead of
        # another threshold.
        # 730 — THE LOOK-AHEAD, replacing the vertical-gap threshold.
        #
        # Nothing inside a display says where it stops. The line AFTER it
        # announces itself, by starting at the body margin rather than the
        # display indent. That is the only statement of a display's end that
        # exists on the page, and it is the rule used here.
        #
        # The gap rule this replaces asked "are these two display rows close
        # enough vertically" (-1.5*size .. 2.0*size). It got both directions
        # wrong. A fraction, a sum's limits or a tall delimiter pushes two
        # rows of ONE equation further apart than 2.0*size, so the equation
        # split; and nothing stopped two genuinely separate displays with
        # prose between them from fusing, because the scan deliberately
        # looked only at display groups and ignored everything between.
        #
        # Measured over the 421 gold display equations of the PDF2LaTeX set:
        # stacked equations (fraction / operator limits / stacked rows) were
        # delivered whole 2 times in 281 under the gap rule.
        #
        # What may close a run, then, is exactly one thing: a return to the
        # body margin. An indented fragment (`$\bigl($`) does not -- it is
        # still inside the display, which is why 91.pdf's rows fell apart.
        # A crop does not. A heading does, and so does the page end.
        # ONE guard remains beside the margin rule, for the case the margin
        # cannot see: two displays with NOTHING between them -- no prose, no
        # fragment -- which is a real shape (a lemma's two equations) and
        # which the margin rule alone would fuse. It is a vertical gap, but a
        # generous one: rows of a single equation sit within about one
        # display leading of each other, while two equations are separated by
        # that plus a display skip. Swept over the gold set below.
        run_of: dict[int, int] = {}
        run_id = 0
        open_run = False
        prev_bottom = None
        _GAP = float(os.environ.get("PDF2MMD_RUN_GAP", "3.0"))

        def _at_margin(group) -> bool:
            """Does any line of this group BEGIN at the body margin?"""
            for ln in group:
                if ln.rotated or not ln.glyphs:
                    continue
                size = max(g.size for g in ln.glyphs)
                if ln.rect[0] <= left + 1.2 * size:
                    return True
            return False

        for gi, (lvl, group) in enumerate(grouped[p.page]):
            if not group:
                continue
            if not lvl and all(is_display(ln, left) for ln in group):
                top = group[0].rect[3]
                size = max((g.size for ln in group for g in ln.glyphs),
                           default=10.0)
                if open_run and prev_bottom is not None \
                        and prev_bottom - top > _GAP * size:
                    open_run = False
                if not open_run:
                    run_id += 1
                    open_run = True
                run_of[gi] = run_id
                prev_bottom = group[-1].rect[1]
                continue
            # Not a display. It ends the run only if it comes back to the
            # margin -- or is a heading, which always does.
            if lvl or _at_margin(group):
                open_run = False

        run_text: dict[int, list] = {}
        pending = sorted(p.diagrams, key=lambda d: -d[3])
        for gi, (lvl, group) in enumerate(grouped[p.page]):
            top = max(ln.rect[3] for ln in group)
            gcol = _col_of((min(ln.rect[0] for ln in group),
                            min(ln.rect[1] for ln in group),
                            max(ln.rect[2] for ln in group),
                            max(ln.rect[3] for ln in group)))
            ready = [d for d in pending
                     if d[3] > top and _col_of(d) in (gcol, 2)]
            for d in ready:
                pending.remove(d)
                out.append("")
                out.append(f"![diagram]({crop_url(d, p, doc_id, base, px_per_pt)})")
                out.append("")
            chunks = []
            for ln in group:
                if ln.rotated and not keep_rotated:
                    continue
                parts: list[str] = []
                for sp in ln.spans:
                    if sp.kind == "text":
                        # Frame decoration is not content: the corner glyphs
                        # of a listing box come from a drawing font and
                        # render as `(cid:7)`.
                        if sp.glyphs and all(texmap.is_drawing(g.fontname)
                                             for g in sp.glyphs):
                            continue
                        if _mostly_inside(sp.rect, crop_boxes):
                            continue      # already inside the picture
                        parts.append(_linked_text(sp, p, sp.word_gap))
                        continue
                    covered = _mostly_inside(sp.rect, crop_boxes)
                    tex = docmodel.span_latex(sp)
                    if covered and tex is not None:
                        continue          # the crop already shows it
                    if tex is not None and is_emittable(tex) and _inline(tex):
                        parts.append(_inline(tex))
                    elif crop_deferred:
                        rect = crops.get(sp.id, sp.rect)
                        if rect is None:
                            continue          # covered by a merged crop
                        url = crop_url(rect, p, doc_id, base, px_per_pt)
                        # The node id and reason go in the image ALT text, not
                        # in an HTML comment. Markdown viewers that run a
                        # typographer turn `--` into an en dash, which breaks
                        # `<!-- ... -->` and prints the comment as visible
                        # text. Alt text survives, and is what a reader sees
                        # when the image server is not running.
                        parts.append(
                            f"![{sp.id} {docmodel.span_reason(sp)}]({url})")
                    else:
                        parts.append(docmodel.span_text(sp))
                chunks.append(" ".join(x for x in parts if x))
            # Rotated lines are excluded above, so a rotated group must not
            # qualify as verbatim either: a stamp whose font happens to
            # measure as monospace would otherwise be fenced back into the
            # body it was just filtered out of.
            verb = (all(ln.verbatim for ln in group)
                    and not any(ln.rotated for ln in group))
            if fence_open and not verb and _is_gutter_only(group, fence_size):
                # A blank code line still carries its number. Emitting an
                # empty line instead loses it, and a listing that shows 1, 2,
                # 4, 6 has a hole a reader cannot explain.
                if line_numbers:
                    out.append(docmodel._run_text(
                        sorted((g for ln in group for g in ln.glyphs),
                               key=lambda g: g.rect[0])).strip())
                else:
                    out.append("")
                continue
            if verb:
                # The fence's type size must be known BEFORE the first line
                # is rendered, or that line keeps its gutter number while
                # every other line loses it.
                fence_size = fence_size or max(
                    (g.size for ln in group for g in ln.glyphs
                     if texmap.is_monospace(g.fontname)), default=10.0)
                text = " ".join(
                    docmodel._run_text(
                        ln.glyphs if line_numbers
                        else _strip_gutter(ln.glyphs, fence_size),
                        ln.spans[0].word_gap if ln.spans else 0)
                    for ln in group).rstrip()
                # Frame decoration is not code: the corner glyphs of a listing
                # box come from a drawing font and render as `(cid:7)`.
                text = re.sub(r"\(cid:\d+\)", "", text).strip()
                if not text:
                    continue
                if not fence_open:
                    out.append("")
                    out.append("```")
                    fence_open = True
                    fence_size = max(
                        (g.size for ln in group for g in ln.glyphs
                         if texmap.is_monospace(g.fontname)), default=10.0)
                out.append(text)
                continue
            if fence_open:
                out.append("```")
                out.append("")
                fence_open = False

            body = " ".join(c for c in chunks if c)
            if not body.strip():
                continue
            displayable = (
                not lvl
                and all(is_display(ln, left) for ln in group)
                # Every maths span must actually project. A matrix is drawn
                # as separate rows with extensible fences; each row satisfies
                # "indented, no prose" and would become its own `$$` block of
                # `0 i 0 i`, which is not an equation. If any span defers, the
                # line falls through and becomes a crop instead.
                and all(is_emittable(docmodel.span_latex(sp))
                        for ln in group for sp in ln.spans
                        if sp.kind == "math")
            )
            if displayable:
                # A display equation is a block, not an inline span. Wrapping
                # it in $$ is what makes a renderer centre it on its own line
                # instead of running it into the surrounding paragraph.
                # Walk ALL spans in order, not just the maths ones. An
                # aligned equation sets its relation symbols in a text font
                # and in their own column; emitting only maths spans dropped
                # every `=` in the document, turning `X^{-1} = \bar X = a1...`
                # into `X^{-1} X a1...`.
                pieces = []
                tagged = {id(g) for ln in group
                          for g in equation_number(ln, _right_margin(p))}
                for ln in group:
                    for sp in ln.spans:
                        if sp.glyphs and all(id(g) in tagged
                                             for g in sp.glyphs
                                             if g.text.strip()):
                            continue          # the equation number

                        if sp.kind == "math":
                            pieces.append(docmodel.span_latex(sp) or "")
                            continue
                        t = docmodel._run_text(sp.glyphs).strip()
                        if not t:
                            continue
                        if re.fullmatch(r"[-+=<>/(){}\[\],.;:|*!'\d\s]+", t):
                            pieces.append(t)          # operators and numbers
                        else:
                            pieces.append(rf"\text{{{t}}}")
                inner = " ".join(x for x in pieces if x).strip()
                # A tag SHARING a span with the mathematics survives the span
                # filter above, so remove the token here. This strips only
                # what was positively identified by geometry -- a
                # parenthesised number, at the right margin, behind a gap of
                # more than three em -- and only when it is what the text
                # actually ends with.
                for ln in group:
                    tag = "".join(g.text for g in equation_number(
                        ln, _right_margin(p))).strip()
                    if tag and inner.rstrip().endswith(tag):
                        inner = inner.rstrip()[:-len(tag)].rstrip()
                extras = ""
                # A display block must SAY something. `$$ $$` and
                # `$$\bigr)$$` are not equations; they were 55 empty and
                # ~20 lone-delimiter rows of the 836 this file emitted
                # across 102 documents. Falling through here makes the line
                # a crop, which is a refusal and therefore cannot be wrong
                # in the way an emission can.
                if is_emittable(inner) and has_content(inner):
                    # FUSE with the display block immediately above when the
                    # two are adjacent on the page. A `cases`, an `align` or
                    # any multi-line display arrives as one group per LINE,
                    # and opening a new `$$` for each turned one authored
                    # equation into several blocks.
                    #
                    # Measured against the PDF2LaTeX gold set: the author's
                    # environment count is 421; this file emitted roughly
                    # four times that, while `equations.json` -- which has
                    # fused runs since 707 -- emitted 1.9x. The difference
                    # between the two outputs of the SAME tool was this.
                    top = group[0].rect[3]
                    size = max((g.size for ln in group for g in ln.glyphs),
                               default=10.0)
                    # One PLACEHOLDER per run, filled at the end.
                    #
                    # Patching `out` in place could not reach back past a
                    # crop or an inline fragment, so the run pass computed
                    # eight runs for a page while twenty-six blocks came
                    # out. The placeholder holds the run's position in the
                    # document; the text is accumulated separately and
                    # substituted once the page is done.
                    rid = run_of.get(gi)
                    if rid is not None:
                        if rid not in run_text:
                            run_text[rid] = []
                            out.append("")
                            out.append(f"\x00RUN{rid}\x00")
                            out.append("")
                        # Keep each fragment's BASELINE with it. A run holds
                        # two different things -- the rows of a multi-row
                        # display, and the pieces of a SINGLE row that the
                        # grouper split -- and only the baseline tells them
                        # apart. Without it `L^{-1}dL = Q+P` came back as
                        # `L^{-1} \\ dL = Q + P`: one line printed as two.
                        # The BASELINE, not the box top. A fragment carrying
                        # a fraction is taller than one that does not, so its
                        # `rect` top sits higher while it is on the very same
                        # line -- which is how `\mathcal{L}_{coset}` and
                        # `= -\frac{1}{4} eP...` were still printed as two
                        # rows after the box top was tried.
                        from collections import Counter as _C
                        _bl = _C(round(gg.baseline, 1)
                                 for ln2 in group for gg in ln2.glyphs)
                        _base = _bl.most_common(1)[0][0] if _bl else top
                        run_text[rid].append(
                            (_base, size, balance_delims(inner.strip())))
                        continue
                    out.append("")
                    out.append("$$")
                    out.append(balance_delims(inner.strip()))
                    out.append("$$")
                    if extras:
                        out.append(extras)
                    out.append("")
                    continue
            if lvl:
                out.append("")
                out.append("#" * lvl + " " + body)
                out.append("")
                continue
            if (prev_top is not None and leading > 0
                    and (prev_top - top) > 1.6 * leading):
                if out and out[-1].strip():
                    out.append("")
            prev_top = top
            out.append(body)
            # A line that stops well short of the right margin ended its
            # paragraph. Without this every line of an address block is
            # reflowed into one run-on paragraph, because Markdown joins
            # adjacent lines by design.
            # The margin is the one THIS line's own column justifies to, not
            # the page's widest edge. A narrow block -- an abstract set inside
            # a two-column page -- stops far short of the page margin on every
            # line, so every line looked like a paragraph end and the whole
            # abstract came out one line per paragraph.
            last = group[-1]
            size = max((g.size for g in last.glyphs), default=10.0)
            local = _column_margin(p, last, margin)
            # A line ending in a hyphen is mid-word: it cannot be the end of
            # a paragraph, whatever its length. Breaking there put a blank
            # line between the two halves and left the word broken for good.
            if (last.rect[2] < local - 1.5 * size
                    and not body.rstrip().endswith(("-", "\u00ad"))):
                out.append("")
        if fence_open:
            out.append("```")
            out.append("")
            fence_open = False
        for d in pending:
            out.append("")
            out.append(f"![diagram]({crop_url(d, p, doc_id, base, px_per_pt)})")
            out.append("")
        # Fill this page's run placeholders. Done per page, because a run
        # never spans a page break and the ids restart.
        for i, item in enumerate(out):
            if item.startswith("\x00RUN") and item.endswith("\x00"):
                rid = int(item[4:-1])
                # 732 — THE ROWS OF A FUSED RUN MUST ACTUALLY BREAK.
                #
                # The run pass gathers a multi-row display into one block,
                # which is right, and then joined the rows with a newline --
                # which LaTeX eats. wzlxjtu-009's second display is three
                # rows, `S_{AB}=...`, `N_{AB}=...`, `M^I_{AB}=...`; every
                # symbol came back, in order, run together as ONE line. The
                # content was never the problem; the row structure was
                # discarded at the last step before printing.
                frags = [f for f in run_text.get(rid, []) if f[2].strip()]
                rows: list[str] = []
                for top, size, txt in frags:
                    # same baseline as the fragment before it => same ROW,
                    # joined by a space; a new baseline => a new row.
                    if rows and abs(top - prev_top) <= 0.5 * max(size, 1.0):
                        rows[-1] = rows[-1] + " " + txt
                    else:
                        rows.append(txt)
                    prev_top = top
                if len(rows) > 1:
                    body = ("\\begin{aligned}\n" + " \\\\\n".join(rows)
                            + "\n\\end{aligned}")
                else:
                    body = rows[0] if rows else ""
                out[i] = "$$\n" + body + "\n$$" if body.strip() else ""
        out.append("")
    text = "\n".join(out)
    # Rejoin words the typesetter broke across a line. Done on the
    # finished text so the document itself supplies the evidence for
    # which joins are real.
    if join_hyphens:
        text = dehyphenate(text, _vocabulary(text))
    return text



_TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%",
                "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
                "}": r"\}", "~": r"\textasciitilde{}",
                "^": r"\textasciicircum{}"}



def _tex_color(rgb) -> str:
    """An xcolor operand: `\\textcolor[rgb]{r,g,b}` takes values in 0..1."""
    return ",".join(f"{v:.4g}" for v in rgb)


def _fill_under(page: PageNode, rect):
    """The background fill covering this rectangle, if any."""
    cx = 0.5 * (rect[0] + rect[2])
    cy = 0.5 * (rect[1] + rect[3])
    for fl in page.fills:
        if (fl.rect[0] <= cx <= fl.rect[2] and fl.rect[1] <= cy <= fl.rect[3]
                and fl.color):
            return fl.color
    return None


def _escape_tex(t: str) -> str:
    """Escape TeX specials in prose destined for a .tex file.

    Markdown only needed `$` guarded; LaTeX needs all ten. A code listing
    containing `\n` was emitted as a raw control sequence, so the document
    would not compile -- found by the package check, which reported `\n`,
    `\nSum` and friends as commands defined by no package.
    """
    return "".join(_TEX_SPECIAL.get(c, c) for c in t)


def to_latex(pages: list[PageNode], doc_id: str = "pdfdrill",
             base: str = "http://localhost:8000",
             px_per_pt: float = DEFAULT_PX_PER_PT,
             preamble: bool = True,
             unicode_fonts: bool = False) -> str:
    """LaTeX using the same crop URLs inside \\includegraphics, as Mathpix does.

    With a PREAMBLE derived from the symbols actually used. We can do this
    because every symbol we emit was chosen from a known font, and the font
    says which package defines it -- the glyph/package correlation Mathpix
    did not keep. A document with no fraktur does not load amsfonts.
    """
    fp = profile(pages)
    sect = {1: "section", 2: "subsection", 3: "subsubsection"}
    out: list[str] = []
    for p in pages:
        out.append(f"% ---- page {p.page} ----")
        out.append(r"\newpage")
        for ln in p.lines:
            lvl = heading_level(ln, fp)
            parts: list[str] = []
            for sp in ln.spans:
                if sp.kind == "text":
                    txt = _escape_tex(
                        docmodel._run_text(sp.glyphs, sp.word_gap))
                    # Colour goes in the LaTeX, never in the Markdown --
                    # Markdown has no way to say it, and Mathpix drops it
                    # from the .md for the same reason.
                    cols = {g.color for g in sp.glyphs if g.color}
                    if len(cols) == 1 and txt.strip():
                        txt = (rf"\textcolor[rgb]{{{_tex_color(cols.pop())}}}"
                               rf"{{{txt}}}")
                    back = _fill_under(p, sp.rect)
                    if back and txt.strip():
                        txt = (rf"\colorbox[rgb]{{{_tex_color(back)}}}"
                               rf"{{{txt}}}")
                    parts.append(txt)
                    continue
                tex = docmodel.span_latex(sp)
                if tex is not None and is_emittable(tex):
                    parts.append(f"${tex}$")
                else:
                    url = crop_url(sp.rect, p, doc_id, base, px_per_pt)
                    parts.append(
                        "\n".join([
                            r"\begin{figure}[H]",
                            rf"  \includegraphics[alt={{}},max width=\textwidth]{{{url}}}",
                            rf"  % unprojected: {sp.id} {docmodel.span_reason(sp)}",
                            r"\end{figure}",
                        ])
                    )
            body = " ".join(x for x in parts if x)
            if lvl:
                out.append(rf"\{sect[lvl]}{{{body}}}")
            else:
                out.append(body)
        out.append("")
    body = "\n".join(out)
    if not preamble:
        return body

    import texpackages
    pkgs = texpackages.packages_for(body)
    if "\\includegraphics" in body and "graphicx" not in pkgs:
        pkgs.append("graphicx")
    if unicode_fonts:
        pkgs.append("fontspec")
    unknown = texpackages.unknown_commands(body)

    head = [r"\documentclass{article}"]
    for name in pkgs:
        if name == "fontspec":
            # [no-math] so fontspec leaves the maths fonts alone: the symbols
            # projected here were chosen from the document's own TeX maths
            # fonts, and letting fontspec substitute would change them.
            head.append(r"\usepackage[no-math]{fontspec}")
        else:
            head.append(rf"\usepackage{{{name}}}")
    head += texpackages.provides_for(body)
    if unknown:
        # Surfaced, not shipped: Mathpix output that will not compile is the
        # failure this exists to avoid.
        head.append("% WARNING: commands with no known package: "
                    + " ".join(sorted(r"\\" + u for u in unknown))[:400])
    head.append(r"\begin{document}")
    return "\n".join(head) + "\n" + body + "\n" + r"\end{document}" + "\n"


def font_report(pages: list[PageNode]) -> str:
    """Document the type sizes and fonts, and what was inferred from them.

    A PDF does not say which lines are headings. This table is the evidence
    behind that inference, so a wrong heading can be traced to the measurement
    that produced it rather than argued about.
    """
    fp = profile(pages)
    total = sum(fp.sizes.values())
    rows = ["| size (pt) | glyphs | share | rank | meaning |",
            "|---:|---:|---:|---:|---|"]
    meaning = {0: "body / smaller", 1: "###", 2: "##", 3: "#"}
    for size, n in sorted(fp.sizes.items(), key=lambda kv: -kv[1])[:12]:
        r = fp.rank(size)
        rows.append(f"| {size} | {n} | {100.0 * n / max(total, 1):.1f}% | "
                    f"{r} | {meaning[r]} |")
    frows = ["", "| font | glyphs | share | bold? |", "|---|---:|---:|:--:|"]
    ftotal = sum(fp.fonts.values())
    for f, n in fp.fonts.most_common(12):
        frows.append(f"| `{f}` | {n} | {100.0 * n / max(ftotal, 1):.1f}% | "
                     f"{'yes' if _is_bold(f) else ''} |")
    head = [
        "## Font profile",
        "",
        f"Body text: **{fp.body_size} pt** in `{fp.body_font}` "
        f"(the size most glyphs are set in, not the smallest).",
        "",
        "Heading levels are inferred from size relative to body, or from a "
        "bold-only line short enough not to be a paragraph. Nothing in the PDF "
        "states them.",
        "",
    ]
    return "\n".join(head + rows + frows) + "\n"
