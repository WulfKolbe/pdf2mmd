"""structure — rebuild maths structure from glyph geometry and rules.

This is the layout-tree unit. `docmodel` produced spans whose glyphs are
identified but whose vertical relationships were thrown away; 50.6 % of maths
spans deferred on exactly that. Here those relationships are recovered:

  U8 FRACTIONS  a rule with glyphs above and below is a `\\frac`. The rule's
                x-range bounds the numerator and denominator.
  U9 SCRIPTS    a glyph that is smaller than the row AND sits off the row's
                baseline is a super- or subscript of the nearest main glyph
                to its left.

DESIGN RULE, applied everywhere: when the geometry does not determine the
answer, return None and let the caller defer. A wrong `\\frac` or a
misattached `^{}` is valid LaTeX with different mathematics in it, and nothing
downstream can detect that. Silence is recoverable; a confident error is not.

LIMITS, stated rather than discovered later:
  - no radicals: `radicalbig` gives the sign, but the vinculum rule does not
    reliably bound the argument, so radical spans are refused outright
  - no matrices, no stacked limits above/below big operators
  - no \\left…\\right pairing across a whole expression
  - scripts attach to a single preceding glyph, not to a sub-expression
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from docmodel_six import MATH_FAMILIES, GlyphNode, RuleNode, glyph_latex
from texmap import TexToken, math_space

# A script is smaller than its base. Measured on the corpus: 6.97pt scripts
# under 9.96pt bases, a ratio of 0.70, so 0.92 separates them with margin.
SCRIPT_SIZE_RATIO = 0.92
# Vertical offset, as a fraction of row size, beyond which a same-row glyph is
# considered shifted rather than level.
# How far two glyphs may sit apart in baseline and still count as one level.
#
# 0.12 em, by sweep. At 0.08 an author line refused to project because its
# two superscript markers -- an `a` in the body face and a `*` in STIXMath --
# sit 1.0pt apart at 7pt, which is 0.14em: different FONTS place a raised
# glyph differently, and both are the same superscript. At 0.16 the tolerance
# starts swallowing real script levels: Gallier fell 98.7% -> 79.9% and
# Mielke 85.6% -> 61.6%.
LEVEL_TOL = 0.12


@dataclass
class Frac:
    rule: RuleNode
    num: list
    den: list


def _row_size(glyphs: list[GlyphNode]) -> float:
    return max(g.size for g in glyphs) if glyphs else 0.0


def _split_fractions(glyphs: list[GlyphNode], rules: list[RuleNode]):
    """Partition a span into fraction groups and the glyphs between them.

    A fraction's extent is its RULE's x-range: that is what the typesetter
    drew, so it needs no inference. Glyphs inside that range go above or below
    by baseline; glyphs outside stay at this level.
    """
    fracs: list[Frac] = []
    consumed: set[int] = set()
    for r in sorted(rules, key=lambda r: r.rect[0]):
        if r.role not in ("fraction",):
            continue
        x0, x1 = r.rect[0], r.rect[2]
        mid = 0.5 * (r.rect[1] + r.rect[3])
        num, den = [], []
        for i, g in enumerate(glyphs):
            if i in consumed:
                continue
            cx = 0.5 * (g.rect[0] + g.rect[2])
            if cx < x0 - 1 or cx > x1 + 1:
                continue
            (num if g.baseline > mid else den).append(i)
        if not num or not den:
            continue                     # not a fraction after all; abstain
        consumed |= set(num) | set(den)
        fracs.append(Frac(r, [glyphs[i] for i in num], [glyphs[i] for i in den]))
    rest = [g for i, g in enumerate(glyphs) if i not in consumed]
    return fracs, rest


def _weave_underscores(triples, unders, depth: int) -> str | None:
    """Emit the row with its underscore rules back in x-order."""
    items: list[tuple[float, str]] = []
    for base, sup, sub in triples:
        if base is None:
            continue
        lit = glyph_latex(base)
        if lit is None:
            return None
        part = lit
        if sub:
            s = to_tex(sub, [], depth + 1)
            if s is None:
                return None
            part += f"_{{{s}}}"
        if sup:
            s = to_tex(sup, [], depth + 1)
            if s is None:
                return None
            part += f"^{{{s}}}"
        items.append((base.rect[0], part))
    for r in unders:
        items.append((r.rect[0], r"\_"))
    items.sort(key=lambda t: t[0])
    # The SECOND join site, and it had no guard either. `P` `\cdot` `y` came
    # out as `P\cdoty`: the pieces loop below guards its own joins, but a row
    # carrying scripts or underscores is assembled HERE instead, so the same
    # hazard reappeared in a path the first fix never touched.
    parts: list[str] = []
    for _, piece in items:
        if parts and piece[:1].isalpha() and _ends_in_command(parts[-1]):
            parts.append(" ")
        parts.append(piece)
    return "".join(parts)


def _attach_scripts(glyphs: list[GlyphNode]):
    """Split a row into (main glyph, superscripts, subscripts) triples.

    Returns None when a script cannot be attributed — a script with no glyph
    to its left has no base, and guessing one would invent a relationship.
    """
    if not glyphs:
        return []
    size = _row_size(glyphs)
    # A big operator is RAISED above the text baseline by design, so that it
    # and its limits centre on the maths axis. Judged by baseline alone it is
    # a full-size glyph off the row, which this function refuses -- so every
    # inline `\sum_{J}` deferred. It counts as a main glyph whatever its
    # baseline, and its scripts are measured against ITS baseline.
    # A big DELIMITER is off the row baseline for the same reason a big
    # OPERATOR is: it is grown about the maths axis, not set on the line.
    # `\left( ... \right)` around a two-level expression puts its parens at
    # 12pt on a baseline 10pt above the row, and the guard below -- which
    # refuses a full-size glyph off the row -- then refused the whole span.
    #
    # Measured on the PDF2LaTeX dataset: `script-attachment` was 299 of 448
    # crops, the largest cause by a wide margin, and this is one of its
    # shapes.
    bigops = {i for i, g in enumerate(glyphs)
              if g.tex.kind in ("bigop", "delimiter")}
    main_idx = [i for i, g in enumerate(glyphs)
                if g.size >= SCRIPT_SIZE_RATIO * size or i in bigops]
    if not main_idx:
        # Every glyph is a script and there is no base -- a marker group that
        # follows a WORD, whose last letter belongs to the prose. LaTeX spells
        # that with an empty group: `${}^{a,*}$`. Attributing the word's last
        # letter to the maths instead broke the name and moved the marker:
        # `Montañ $\mathrm{o}^{\mathrm{a},*}$`.
        return [(None, list(glyphs), [])] if all(
            g.baseline >= glyphs[0].baseline - 0.01 for g in glyphs) else None
    # The row's baseline is the one MOST glyphs sit on, not the highest.
    # Taking the maximum lets a single full-size element that happens to sit
    # high -- a fraction, a raised full-size script -- redefine the row and
    # push every real base into a subscript.
    from collections import Counter
    votes = Counter(round(glyphs[i].baseline / max(LEVEL_TOL * size, 0.01))
                    for i in main_idx)
    top = votes.most_common(1)[0][0]
    base_line = top * max(LEVEL_TOL * size, 0.01)
    # a full-size glyph that is nonetheless shifted is still a script
    main_idx = [i for i in main_idx
                if i in bigops
                or abs(glyphs[i].baseline - base_line) <= LEVEL_TOL * size]
    if not main_idx:
        return None

    # BINDING BY EMISSION ORDER WAS TRIED HERE AND REVERTED.
    #
    # 725 and 726 both concluded that `_attach_scripts` binding by x rather
    # than by stream was what blocked `_merge_stream_lines`; 696 had reached
    # the same conclusion from the other direction. Implemented -- a script
    # attaches to the main glyph most recently emitted before it, falling
    # back to x where any glyph lacks a stream index -- and measured:
    #
    #     merge OFF   blocks 817 -> 801   crops 548 -> 588   proj 86.4 -> 85.4
    #     merge ON    blocks  95          crops 1349         proj 60.9
    #
    # The merge still collapsed. The stream path WAS being taken (every
    # glyph on those pages carries an index) and it still refused, so the
    # binding rule was never the blocker.
    #
    # Instrumented, 17 of 18 refusals on 91.pdf are the "same baseline but
    # excluded" case below, not the off-baseline guard: a merged display
    # line contains whole sub-expressions set at script size on their OWN
    # baselines -- a fraction's numerator and denominator -- and this pass
    # models one baseline with scripts around it. That is a different pass,
    # not a different binding rule.
    #
    # The plan in 725/726 is falsified. Recorded here rather than in a note
    # alone, because the next person to read those notes will otherwise
    # implement it again.

    # 733 — BINDING BY STREAM INSIDE THIS PASS WAS TRIED AGAIN, AND AGAIN
    # IT IS NOT THE FIX. Recorded with numbers so it is not attempted a
    # fourth time.
    #
    # The stream genuinely carries the box structure -- measured on
    # wzlxjtu-010, `\sigma'=2\eta\sqrt{N_0^2+N_3^2}`:
    #
    #   by x       sigma prime equal two eta radicalBig N zero two plus N three two
    #   by stream  radicalBig sigma prime equal two eta N two zero plus N two three
    #
    # `N two zero` is base, superscript, subscript in TeX's own order, while
    # by x the two scripts are 1.2pt apart and stacked. It looks decisive.
    # It is not, because THIS PASS NEVER HAD TO ORDER THEM: it takes every
    # glyph between one main glyph and the next and sorts them into sup and
    # sub by BASELINE, so their order among themselves never mattered.
    #
    # Implemented as attach-by-stream / order-by-x, whole corpus:
    #
    #     attachment by x       whole 34   crops 452   flat exact 18
    #     attachment by stream  whole 28   crops 481   flat exact 13
    #
    # It loses, because the most recent main glyph IN THE STREAM is not
    # always the base: a producer ships an extensible glyph ahead of the run
    # it covers (`radicalBig` first, at x=286.3, before the `sigma` at 248.7
    # that starts the expression), and a script following it in the stream
    # then binds to the wrong thing.
    #
    # Where the stream DOES decide is one step earlier, in which glyphs form
    # a row at all -- see the band merge in docmodel_six. Same corpus, same
    # day: whole 34 -> 38. The evidence was always about grouping, never
    # about binding, and that is the distinction three notes have missed.

    out = []
    for pos, i in enumerate(main_idx):
        nxt = main_idx[pos + 1] if pos + 1 < len(main_idx) else len(glyphs)
        sup, sub = [], []
        for j in range(i + 1, nxt):
            g = glyphs[j]
            # Only a SMALLER glyph may be a script. A full-size glyph sitting
            # off the row baseline is structure this pass does not model --
            # a fraction whose numerator escaped its rule's x-range, a stacked
            # limit, a matrix row. Calling it a script produced `+^{( -}` from
            # `+\frac{(-1)^{s}}{2}`: confident, valid, wrong.
            if g.size >= SCRIPT_SIZE_RATIO * size and j not in bigops:
                return None
            if g.baseline > glyphs[i].baseline + LEVEL_TOL * size:
                sup.append(g)
            elif g.baseline < glyphs[i].baseline - LEVEL_TOL * size:
                sub.append(g)
            else:
                return None              # same baseline but excluded: unclear
        out.append((glyphs[i], sup, sub))

    # 735 — A LEADING SCRIPT IS NOT A REASON TO DROP THE ROW.
    #
    # This refused whenever the leftmost glyph was a script, and it was the
    # largest single cause of deferral after the band merge: 4,573 of them
    # across the 102 documents, each one taking a whole display with it.
    #
    # The row is not unreadable. LaTeX has a spelling for a script with no
    # base -- `{}^{x}` -- and this pass ALREADY emits it, three branches up,
    # for a marker group whose base belongs to the prose. What was missing is
    # that a row can begin that way and still be a row: `(\phi^0)'` whose
    # prime was carried in from another band, a continuation whose base sits
    # on the line above.
    #
    # Emitting `{}` is not a guess. It says exactly what is true -- these
    # scripts have no base HERE -- which is the distinction this file keeps:
    # a wrong `\frac` is worse than a crop, and an honest `{}` is neither.
    if main_idx and main_idx[0] != 0:
        lead_sup, lead_sub = [], []
        for g in glyphs[:main_idx[0]]:
            if g.size >= SCRIPT_SIZE_RATIO * size:
                return None              # a full-size leading glyph is structure
            if g.baseline > base_line + LEVEL_TOL * size:
                lead_sup.append(g)
            elif g.baseline < base_line - LEVEL_TOL * size:
                lead_sub.append(g)
            else:
                return None
        return [(None, lead_sup, lead_sub)] + out
    return out


# Computer Modern builds `\mapsto` from TWO glyphs: the bar (`mapsto`) and an
# arrow (`arrowright`). Projected separately they read `\mapsto \rightarrow`,
# which is one arrow too many.
_TWO_GLYPH = {("mapsto", "arrowright"): r"\mapsto",
              ("mapsto", "arrowdblright"): r"\Mapsto"}

# `negationslash` is a ZERO-WIDTH overlay: TeX draws it across the relation it
# negates, so the pair is one symbol. Its box says nothing about which side the
# relation is on -- measured, it appears immediately before an `equal` in some
# places and immediately after one in others -- so both orders are accepted and
# the neighbour it TOUCHES is the one it belongs to.
_NEGATED = {
    "equal": r"\neq", "element": r"\notin", "similar": r"\nsim",
    "equivalence": r"\not\equiv", "propersubset": r"\not\subset",
    "propersuperset": r"\not\supset", "reflexsubset": r"\nsubseteq",
    "lessequal": r"\nleq", "greaterequal": r"\ngeq",
    "bar": r"\nmid", "arrowright": r"\nrightarrow",
}


def _merge_negations(glyphs: list[GlyphNode]) -> list[GlyphNode]:
    """Fuse a negation slash with the relation it strikes through.

    Decided in one pass and built in a second. Building as it went emitted the
    relation twice whenever the slash's partner was the PRECEDING glyph: that
    glyph had already been appended, giving `= \\neq`.
    """
    replace: dict[int, str] = {}
    drop: set[int] = set()
    for i, g in enumerate(glyphs):
        if g.tex.kind != "overlay" or i in drop:
            continue
        for j in (i + 1, i - 1):        # `\not` precedes, so look right first
            if not 0 <= j < len(glyphs) or j in drop or j in replace:
                continue
            n = glyphs[j]
            latex = _NEGATED.get(n.glyphname or "")
            if not latex:
                continue
            gap = max(g.rect[0], n.rect[0]) - min(g.rect[2], n.rect[2])
            if gap <= 0.35 * max(g.size, n.size):
                replace[j] = latex
                drop.add(i)
                break

    out: list[GlyphNode] = []
    for i, g in enumerate(glyphs):
        if i in drop:
            continue                     # the slash itself is consumed
        if i in replace:
            out.append(GlyphNode(
                id=g.id, page=g.page, rect=g.rect, text=g.text, cid=-1,
                glyphname=g.glyphname, fontname=g.fontname, family=g.family,
                size=g.size, tex=TexToken(replace[i], "atom", None, "corpus"),
                matrix=g.matrix, upright=g.upright, stream=g.stream))
            continue
        out.append(g)
    return out


def _merge_mapsto(glyphs: list[GlyphNode]) -> list[GlyphNode]:
    """Collapse the glyph PAIRS that TeX uses to build one symbol."""
    out: list[GlyphNode] = []
    i = 0
    while i < len(glyphs):
        if i + 1 < len(glyphs):
            key = (glyphs[i].glyphname, glyphs[i + 1].glyphname)
            latex = _TWO_GLYPH.get(key)
            if latex and (glyphs[i + 1].rect[0] - glyphs[i].rect[2]
                          < 0.6 * glyphs[i].size):
                a, b = glyphs[i], glyphs[i + 1]
                out.append(GlyphNode(
                    id=a.id, page=a.page,
                    rect=(a.rect[0], min(a.rect[1], b.rect[1]),
                          b.rect[2], max(a.rect[3], b.rect[3])),
                    text=a.text + b.text, cid=-1, glyphname=key[0],
                    fontname=a.fontname, family=a.family, size=a.size,
                    tex=TexToken(latex, "atom", None, "corpus"),
                    matrix=a.matrix, upright=a.upright,
                    stream=min((x.stream for x in (a, b) if x.stream >= 0),
                               default=-1)))
                i += 2
                continue
        out.append(glyphs[i])
        i += 1
    return out


def _merge_accents(glyphs: list[GlyphNode],
                   depth: int = 0) -> list[GlyphNode]:
    """Wrap the glyphs an accent sits over.

    A wide accent is a SEPARATE glyph drawn above its base: `\\widehat{\\otimes}`
    is cmex10's `hatwide` over cmsy10's `circlemultiply`. Projected side by
    side it yields `\\widehat \\otimes`, which is not valid LaTeX -- `\\widehat`
    takes an argument -- and because the accent sits on its own raised
    baseline the span defers instead.

    The base is whatever the accent SPANS horizontally. An accent may also
    carry zero advance width, in which case its box says nothing about extent,
    so the nearest glyph below it is taken instead.
    """
    accents = [i for i, g in enumerate(glyphs) if g.tex.kind == "accent"
               and g.tex.latex]
    if not accents:
        return glyphs

    consumed: set[int] = set()
    wrapped: dict[int, tuple[str, list[int]]] = {}
    for i in accents:
        a = glyphs[i]
        width = a.rect[2] - a.rect[0]
        under = []
        for j, g in enumerate(glyphs):
            if j == i or j in consumed or g.tex.kind == "accent":
                continue
            # A CMEX accent carries its raise INSIDE the glyph, so its origin
            # sits on the text baseline -- exactly level with the letter it
            # covers. Measured: `tildewide` and its `gamma` both at 675.7.
            # Requiring the base to be strictly lower found nothing, and every
            # `\widetilde{\gamma}` deferred. Only a base clearly ABOVE the
            # accent is excluded.
            if g.baseline > a.baseline + 0.3 * a.size:
                continue
            if width > 0.1 * a.size:
                cx = 0.5 * (g.rect[0] + g.rect[2])
                if a.rect[0] - 0.15 * a.size <= cx <= a.rect[2] + 0.15 * a.size:
                    under.append(j)
            else:
                # zero-width accent: it marks a position, not an extent
                if abs(g.rect[0] - a.rect[0]) <= 0.6 * a.size:
                    under.append(j)
                    break
        if not under:
            continue
        consumed.update(under)
        consumed.add(i)
        wrapped[min(under)] = (a.tex.latex, sorted(under))

    if not wrapped:
        return glyphs

    out: list[GlyphNode] = []
    for i, g in enumerate(glyphs):
        if i in wrapped:
            cmd, idxs = wrapped[i]
            # ordinary recursion: the accent itself is already consumed,
            # so this cannot re-enter on the same glyph
            inner = to_tex([glyphs[k] for k in idxs], [], depth + 1)
            if inner is None:
                return glyphs              # cannot build it: let the span defer
            base = glyphs[idxs[0]]
            rect = (min(glyphs[k].rect[0] for k in idxs),
                    min(glyphs[k].rect[1] for k in idxs),
                    max(glyphs[k].rect[2] for k in idxs),
                    max(glyphs[k].rect[3] for k in idxs))
            out.append(GlyphNode(
                id=base.id, page=base.page, rect=rect, text=base.text,
                cid=-1, glyphname=base.glyphname, fontname=base.fontname,
                family=base.family, size=base.size,
                tex=TexToken(rf"{cmd}{{{inner}}}", "atom", None, "corpus"),
                matrix=base.matrix, upright=base.upright,
                stream=min((glyphs[k].stream for k in idxs
                            if glyphs[k].stream >= 0), default=-1)))
            continue
        if i in consumed:
            continue
        out.append(g)
    return out


def _merge_operator_runs(glyphs: list[GlyphNode]) -> list[GlyphNode]:
    """Collapse runs of upright letters into one operator token.

    `Spin`, `Cl`, `Tr`, `det` are set in an UPRIGHT text face inside
    mathematics. Emitted letter by letter they become `S p i n`, which is a
    product of four variables, not an operator name. LaTeX writes them
    `\\mathbf{Spin}` or `\\mathrm{Cl}`, and the face says which: bold for a
    group name, roman for an operator.

    Italic letters are NOT merged: those are variables, and each can carry its
    own script.
    """
    from project_mmd import _is_bold
    from texmap import is_italic

    def upright(g: GlyphNode) -> bool:
        if g.family in MATH_FAMILIES:
            return False
        if is_italic(g.fontname):
            return False
        return len(g.text) == 1 and g.text.isalpha() and g.text.isascii()

    out: list[GlyphNode] = []
    i = 0
    while i < len(glyphs):
        g = glyphs[i]
        if not upright(g):
            out.append(g)
            i += 1
            continue
        run = [g]
        j = i + 1
        while j < len(glyphs):
            n = glyphs[j]
            if (not upright(n) or n.fontname != g.fontname
                    or abs(n.size - g.size) > 0.05 * g.size
                    or abs(n.baseline - g.baseline) > 0.05 * g.size
                    or n.rect[0] - run[-1].rect[2] > 0.28 * g.size):
                break
            run.append(n)
            j += 1
        if len(run) < 2:
            out.append(g)
            i += 1
            continue
        name = "".join(x.text for x in run)
        cmd = "mathbf" if _is_bold(g.fontname) else "mathrm"
        rect = (run[0].rect[0], min(x.rect[1] for x in run),
                run[-1].rect[2], max(x.rect[3] for x in run))
        out.append(GlyphNode(
            id=run[0].id, page=run[0].page, rect=rect, text=name,
            cid=-1, glyphname=None, fontname=g.fontname, family=g.family,
            size=g.size, tex=TexToken(rf"\{cmd}{{{name}}}", "atom", None,
                                      "corpus"),
            matrix=run[0].matrix, upright=True,
            stream=min((x.stream for x in run if x.stream >= 0), default=-1)))
        i = j
    return out


def _merge_overlines(glyphs: list[GlyphNode],
                     rules: list[RuleNode], depth: int):
    """Wrap the glyphs a bar sits over, and report which bars were used.

    `\\bar{x}` and `\\overline{A}` are a rule with glyphs BELOW it and nothing
    above. The conjugate in a Cayley-algebra text is written that way
    throughout, and ignoring the rule turned every `\\bar{x}` into a plain `x`
    -- the bar vanished with no trace that anything had been dropped.
    """
    bars = [r for r in rules if r.role == "overline"]
    if not bars:
        return glyphs, set()

    used: set[int] = set()
    consumed: set[int] = set()
    wrapped: dict[int, tuple[str, list[int]]] = {}
    for bi, r in enumerate(bars):
        mid = 0.5 * (r.rect[1] + r.rect[3])
        under = [i for i, g in enumerate(glyphs)
                 if i not in consumed and g.baseline < mid
                 and r.rect[0] - 1 <= 0.5 * (g.rect[0] + g.rect[2])
                 <= r.rect[2] + 1]
        if not under:
            continue
        # A RADICAL is this same shape with a sign in front of it: TeX draws
        # `\sqrt{x}` as the radical glyph plus a vinculum over the radicand,
        # and the vinculum is an overline in every respect the classifier can
        # see. Told apart by what stands immediately to the bar's LEFT.
        #
        # `to_tex` refused any span containing a radical outright ("see
        # LIMITS"), which cost 828 spans across the 102 documents -- the
        # author wrote 70 roots in 41 equations, 9.7% of the corpus, and not
        # one of them could ever be read. The machinery was already here.
        size = max((g.size for g in glyphs), default=10.0)
        sign = None
        for i, g in enumerate(glyphs):
            if i in consumed or not (g.glyphname or "").startswith("radical"):
                continue
            if abs(g.rect[2] - r.rect[0]) > 0.6 * size:
                continue
            if not (g.rect[1] - size <= mid <= g.rect[3] + size):
                continue
            sign = i
            break
        if sign is not None:
            consumed.add(sign)
            consumed.update(under)
            used.add(bi)
            wrapped[min([sign] + under)] = (r"\sqrt", sorted(under))
            continue
        consumed.update(under)
        used.add(bi)
        wrapped[min(under)] = (r"\overline", sorted(under))

    if not wrapped:
        return glyphs, set()

    out: list[GlyphNode] = []
    for i, g in enumerate(glyphs):
        if i in wrapped:
            cmd, idxs = wrapped[i]
            inner = to_tex([glyphs[k] for k in idxs], [], depth + 1)
            if inner is None:
                return glyphs, set()
            base = glyphs[idxs[0]]
            out.append(GlyphNode(
                id=base.id, page=base.page,
                rect=(min(glyphs[k].rect[0] for k in idxs),
                      min(glyphs[k].rect[1] for k in idxs),
                      max(glyphs[k].rect[2] for k in idxs),
                      max(glyphs[k].rect[3] for k in idxs)),
                text=base.text, cid=-1, glyphname=base.glyphname,
                fontname=base.fontname, family=base.family, size=base.size,
                tex=TexToken(rf"{cmd}{{{inner}}}", "atom", None, "corpus"),
                matrix=base.matrix, upright=base.upright,
                stream=min((glyphs[k].stream for k in idxs
                            if glyphs[k].stream >= 0), default=-1)))
            continue
        if i in consumed:
            continue
        out.append(g)
    return out, {id(bars[b]) for b in used}


def to_tex(glyphs: list[GlyphNode], rules: list[RuleNode] | None = None,
           depth: int = 0) -> str | None:
    """LaTeX for a glyph group, or None if the geometry is not decisive."""
    if depth > 6:
        return None
    # 734 — SORTING BY STREAM HERE WAS TRIED AND LOSES. The numbers, so
    # it is not tried a fourth time, and the distinction, which is real.
    #
    # The stream IS reading order inside a horizontal list. Measured,
    # wzlxjtu-009:
    #
    #   x        psi macron A ... psi B T      sigma A three B
    #   stream   macron psi A ... psi T B      sigma three A B
    #   author   \bar\psi_A ... \psi_B^T        \sigma^3_{AB}
    #
    # The accent precedes its base and the superscript precedes the
    # subscript, exactly as written -- and x cannot recover either, because
    # `\sigma`'s two scripts are 1.2pt apart and stacked.
    #
    # But a VERTICAL box is not shipped in reading order. TeX builds
    # `\prod_{i=0}^{3}` as a vbox and ships it top to bottom: upper limit,
    # operator, lower limit. Sorting the row by stream therefore puts the
    # limit before the operator that owns it:
    #
    #   gold   L=\prod_{i=0}^3 e^{\phi^i K^i}
    #   got    L =^{3} \prod_{i=0} e^{\phi^i K^i}
    #   gold   4(S_0^2+S_3^2)
    #   got    4 \bigl(S^{2}\bigr)_{0} + S_3^2
    #
    # Whole corpus: 38 delivered whole by x order, 34 by stream order, and
    # 6 equations lost against 1 gained -- every loss a stacked construction.
    #
    # So the stream decides WHICH GLYPHS BELONG TOGETHER, which is a local
    # question it answers exactly (see the band merge in docmodel_six,
    # whole 34 -> 38); x decides WHAT ORDER THE ROW READS IN, because that
    # is the question the page answers and the stream does not.
    glyphs = sorted(glyphs, key=lambda g: g.rect[0])
    if not glyphs:
        return ""
    glyphs = _merge_mapsto(glyphs)
    glyphs = _merge_negations(glyphs)
    glyphs = _merge_accents(glyphs, depth)
    glyphs = _merge_operator_runs(glyphs)
    if any(g.tex.kind == "fragment" for g in glyphs):
        return None

    rules = rules or []
    glyphs, used_bars = _merge_overlines(glyphs, rules, depth)
    # A radical that found no vinculum is still structure this pass cannot
    # describe, and it defers exactly as it always did. The check moved BELOW
    # the merge rather than away: what changed is that a root which WAS
    # assembled no longer counts against the span.
    if any(g.glyphname and g.glyphname.startswith("radical") for g in glyphs):
        return None                      # see LIMITS
    rules = [r for r in rules if id(r) not in used_bars]

    # Any rule still unaccounted for is structure this pass does not model.
    # Ignoring it is how `\bar{x}` became `x`: valid output, silently missing
    # the bar, with nothing to show a reader that anything was lost.
    # A separator is a frame edge or a rule between blocks: it says nothing
    # about the mathematics beside it, so it neither composes nor blocks.
    # An underscore is CONTENT, not structure: it is drawn as a rule but it
    # is a character. It joins the glyph stream in x-order rather than
    # blocking the span.
    unders = [r for r in rules if r.role == "underscore"]
    rules = [r for r in rules if r.role not in ("separator", "underscore")]
    if any(r.role not in ("fraction",) for r in rules):
        return None

    fracs, rest = _split_fractions(glyphs, rules)

    # A fraction rule that did not resolve into a numerator AND a denominator
    # must not be ignored. If it is, its numerator and denominator fall through
    # to `_attach_scripts` as ordinary glyphs on different baselines and come
    # out as `1_{\beta\gamma}` -- valid LaTeX with the fraction silently gone.
    # Deferring keeps the rectangle and the reason instead.
    if any(r.role == "fraction" for r in rules) and \
            len(fracs) != sum(1 for r in rules if r.role == "fraction"):
        return None

    if fracs:
        # A fraction is a single object in its row, so it must take part in
        # script attachment rather than short-circuiting it. Emitting `rest`
        # glyph-wise here would silently drop the scripts on the fraction's
        # neighbours — turning `\Gamma_{\alpha}^{\star}` into
        # `\Gamma \alpha \star`, which is valid LaTeX saying something else.
        row: list[GlyphNode] = list(rest)
        size = _row_size(glyphs)
        for f in fracs:
            num = to_tex(f.num, [], depth + 1)
            den = to_tex(f.den, [], depth + 1)
            if num is None or den is None:
                return None
            mid = 0.5 * (f.rule.rect[1] + f.rule.rect[3])
            row.append(
                GlyphNode(
                    id=f.rule.id, page=f.rule.page,
                    rect=(f.rule.rect[0], mid - size, f.rule.rect[2], mid + size),
                    text="", cid=-1, glyphname=None,
                    fontname="<fraction>", family="math-extension", size=size,
                    tex=TexToken(rf"\frac{{{num}}}{{{den}}}", "atom", None,
                                 "corpus"),
                    # the bar sits on the maths axis, about a quarter of the
                    # type size above the baseline it shares with its row
                    matrix=(size, 0, 0, size, f.rule.rect[0], mid - 0.25 * size),
                    # 733 — the synthesised atom keeps the EARLIEST stream
                    # index of the glyphs it replaces, so a row carrying a
                    # fraction can still be read in emission order. Without
                    # it every such row fell back to the x walk, which is
                    # exactly the row that needs the stream most.
                    stream=min((g.stream for g in (f.num + f.den)
                                if g.stream >= 0), default=-1),
                )
            )
        glyphs = sorted(row, key=lambda g: g.rect[0])
        rules = []

    triples = _attach_scripts(glyphs)
    if triples is None:
        return None
    if unders:
        return _weave_underscores(triples, unders, depth)

    out: list[str] = []
    bases: list = []
    for base, sup, sub in triples:
        if base is None:                 # a marker group with an empty base
            lit = "{}"
        else:
            lit = glyph_latex(base)
            if lit is None:
                return None
        part = lit
        bases.append(base if base is not None else (sup or sub)[0])
        if sub:
            s = to_tex(sub, [], depth + 1)
            if s is None:
                return None
            part += f"_{{{s}}}"
        if sup:
            s = to_tex(sup, [], depth + 1)
            if s is None:
                return None
            part += f"^{{{s}}}"
        out.append(part)

    # Join by the MEASURED gap, not with a blank. A LaTeX space emits no
    # glyph -- every one is a pure advance of the current point -- and the
    # advances are quantised in em, so the command is recoverable. Joining
    # with " " lost `\!` (a deliberate NEGATIVE space) and could not tell
    # `$ab$` from `$a\quad b$`.
    pieces: list[str] = []
    for i, part in enumerate(out):
        if i:
            prev, cur = bases[i - 1], bases[i]
            size = max(prev.size, cur.size, 1.0)
            gap = math_space((cur.rect[0] - prev.rect[2]) / size)
            # A multi-letter command needs a separator before a letter, or
            # `\quad` + `b` becomes the undefined control sequence
            # `\quadb`. TeX ends a control word at the first non-letter.
            if gap[-1:].isalpha() and part[:1].isalpha():
                gap += " "
            # The same hazard on the OTHER side, and it is the common one.
            # This test asked whether the GAP ends in a letter, which catches
            # `\quad` + `b`; it never asked whether the PREVIOUS PART does.
            # A measured gap of zero -- which 694 renders as "" so that `$ab$`
            # stays `$ab$` -- then glues `\mid` to the letter after it:
            # `|x - y|` came out as `\midx - y\mid`, an undefined command.
            # Measured: 66 occurrences across 19 of 30 documents, `\right`,
            # `\mid`, `\in`, `\ell` and `\langle` among them.
            elif not gap and part[:1].isalpha() and _ends_in_command(
                    pieces[-1] if pieces else ""):
                gap = " "
            pieces.append(gap)
        pieces.append(part)
    return "".join(pieces)


_COMMAND_TAIL = re.compile(r"\\[a-zA-Z]+$")


def _ends_in_command(text: str) -> bool:
    """Does this piece end with a multi-letter control word?

    TeX ends a control word at the first NON-LETTER, so a letter placed
    straight after one extends its name instead of following it.
    """
    return bool(_COMMAND_TAIL.search(text))


def span_to_tex(span) -> str | None:
    """Entry point for a docmodel Span. None means: defer, keep the blobs."""
    if span.kind != "math" or not span.glyphs:
        return None
    body = to_tex(span.glyphs, span.rules)
    if body is None:
        return None

    # A span made up ENTIRELY of raised, script-size glyphs is a marker group
    # whose base is the word before it -- `Montaño` with `a,*` above the line.
    # Judged inside the span the glyphs all look full size, because they are
    # full size relative to EACH OTHER; the raise is only visible against the
    # LINE. LaTeX spells a script with no base as an empty group.
    size = getattr(span, "line_size", 0.0) or 0.0
    if size > 0 and len(span.glyphs) <= 4:
        base = min(g.baseline for g in span.glyphs)
        if (all(g.size <= 0.85 * size for g in span.glyphs)
                and base >= span.rect[1] - 0.01
                and all(g.baseline >= base - 0.01 for g in span.glyphs)):
            return "{}^{" + body + "}"
    return body
