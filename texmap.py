"""texmap — project a (font family, glyph name) onto a LaTeX token.

Built from the measured inventory of four born-digital maths documents:
280 distinct (family, glyphname) pairs over 64,094 maths glyph instances.
Nothing here is invented from TeX folklore that the corpus did not contain.

WHY THE FAMILY IS PART OF THE KEY
    The glyph name alone is not the identity. `C` is `C` in CMMI, `\\mathbb{C}`
    in MSBM, `\\mathcal{C}` in RSFS and `\\mathfrak{C}` in EUFM — same name,
    four different LaTeX tokens. The embedded font's family is what selects
    the alphabet, which is why the name channel had to carry the font too.

WHAT A TOKEN IS NOT
    Three kinds of maths cannot be projected from a glyph at all, and the
    table says so rather than guessing:

    FRAGMENT   an extensible delimiter is drawn as several glyphs stacked
               (`bracketlefttp` / `bracketleftex` / `bracketleftbt`). No one
               of them is a LaTeX token; the group is one `\\left[`. These
               must be merged by geometry before projection.
    RULE       `\\frac`, `\\sqrt`, `\\overline` have NO glyph for the bar.
               It is a filled rule, which pdfminer reports as an LTLine of
               zero height. `radicalbig` is only the radical SIGN; its
               vinculum is a separate rule. Recovering these needs the rule
               rectangles, not the glyph stream.
    UNKNOWN    not in the table. The caller must keep the blob/outline and
               defer, never emit a plausible-looking guess.

CONFIDENCE
    "corpus"    the mapping is standard and the glyph occurs in the corpus
    "unverified" plausible but NOT checked against a font's own encoding
                 table; treated as unknown by `project` unless explicitly
                 allowed, so it can never silently enter output.
"""
from __future__ import annotations

import re
from typing import NamedTuple


# TeX/Type1 maths font families. The family says which ALPHABET a glyph name
# is to be read in: `a` in CMMI is a maths variable, `a` in CMR is a letter.
FAMILY_RULES = [
    (re.compile(r"CM(MI|MIB)\d*", re.I), "math-italic"),      # Computer Modern math italic
    (re.compile(r"CM(SY|BSY)\d*", re.I), "math-symbol"),      # CM symbols
    (re.compile(r"CMEX\d*", re.I), "math-extension"),         # CM extension (big ops/delims)
    (re.compile(r"MSAM\d*|MSBM\d*", re.I), "ams-symbol"),     # AMS symbols A/B
    (re.compile(r"EUFM\d*|EUFB\d*", re.I), "fraktur"),
    (re.compile(r"EUSM\d*|EUSB\d*", re.I), "script"),
    (re.compile(r"MTMIB|MTMI", re.I), "math-italic"),         # MathTime italic
    (re.compile(r"MTSY|MTSYN|MTSYB", re.I), "math-symbol"),   # MathTime symbols
    (re.compile(r"BLEX|BLSY|MTEX", re.I), "math-extension"),  # Belleek/MathTime ext
    (re.compile(r"RSFS\d*", re.I), "script"),
    # LATIN MODERN: Computer Modern's successor, and what every
    # lualatex/xelatex document ships. Its maths fonts were falling through
    # to "text", so none of their glyph names were projected -- measured, 81
    # of the small crops in one corpus pass were LM `prime`, `element`,
    # `lscript` and `asteriskmath`, every one of which the table already
    # knows under its Computer Modern name.
    # MnSymbol: a complete maths family (MnSymbol5..7 are its optical
    # sizes). Unknown here, it fell through to "text" and nothing projected
    # -- measured, 3721 failures on one 476-page paper, 3239 of them the
    # single glyph `minute`, which RENDERS as a prime (`f'`).
    # `TeX-matha`/`TeX-mathx` are the AMS symbol and extension fonts under a
    # distiller's own naming. Classified as TEXT they emitted their
    # StandardEncoding fallback characters literally -- `\pi^{*}p\omega q`
    # for `\pi^{*}(\omega_{0})`.
    (re.compile(r"TeX-math[ab]\d*", re.I), "math-symbol"),
    (re.compile(r"TeX-mathx\d*", re.I), "math-extension"),
    (re.compile(r"TeX-mathit\d*", re.I), "math-italic"),
    (re.compile(r"MnSymbol\w*\d*", re.I), "math-symbol"),
    (re.compile(r"LMMathItalic\d*", re.I), "math-italic"),
    (re.compile(r"LMMathSymbols\d*", re.I), "math-symbol"),
    (re.compile(r"LMMathExtension\d*", re.I), "math-extension"),
    (re.compile(r"LMSans(Quotation|Demi)?\d*|LMRoman\w*\d*", re.I),
     "text-cm"),
    (re.compile(r"CMR\d*|CMBX\d*|CMTI\d*|CMSL\d*|CMTT\d*", re.I), "text-cm"),
]


# Italic in TeX font names is `TI` (text italic), `MI` (math italic) or `SL`
# (slanted) -- never the substring "it". Testing `endswith("it")` matched
# nothing: `CMTI10` ends in "ti". The error made every Computer Modern italic
# look upright, so variables were treated as operator names.
_ITALIC_NAME = re.compile(
    r"italic|oblique|"
    r"\bcm(ti|mi|sl|bxti|bxsl|mib|itt)\d*|"
    r"-(it|ital|italic|oblique)\b",
    re.I,
)


# Fonts whose glyphs are DRAWING, not text. Xy-pic builds a commutative
# diagram by stamping a dash glyph repeatedly along a line and capping it with
# an arrow-tip glyph; LaTeX's picture environment does the same with line10 and
# lasy. There are no path operators in the PDF at all -- the "strokes" are
# glyphs -- so a text pipeline scatters them through the prose as `****\ */`.
_DRAWING_NAME = re.compile(
    r"^xy(dash|atip|btip|mark|circ|dash|line|bsql|rown)|^xy[a-z]*-|"
    r"^(line|linew|lcircle|lcirclew)\d*$|"
    r"^lasy[b]?\d*$|"
    r"^(msam|msbm)$",          # bare, without a size: rare drawing variants
    re.I,
)


# TeX sets UPPERCASE Greek in the roman text font, not the maths font: in OT1
# `\Gamma` is cmr slot 0x00. So a `Gamma` glyph arrives from CMR12 with family
# "text-cm", and a classifier that goes by font alone calls it prose. It is
# mathematics -- the font is an encoding accident of OT1.
_UPPER_GREEK = {
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon",
    "Phi", "Psi", "Omega",
}


def greek_latex(glyphname: str | None) -> str | None:
    """The LaTeX command for a Greek letter glyph name, whatever font it
    was set in. Returns None for anything else."""
    if not glyphname:
        return None
    if glyphname in _UPPER_GREEK:
        return "\\" + glyphname
    if glyphname in _GREEK:
        return "\\" + glyphname
    return _VARIANTS.get(glyphname)


def is_drawing(fontname: str) -> bool:
    """True if this font draws diagram parts rather than setting text."""
    return bool(_DRAWING_NAME.search(fontname.split("+")[-1]))


# Typewriter faces. A line set in one is VERBATIM: its `<` and `>` come from
# the maths font because OT1 has no angle brackets, and projecting them as
# mathematics turns `<omtext xml:id="foo">` into `$<$ omtext xml:id="foo" $>$`.
_MONO_NAME = re.compile(
    r"\bcm(tt|sltt|itt|tex|vtt)\d*|"
    r"courier|consol|monaco|menlo|inconsolata|nimbusmon|lmmono|"
    r"mono(space)?\d*$|mono[a-z]*\d*$|-tt\b",
    re.I,
)


# Fonts MEASURED to be monospace in the document being read. Names carry no
# reliable signal: `lmt-regular` is Latin Modern Typewriter and `t1-uni-regular`
# is proportional, and nothing in either name says so.
_MEASURED_MONO: set[str] = set()


def measure_monospace(samples) -> set[str]:
    """Which fonts are monospace, from their ADVANCE WIDTHS.

    A monospace face advances by the same amount for every glyph. Measured on
    one journal page:

        lmt-regular         91% of glyphs share one advance ratio -> monospace
        t1-uni-regular      25% -- proportional, despite setting the listings
        CharisSIL           12% -- ordinary body text
        LMRoman10-Regular  100% -- but all 517 glyphs are SPACES

    That last row is why diversity is required as well -- and it has to be
    LETTERS, not characters. Measured across two documents:

        lmt-regular   21 distinct, 20 letters, 0.920 uniform -> monospace
        CMR8          17 distinct,  4 letters, 0.930 uniform -> digits and
                      symbols, uniform by accident

    Uniformity alone cannot tell those apart; the alphabet can.

    `samples` yields (fontname, advance, size, text).
    """
    ratios: dict[str, list[float]] = {}
    chars: dict[str, set[str]] = {}
    for name, adv, size, text in samples:
        if size <= 0 or not text.strip():
            continue
        key = name.split("+")[-1]
        ratios.setdefault(key, []).append(round(adv / size, 3))
        chars.setdefault(key, set()).add(text)
    out: set[str] = set()
    for key, vals in ratios.items():
        letters = {c for c in chars.get(key, ()) if c.isalpha()}
        if len(vals) < 100 or len(letters) < 10:
            continue
        top = max(set(vals), key=vals.count)
        if vals.count(top) >= 0.9 * len(vals):
            out.add(key)
    return out


def set_measured_monospace(names) -> None:
    """Install the measured set for the document being read."""
    _MEASURED_MONO.clear()
    _MEASURED_MONO.update(names)


def is_monospace(fontname: str) -> bool:
    """True if this font is a typewriter face.

    Measurement first, name second: the name is a hint that happens to be
    right for TeX's own families and wrong for anything subset by a
    publisher's toolchain.
    """
    base = fontname.split("+")[-1]
    if base in _MEASURED_MONO:
        return True
    return bool(_MONO_NAME.search(base))


def is_italic(fontname: str) -> bool:
    """True if this font is an italic or slanted face."""
    return bool(_ITALIC_NAME.search(fontname.split("+")[-1]))


def family_of(fontname: str) -> str:
    base = fontname.split("+")[-1]
    for rx, label in FAMILY_RULES:
        if rx.search(base):
            return label
    return "text"

class TexToken(NamedTuple):
    latex: str | None   # None when nothing can be emitted for this glyph alone
    kind: str           # atom | delimiter | bigop | accent | fragment | unknown
    package: str | None  # LaTeX package required, if any
    confidence: str     # corpus | unverified | none


UNKNOWN = TexToken(None, "unknown", None, "none")

# --- Greek and named letters shared by the maths alphabets -----------------
_GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
    "upsilon", "phi", "chi", "psi", "omega",
}
# TeX's "variant" glyph names, where the name carries the \var prefix
_VARIANTS = {
    "epsilon1": r"\varepsilon",
    "theta1": r"\vartheta",
    "rho1": r"\varrho",
    "phi1": r"\varphi",
    "sigma1": r"\varsigma",
}

# --- math-italic (CMMI / MTMI): variables, Greek, a few named symbols ------
_MATH_ITALIC = {
    "lscript": r"\ell", "star": r"\star", "partialdiff": r"\partial",
    # cmmi10 carries the musical symbols at 0x5B-0x5D
    "flat": r"\flat", "natural": r"\natural", "sharp": r"\sharp",
    "weierstrass": r"\wp", "arrowrightleft": r"\rightleftharpoons",
    "vector": r"\vec", "period": ".", "comma": ",", "slash": "/",
    "less": "<", "greater": ">", "parenleft": "(", "parenright": ")",
}

# --- math-symbol (CMSY / MTSYN) -------------------------------------------
_MATH_SYMBOL = {
    "minus": "-", "plus": "+", "equal": "=", "colon": ":", "semicolon": ";",
    "exclam": "!", "bar": r"\mid", "bardbl": r"\|",
    "asteriskmath": r"\ast", "periodcentered": r"\cdot", "multiply": r"\times",
    "plusminus": r"\pm", "minusplus": r"\mp", "prime": r"\prime",
    "logicaland": r"\wedge", "logicalor": r"\vee",
    "intersection": r"\cap", "union": r"\cup",
    "element": r"\in", "owner": r"\ni", "emptyset": r"\emptyset",
    "propersubset": r"\subset", "propersuperset": r"\supset",
    "reflexsubset": r"\subseteq",
    "lessequal": r"\leq", "greaterequal": r"\geq",
    "lessmuch": r"\ll", "greatermuch": r"\gg",
    "similar": r"\sim", "similarequal": r"\simeq", "approxequal": r"\approx",
    "equivalence": r"\equiv", "proportional": r"\propto",
    "precedes": r"\prec", "perpendicular": r"\perp",
    "infinity": r"\infty", "nabla": r"\nabla", "radical": r"\surd",
    # cmsy10 slot 0x3E. In a linear-algebra text this is the TRANSPOSE,
    # `A^{\top}`: 3738 occurrences in one 1962-page book.
    "latticetop": r"\top",
    # cmsy10 slot 0x0E: function composition, `f \circ g`. Confirmed against a
    # rendered page showing `f o i` where the source has a composition.
    "openbullet": r"\circ",
    "follows": r"\succ", "followsequal": r"\succeq",
    "precedesequal": r"\preceq",
    "heart": r"\heartsuit", "spade": r"\spadesuit", "club": r"\clubsuit",
    "diamondsuit": r"\diamondsuit",
    "existential": r"\exists", "universal": r"\forall",
    "flat": r"\flat", "sharp": r"\sharp", "natural": r"\natural",
    "triangleleft": r"\triangleleft", "triangleright": r"\triangleright",
    "backslash": r"\backslash", "aleph": r"\aleph", "wreathproduct": r"\wr",
    "triangle": r"\triangle", "diamond": r"\diamond",
    "diamondmath": r"\diamond", "bullet": r"\bullet",
    "circlemultiply": r"\otimes", "circleplus": r"\oplus",
    "circledot": r"\odot", "dagger": r"\dagger",
    "arrowright": r"\rightarrow", "arrowleft": r"\leftarrow",
    "arrowup": r"\uparrow", "arrowdown": r"\downarrow",
    "arrowboth": r"\leftrightarrow", "arrowbothv": r"\updownarrow",
    "arrowdblright": r"\Rightarrow", "arrowdblleft": r"\Leftarrow",
    "arrowdblboth": r"\Leftrightarrow",
    "arrownortheast": r"\nearrow", "arrownorthwest": r"\nwarrow",
    "arrowsoutheast": r"\searrow", "arrowsouthwest": r"\swarrow",
    "turnstileright": r"\vdash",
    "Ifractur": r"\Im", "Rfractur": r"\Re",
    "angbracketleft": r"\langle", "angbracketright": r"\rangle",
    "braceleft": r"\{", "braceright": r"\}",
    "bracketleft": "[", "bracketright": "]",
    "floorright": r"\rfloor", "floorleft": r"\lfloor",
    "ceilingright": r"\rceil", "ceilingleft": r"\lceil",
    "mapsto": r"\mapsto", "arrowhookright": r"\hookrightarrow",
    "arrowhookleft": r"\hookleftarrow",
    "reflexsuperset": r"\supseteq", "section": r"\S",
    "dagger": r"\dagger", "daggerdbl": r"\ddagger",
    # accents, which apply to a following/covered argument
    "circumflex": r"\hat", "tilde": r"\tilde", "bar_accent": r"\bar",
    "caron": r"\check", "macron": r"\bar", "dieresis": r"\ddot",
    # Measured on the PDF2LaTeX dataset: `ring` alone was 30 of 448 crops,
    # mapped for a TEXT family and not for a maths one, though a maths font
    # supplies it just as readily.
    "ring": r"\mathring", "breve": r"\breve", "acute": r"\acute",
    "grave": r"\grave", "dotaccent": r"\dot",
}
_SYMBOL_ACCENTS = {"circumflex", "tilde", "caron", "macron", "dieresis",
                   "ring", "breve", "acute", "grave", "dotaccent"}
# Occurs in the corpus but NOT verified against a font encoding table.
# `circlecopyrt` was refused as unverified. RENDERED at 600dpi from a journal
# front page it is unmistakable: the ring of a copyright sign, with a
# separate `c` from the TEXT font drawn INSIDE it --
#
#     circlecopyrt  CMSY7  x=[177.6, 185.6]
#     c             CMR7   x=[179.8, 183.4]
#
# so `\copyright` in Computer Modern is a composition of two glyphs, not one,
# and refusing the ring dropped the symbol entirely: `2025. (cid:2)c The
# Author(s)`.
_SYMBOL_UNVERIFIED: set = set()

# --- ams-symbol (MSAM / MSBM) ---------------------------------------------
# Ordinary punctuation and operators as a font names them. Needed because a
# fraktur or script font emits its own brackets: measured, `parenleft`,
# `parenright` and `equal` from EUFM10 were 984 of one paper's failures.
# Text-font glyphs a maths formula uses. `dotlessi` is not a typographic
# curiosity: `\hat{\imath}` is set as a CIRCUMFLEX over a DOTLESS I, two
# glyphs, and both were unmapped -- 64 and 27 of one paper's failures. The
# `ff` ligature is one glyph for two letters.
_TEXT_MATHS = {
    "dotlessi": r"\imath", "dotlessj": r"\jmath",
    "ff": "ff", "fi": "fi", "fl": "fl", "ffi": "ffi", "ffl": "ffl",
    "quoteright": "'", "quoteleft": "`", "emdash": "---", "endash": "--",
    "hyphen": "-", "germandbls": "ss",
}

_TEXT_PUNCT = {
    "parenleft": "(", "parenright": ")",
    "bracketleft": "[", "bracketright": "]",
    "braceleft": r"\{", "braceright": r"\}",
    "equal": "=", "plus": "+", "minus": "-", "slash": "/",
    "backslash": r"\backslash", "bar": r"\mid", "colon": ":",
    "semicolon": ";", "comma": ",", "period": ".", "exclam": "!",
    "question": "?", "at": "@", "percent": r"\%", "numbersign": r"\#",
    "dollar": r"\$", "ampersand": r"\&", "underscore": r"\_",
    "asterisk": "*", "less": "<", "greater": ">",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

_AMS = {
    # MnSymbol's name for the prime, verified by rendering `f'` at 500dpi
    # from a 476-page paper where it failed 3239 times.
    "minute": r"\prime", "second": r"\prime\prime",
    "blackcircle": r"\bullet", "ratio": ":", "whitecircle": r"\circ",
    "notequal": r"\neq", "notelement": r"\notin",
    "harpoonupright": r"\upharpoonright", "harpoonupleft": r"\upharpoonleft",
    "harpoondownright": r"\downharpoonright",
    "harpoondownleft": r"\downharpoonleft",
    "arrowparrrightleft": r"\rightleftarrows",
    "arrowparrleftright": r"\leftrightarrows",
    "whitediamond": r"\diamond", "blackdiamond": r"\blacklozenge",
    "whitesquare": r"\square", "blacktriangle": r"\blacktriangle",
    "whitetriangle": r"\triangle", "whitestar": r"\star",
    "third": r"\prime\prime\prime",
    # Found by a corpus pass over small crops: the commonest AMS glyph names
    # the table did not carry. `defines` was identified by RENDERING it at
    # 500dpi out of a real page -- it is a triangle over an equals sign,
    # `\triangleq` -- rather than inferred from the name.
    "defines": r"\triangleq",
    "lessorsimilar": r"\lesssim", "greaterorsimilar": r"\gtrsim",
    "lessorequalslant": r"\leqslant", "greaterorequalslant": r"\geqslant",
    "squaresolid": r"\blacksquare", "squareimage": r"\sqsubset",
    "squareoriginal": r"\sqsupset",
    "subsetsqequal": r"\sqsubseteq", "supersetsqequal": r"\sqsupseteq",
    "trianglesolid": r"\blacktriangle", "circlesolid": r"\bullet",
    "lessmuch": r"\lll", "greatermuch": r"\ggg",
    "curlyless": r"\prec", "curlygreater": r"\succ",
    "planckover2pi": r"\hbar", "planckover2pi1": r"\hbar",
    "square": r"\square", "angle": r"\angle",
    "emptyset": r"\varnothing", "approxequal": r"\approx",
    "circleasterisk": r"\circledast",
    # MSAM slanted order relations
    "lessorequalslant": r"\leqslant", "greaterorequalslant": r"\geqslant", "triangleleft": r"\vartriangleleft",
    "triangleright": r"\vartriangleright",
}
# `notbar` is NOT an overlay: measured with real width and a gap on both sides,
# so it is a standalone binary relation (very likely "does not divide"). Which
# relation exactly is not verified against the font's encoding, so it abstains.
_AMS_UNVERIFIED = {"anticlockwise", "epsiloninv", "triangleinv", "notbar",
                   "upslope", "downslope",
                   # MSAM corner marks: used for angle marks and end-of-proof
                   # boxes; which command they are is not verified.
                   "rightanglese", "rightanglesw", "rightanglene",
                   "rightanglenw"}

# --- math-extension (CMEX / BLEX) -----------------------------------------
# The large operators of cmex10 ("largesymbols"). Each exists in two sizes and
# the glyph name carries which: `circleplusdisplay` and `circleplustext` are
# both `\bigoplus`, set larger in display style. Keying on the BASE name and
# stripping the size suffix covers the whole family at once -- enumerating
# them one at a time is how `circleplusdisplay` came to be missing while
# `circlemultiplydisplay` was present.
#
# Cross-checked against fontmath.ltx, which declares them by slot:
#   \bigodot "4A   \bigoplus "4C   \bigotimes "4E
#   \sum "50  \prod "51  \intop "52  \bigcup "53  \bigcap "54
#   \biguplus "55  \bigwedge "56  \bigvee "57  \coprod "60
_BIGOP_BASE = {
    "summation": r"\sum",
    "product": r"\prod",
    "coproduct": r"\coprod",
    "integral": r"\int",
    "contintegral": r"\oint",
    "union": r"\bigcup",
    "intersection": r"\bigcap",
    "unionmulti": r"\biguplus",
    "unionsq": r"\bigsqcup",
    "logicaland": r"\bigwedge",
    "logicalor": r"\bigvee",
    "circleplus": r"\bigoplus",
    "circlemultiply": r"\bigotimes",
    "circledot": r"\bigodot",
}


def _bigop(name: str) -> tuple[str, str] | None:
    """(latex, style) for a large-operator glyph name, or None."""
    for suffix, style in (("display", "display"), ("text", "text")):
        if name.endswith(suffix):
            base = _BIGOP_BASE.get(name[: -len(suffix)])
            if base:
                return base, style
    return None
_DELIM_SHAPE = {
    "paren": ("(", ")"), "bracket": ("[", "]"),
    "floor": (r"\lfloor", r"\rfloor"), "ceiling": (r"\lceil", r"\rceil"),
    "brace": (r"\{", r"\}"), "angbracket": (r"\langle", r"\rangle"),
    "slash": ("/", "/"),
}
_SIZE_CMD = {"big": r"\big", "Big": r"\Big", "bigg": r"\bigg", "Bigg": r"\Bigg"}
# Pieces of a built-up delimiter: never a token on their own.
_FRAGMENT_SUFFIX = ("tp", "bt", "ex", "mid")
# cmex10 carries each wide accent in three sizes. Only the base `hatwide` was
# listed, so `hatwider` and `hatwidest` -- 10 occurrences in one book -- fell
# through as unknown and deferred whole spans.
_WIDE_ACCENT = {
    "tildewide": r"\widetilde", "tildewider": r"\widetilde",
    "tildewidest": r"\widetilde",
    "hatwide": r"\widehat", "hatwider": r"\widehat",
    "hatwidest": r"\widehat",
}


def _math_italic(name: str) -> TexToken:
    # The dotless letters live in the MATHS italic font, not the text one:
    # `\hat{\imath}` is a circumflex over CMMI's `dotlessi`. 85 failures on
    # one paper were these two glyphs.
    if name in _TEXT_MATHS:
        return TexToken(_TEXT_MATHS[name], "atom", None, "corpus")
    if len(name) == 1 and name.isalpha():
        return TexToken(name, "atom", None, "corpus")
    if name in _VARIANTS:
        return TexToken(_VARIANTS[name], "atom", None, "corpus")
    if name in _GREEK:
        cmd = "\\" + name
        return TexToken(cmd, "atom", None, "corpus")
    if name.lower() in _GREEK and name[0].isupper():
        return TexToken("\\" + name, "atom", None, "corpus")
    if name in _MATH_ITALIC:
        latex = _MATH_ITALIC[name]
        kind = "accent" if name == "vector" else "atom"
        return TexToken(latex, kind, None, "corpus")
    return UNKNOWN


# `negationslash` has ZERO ADVANCE WIDTH: its ink extends outside its box and
# lies over a neighbour, so the box says nothing about which symbol it negates.
# Measured on a Computer Modern number-theory book, it appears immediately
# after an `equal` in some places and immediately before one in others, so the
# attachment side is not decidable from geometry alone here. `\not` PRECEDES
# its symbol in LaTeX, so guessing wrong turns `\neq` into `=\not` -- valid
# LaTeX, different mathematics. It is therefore a kind of its own with NO
# latex: a span containing one defers until composition is implemented.
_OVERLAY = {"negationslash": None, "circlecopyrt": None}

# An overlay composed with what it ENCLOSES or CROSSES. The key is the
# overlay's glyph name and the inner glyph's text.
OVERLAY_PAIRS = {
    ("circlecopyrt", "c"): "©",
    ("circlecopyrt", "R"): "®",
    ("circlecopyrt", "a"): "@",
}


_VARIANT_SUFFIX = re.compile(r"\.(alt\d*|var\d*|sc|ss\d*|oldstyle|fitted)$")


def _math_symbol(name: str) -> TexToken:
    # A font names a second design of the same character with a suffix:
    # `parenright.alt1` beside `parenright`, `uni2032.var` beside `uni2032`.
    # The suffix is a DESIGN choice, not a different character, and 1464
    # brackets on one paper were refused for carrying one.
    name = _VARIANT_SUFFIX.sub("", name)
    if name in _SYMBOL_UNVERIFIED:
        return TexToken(None, "unknown", None, "unverified")
    if name in _OVERLAY:
        return TexToken(None, "overlay", None, "corpus")
    if len(name) == 1 and name.isalpha() and name.isupper():
        # In CMSY the Latin capitals ARE the calligraphic alphabet.
        return TexToken(rf"\mathcal{{{name}}}", "atom", None, "corpus")
    if name in _SYMBOL_ACCENTS:
        return TexToken(_MATH_SYMBOL[name], "accent", None, "corpus")
    if name in _MATH_SYMBOL:
        return TexToken(_MATH_SYMBOL[name], "atom", None, "corpus")
    # A vendor's symbol font carries AMS names too: MathTime's MTSYN holds
    # `subsetsqequal` and `macron` alongside the CMSY set, and refusing them
    # here sent a symbol the table already knows to a crop.
    if name in _AMS:
        return TexToken(_AMS[name], "atom", None, "corpus")
    # A maths font supplies its own BRACKETS and operators: measured, 12333
    # parentheses from MnSymbol10 on one paper, refused by a reader that had
    # them in the plain table.
    if name in _TEXT_PUNCT:
        return TexToken(_TEXT_PUNCT[name], "atom", None, "corpus")
    return UNKNOWN


_TEXT_ACCENT = {
    "circumflex": r"\hat", "caron": r"\check", "tilde": r"\tilde",
    "acute": r"\acute", "grave": r"\grave", "breve": r"\breve",
    "dieresis": r"\ddot", "dotaccent": r"\dot", "ring": r"\mathring",
    "macron": r"\bar",
}


def _text_glyph(name: str) -> TexToken:
    """A text font's contribution to a formula.

    A text face supplies the ACCENT and the dotless letter that a composed
    maths accent is built from, and its ligatures stand for two letters.
    """
    if name in _TEXT_ACCENT:
        return TexToken(_TEXT_ACCENT[name], "accent", None, "corpus")
    if name in _TEXT_MATHS:
        return TexToken(_TEXT_MATHS[name], "atom", None, "corpus")
    if name in _TEXT_PUNCT:
        return TexToken(_TEXT_PUNCT[name], "atom", None, "corpus")
    return UNKNOWN


def _math_extension(name: str) -> TexToken:
    big = _bigop(name)
    if big:
        return TexToken(big[0], "bigop", None, "corpus")
    if name in _WIDE_ACCENT:
        return TexToken(_WIDE_ACCENT[name], "accent", None, "corpus")
    if name.startswith("radical"):
        # The radical SIGN only. Its vinculum is a rule, so the extent of the
        # argument is not knowable from this glyph.
        return TexToken(r"\sqrt", "rule", None, "corpus")
    if name in ("vextendsingle", "vextenddouble", "arrowvertex",
                "arrowbt", "arrowtp"):
        # pieces of a built-up extensible arrow or bar: one LaTeX token is
        # spread over several glyphs, so none of them projects alone
        return TexToken(None, "fragment", None, "corpus")
    if name.startswith("bracehtip"):
        return TexToken(None, "fragment", None, "corpus")
    for shape, (opener, closer) in _DELIM_SHAPE.items():
        if not name.startswith(shape):
            continue
        rest = name[len(shape):]
        for side, glyph in (("left", opener), ("right", closer)):
            if not rest.startswith(side):
                # `braceex` and `slashbig` carry no side
                continue
            suffix = rest[len(side):]
            if suffix in _FRAGMENT_SUFFIX:
                return TexToken(None, "fragment", None, "corpus")
            if suffix in _SIZE_CMD:
                cmd = _SIZE_CMD[suffix] + ("l" if side == "left" else "r")
                return TexToken(cmd + glyph, "delimiter", None, "corpus")
        # A sided-less delimiter such as `slashbig`: `/` is an ordinary
        # symbol, not a fence, so it takes the plain size command with no
        # l/r variant. `\bigl/` would be a syntax error.
        if rest in _SIZE_CMD:
            return TexToken(_SIZE_CMD[rest] + opener, "delimiter", None, "corpus")
        if rest in _FRAGMENT_SUFFIX or rest == "ex":
            return TexToken(None, "fragment", None, "corpus")
    return UNKNOWN


def project(family: str, glyphname: str | None) -> TexToken:
    """Map one glyph identity to a LaTeX token.

    Returns UNKNOWN rather than a guess whenever the identity is not stated,
    not in the table, or not verified. A caller must treat `latex is None` as
    "keep the blob" — never as "emit nothing and move on".
    """
    if not glyphname:
        return UNKNOWN
    if family == "math-italic":
        return _math_italic(glyphname)
    if family == "math-symbol":
        return _math_symbol(glyphname)
    if family == "math-extension":
        return _math_extension(glyphname)
    if family in ("text", "text-cm"):
        return _text_glyph(glyphname)
    if family == "ams-symbol":
        # A wide accent is an accent whatever font it was found in: some
        # documents ship `hatwide` in a family this classifier reads as AMS.
        if glyphname in _WIDE_ACCENT:
            return TexToken(_WIDE_ACCENT[glyphname], "accent", None, "corpus")
        if glyphname in _AMS_UNVERIFIED:
            return TexToken(None, "unknown", None, "unverified")
        if glyphname in _AMS:
            return TexToken(_AMS[glyphname], "atom", "amssymb", "corpus")
        if len(glyphname) == 1 and glyphname.isupper():
            return TexToken(rf"\mathbb{{{glyphname}}}", "atom", "amssymb", "corpus")
        return UNKNOWN
    # A fraktur or script font carries ORDINARY PUNCTUATION too: a formula
    # set in Euler emits its parentheses, its `=` and its `-` from EUFM10,
    # not from the maths font beside it. Refusing those deferred the whole
    # formula -- measured, 1200 failures on one paper, every one a bracket
    # or an operator the plain table already knows.
    if family in ("script", "fraktur"):
        if len(glyphname) == 1 and glyphname.isalpha():
            wrap = r"\mathcal" if family == "script" else r"\mathfrak"
            pkg = None if family == "script" else "amsfonts"
            return TexToken(rf"{wrap}{{{glyphname}}}", "atom", pkg, "corpus")
        if glyphname in _MATH_SYMBOL:
            return TexToken(_MATH_SYMBOL[glyphname], "atom", None, "corpus")
        if glyphname in _TEXT_PUNCT:
            return TexToken(_TEXT_PUNCT[glyphname], "atom", None, "corpus")
        return UNKNOWN
    return UNKNOWN

# --- Unicode-named glyphs -------------------------------------------------
#
# A publisher's maths font names its glyphs by CODEPOINT, not by the
# PostScript names TeX uses: STIXMath emits `u1D460`, `u1D6FF`, `uni2032`
# where Computer Modern emits `s`, `delta`, `prime`. Measured on three
# journal papers, those names were the single largest cause of deferral --
# 48 glyphs of one document's mathematics with nothing wrong with them
# except that the table did not speak their dialect.
#
# The Mathematical Alphanumeric Symbols block (U+1D400..U+1D7FF) encodes a
# LETTER together with a STYLE, so it decomposes: U+1D460 is an italic `s`,
# which is what a maths italic already means, and U+1D6FF is an italic
# `delta` -- i.e. `\delta`.

_UNI_NAME = re.compile(r"^u(?:ni)?([0-9A-Fa-f]{4,6})(?:\.\w+)?$")

# base codepoint -> (style, offset) for the alphanumeric block
_MATH_ALPHA = [
    (0x1D400, "bold", "A"), (0x1D434, "italic", "A"),
    (0x1D468, "bolditalic", "A"), (0x1D49C, "script", "A"),
    (0x1D504, "fraktur", "A"), (0x1D538, "double", "A"),
    (0x1D5A0, "sans", "A"), (0x1D670, "mono", "A"),
]

_GREEK_ITALIC = (0x1D6FC, 0x1D6FD, 0x1D6FE, 0x1D6FF)   # alpha..delta run


def codepoint_of(glyphname: str) -> int | None:
    """The Unicode codepoint a glyph name encodes, or None.

    Accepts `uXXXX`, `uniXXXX` and a variant suffix (`uni2032.var`), which is
    how a font distinguishes two designs of the same character.
    """
    m = _UNI_NAME.match(glyphname or "")
    return int(m.group(1), 16) if m else None


def _alphanumeric(cp: int) -> str | None:
    """LaTeX for a Mathematical Alphanumeric Symbols codepoint."""
    # Greek italic run: U+1D6FC is alpha, and the Greek names follow in order
    if 0x1D6FC <= cp <= 0x1D71B:
        greek = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
                 "theta", "iota", "kappa", "lambda", "mu", "nu", "xi",
                 "omicron", "pi", "rho", "varsigma", "sigma", "tau",
                 "upsilon", "phi", "chi", "psi", "omega"]
        idx = cp - 0x1D6FC
        if idx < len(greek):
            name = greek[idx]
            return " " if name == "omicron" else "\\" + name
        return None
    if 0x1D6A8 <= cp <= 0x1D6C0:                     # bold capital Greek
        caps = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta",
                "Theta", "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi",
                "Omicron", "Pi", "Rho", "vartheta", "Sigma", "Tau",
                "Upsilon", "Phi", "Chi", "Psi", "Omega"]
        idx = cp - 0x1D6A8
        if idx < len(caps):
            return "\\" + caps[idx]
        return None
    for base, style, first in _MATH_ALPHA:
        if base <= cp < base + 52:
            off = cp - base
            ch = chr(ord("A") + off) if off < 26 else chr(ord("a") + off - 26)
            if style == "italic":
                return ch                     # maths italic is the default
            if style == "bold":
                return rf"\mathbf{{{ch}}}"
            if style == "bolditalic":
                return rf"\boldsymbol{{{ch}}}"
            if style == "script":
                return rf"\mathcal{{{ch}}}"
            if style == "fraktur":
                return rf"\mathfrak{{{ch}}}"
            if style == "double":
                return rf"\mathbb{{{ch}}}"
            if style == "sans":
                return rf"\mathsf{{{ch}}}"
            if style == "mono":
                return rf"\mathtt{{{ch}}}"
    return None


# Codepoints outside the alphanumeric block that a maths font emits by name.
_UNI_LATEX = {
    0x2032: "\\prime", 0x2033: "\\prime\\prime", 0x2034: "\\prime\\prime\\prime",
    0x2192: "\\rightarrow", 0x2190: "\\leftarrow", 0x2194: "\\leftrightarrow",
    0x21D2: "\\Rightarrow", 0x21D0: "\\Leftarrow", 0x21D4: "\\Leftrightarrow",
    0x2208: "\\in", 0x2209: "\\notin", 0x2211: "\\sum", 0x220F: "\\prod",
    0x221A: "\\sqrt", 0x221E: "\\infty", 0x2229: "\\cap", 0x222A: "\\cup",
    0x222B: "\\int", 0x2248: "\\approx", 0x2260: "\\neq", 0x2261: "\\equiv",
    0x2264: "\\leq", 0x2265: "\\geq", 0x2282: "\\subset", 0x2286: "\\subseteq",
    0x2287: "\\supseteq", 0x2283: "\\supset", 0x22A2: "\\vdash",
    0x22A8: "\\models", 0x2205: "\\emptyset", 0x2200: "\\forall",
    0x2203: "\\exists", 0x00D7: "\\times", 0x00B1: "\\pm", 0x2217: "*",
    0x2218: "\\circ", 0x2219: "\\cdot", 0x22C5: "\\cdot", 0x2026: "\\ldots",
    0x22EF: "\\cdots", 0x2113: "\\ell", 0x2202: "\\partial", 0x2207: "\\nabla",
    0x00AC: "\\neg", 0x2227: "\\wedge", 0x2228: "\\vee", 0x2295: "\\oplus",
    0x2297: "\\otimes", 0x2212: "-", 0x2032: "\\prime",
    0x2013: "--", 0x2014: "---", 0x00A0: " ", 0x2009: "\\,",
}


# ZapfDingbats names its glyphs `a1`..`a191`, and no table shipped with
# pdfminer or fontTools covers them. Only entries VERIFIED by rendering the
# glyph out of a real document belong here -- an unresolved marker is a
# visible crop, a wrong one is a silent corruption.
_DINGBAT = {
    # Rendered at 400 dpi from a journal title page: a five-pointed outlined
    # star with stress lines, used as the article's footnote marker.
    "a36": "\u2729",
}


def unicode_latex(glyphname: str) -> str | None:
    """LaTeX for a glyph named by its codepoint, or None."""
    if glyphname in _DINGBAT:
        return _DINGBAT[glyphname]
    cp = codepoint_of(glyphname)
    if cp is None:
        return None
    if cp in _UNI_LATEX:
        return _UNI_LATEX[cp]
    got = _alphanumeric(cp)
    if got is not None:
        return got
    ch = chr(cp)
    if ch.isalnum() and cp < 0x2000:
        return ch
    return None


# --- maths spacing --------------------------------------------------------
#
# A LaTeX space emits NO GLYPH. Measured on a table of all thirteen forms
# compiled with pdflatex: every one is a pure advance of the current point,
# and the advances are QUANTISED in em, so the command that produced them is
# recoverable from geometry alone:
#
#     \!        -0.167      \enspace   +0.500
#     (none)     0.000       \quad      +1.000
#     \,        +0.167      \qquad     +2.000
#     \:        +0.222      \          +0.333
#     \;        +0.278      ~          +0.333
#
# 3mu is 1/6 em, 4mu is 2/9, 5mu is 5/18 -- the table is TeX's own, not a
# fitted one. Emitting a single generic space for all of them, which is what
# this projector did, loses `\!` (a NEGATIVE space, deliberate kerning) and
# cannot distinguish `$ab$` from `$a\quad b$`.
#
# `\ `, `~` and an ordinary text space all measure +0.333 and are not
# distinguishable here; the ordinary space is emitted for that band.

# ONLY the gaps TeX's own rules cannot produce.
#
# The first version mapped every band, and measured 48.8 spacing commands
# per 1000 tokens on a real paper -- far more explicit spacing than any
# author writes. The reason is that TeX inserts spacing AUTOMATICALLY by
# atom class: a binary operator takes a medium space either side, a relation
# a thick one. Those gaps measure 0.222 and 0.278 em and are
# indistinguishable from `\:` and `\;` by width alone. Emitting them as
# explicit commands would make a recompile add TeX's spacing again ON TOP.
#
# What survives is what automatic spacing never produces between ordinary
# atoms: a NEGATIVE gap (the author wrote `\!`), and a gap of an em or more
# (`\quad`, `\qquad`). Distinguishing `\,` from a binary operator's own
# medium space needs the atom classes, which this projector does not model.
# Narrowed AGAIN, by a second measurement. `\quad` and `\qquad` fired on
# the gaps an ALIGN environment leaves before its relation --
# `\vartheta^{\alpha}\quad= E_{i}^{\alpha}` -- which the author never
# wrote. Alignment, automatic atom spacing and explicit commands all produce
# the same widths, and width alone cannot separate them.
#
# What is left is what geometry CAN settle: whether the glyphs abut at all,
# and a negative gap, which nothing automatic produces.
_SPACE_BANDS = [
    (-0.08, r"\!"),
    (0.08, ""),           # the glyphs abut: `$ab$`, not `$a b$`
]


def math_space(gap_em: float) -> str:
    """The LaTeX spacing command a measured gap encodes.

    `gap_em` is the gap between two glyphs divided by the type size. Returns
    the empty string when the glyphs abut, so `$ab$` stays `$ab$`.
    """
    for edge, latex in _SPACE_BANDS:
        if gap_em < edge:
            return latex
    return " "


# --- TeX's spacing table, read backwards ----------------------------------
#
# TeX inserts space between two atoms as a function of their CLASSES, from a
# fixed 8x8 table (Knuth; LaTeX Companion p.525; reproduced as Table 1 in
# Baker, Sexton & Sorge, DAS 2010). Four values only:
#
#     0  no space        1  thin   3mu = 1/6 em
#     2  medium 4mu      3  thick  5mu = 5/18 em
#
# Read FORWARDS that is a nuisance: 694 measured that an author's `\:` and a
# binary operator's automatic 4mu are the same width, and concluded the atom
# classes were unrecoverable and the spacing therefore not projectable.
#
# Read BACKWARDS it is a signal. The space that TeX inserted IS the evidence
# for the class pair that produced it. Measured on one paper, 1887 gaps:
#
#     class 0  0.000 em   973 gaps   51.6%
#     class 1  0.167 em   158          8.4%
#     class 2  0.222 em    61          3.2%
#     class 3  0.278 em   341         18.1%
#                                     ----
#                                     81.3% of all gaps land on the table
#
# That is what lets a calligraphic `R` in `x R y` be identified as a RELATION
# rather than an ordinary letter: the author's own spacing says so.

_MU = 1.0 / 18.0                      # 18mu to the em

SPACE_CLASS = {0: 0.0, 1: 3 * _MU, 2: 4 * _MU, 3: 5 * _MU}

# Table 1: allowed spacing class for (left, right) atom classes. None means
# the pair cannot occur -- TeX converts one of the atoms to another type
# first. A value in parentheses in the printed table applies only outside
# scripts; that distinction is not modelled here.
ATOM_CLASSES = ("Ord", "Op", "Bin", "Rel", "Open", "Close", "Punct", "Inner")

_SPACING_TABLE = {
    "Ord":   {"Ord": 0, "Op": 1, "Bin": 2, "Rel": 3, "Open": 0, "Close": 0,
              "Punct": 0, "Inner": 1},
    "Op":    {"Ord": 1, "Op": 1, "Bin": None, "Rel": 3, "Open": 0,
              "Close": 0, "Punct": 0, "Inner": 1},
    "Bin":   {"Ord": 2, "Op": 2, "Bin": None, "Rel": None, "Open": 2,
              "Close": None, "Punct": None, "Inner": 2},
    "Rel":   {"Ord": 3, "Op": 3, "Bin": None, "Rel": 0, "Open": 3,
              "Close": 0, "Punct": 0, "Inner": 3},
    "Open":  {"Ord": 0, "Op": 0, "Bin": None, "Rel": 0, "Open": 0,
              "Close": 0, "Punct": 0, "Inner": 0},
    "Close": {"Ord": 0, "Op": 1, "Bin": 2, "Rel": 3, "Open": 0, "Close": 0,
              "Punct": 0, "Inner": 1},
    "Punct": {"Ord": 1, "Op": 1, "Bin": None, "Rel": 1, "Open": 1,
              "Close": 1, "Punct": 1, "Inner": 1},
    "Inner": {"Ord": 1, "Op": 1, "Bin": 2, "Rel": 3, "Open": 1, "Close": 0,
              "Punct": 1, "Inner": 1},
}


def space_class(gap_em: float, tol: float = 0.03) -> int | None:
    r"""Which of TeX's four spacing classes a measured gap is, or None.

    None means the gap is not one TeX's table produces -- an author's
    explicit `\quad`, an alignment, or a word space in text.
    """
    for cls, width in SPACE_CLASS.items():
        if abs(gap_em - width) <= tol:
            return cls
    return None


def class_pairs(space: int) -> list:
    """The (left, right) atom classes that produce this spacing class.

    The inverse is NOT unique -- a thick space is any of seven pairs -- but
    it is a strong constraint, and combined with the glyph identity, which a
    born-digital PDF gives exactly, it often determines the class.
    """
    return [(a, b) for a in ATOM_CLASSES for b in ATOM_CLASSES
            if _SPACING_TABLE[a][b] == space]


# --- fonts with no /Encoding ----------------------------------------------
#
# Most TeX maths fonts in a dvips/Distiller PDF carry NO /Encoding at all.
# pdfminer then falls back to StandardEncoding, and the fork's `glyphname`
# reports that fallback as if it were the font's own name:
#
#     TeX-matha10  cid 112 -> "p"             RENDERED: `(`
#     TeX-matha10  cid 113 -> "q"             RENDERED: `)`
#     TeX-matha10  cid 16  -> "quotedblleft"
#     TeX-matha10  cid 18  -> "quotedblbase"
#     TeX-matha10  cid 24  -> "perthousand"
#     TeX-matha10  cid  6  -> "ring"
#
# The CID is correct; only the NAME is wrong. Before the fork there was no
# name and the span deferred, which was the right outcome. The fork turned an
# abstention into a confident wrong answer -- `\pi^{*}(\omega_{0})` read as
# `\pi^{*}p\omega_{0}q` -- and a confident wrong answer is the one failure
# this project is built to avoid.
#
# These families are SYMBOL fonts: they contain no Latin alphabet and no text
# punctuation, so a StandardEncoding name in one of them is certainly the
# fallback and not the truth.
_NO_LATIN = re.compile(r"TeX-math[a-z]\d*|CMEX\d*|LMMathExtension\d*"
                       r"|MSAM\d*|MSBM\d*", re.I)

# StandardEncoding names that such a font can never legitimately carry.
_STANDARD_TEXT_NAMES = {
    "quoteleft", "quoteright", "quotedblleft", "quotedblright",
    "quotedblbase", "quotesinglbase", "perthousand", "ring", "grave",
    "acute", "dieresis", "cedilla", "germandbls", "ampersand", "at",
    "exclam", "question", "percent", "dollar", "numbersign",
}

# Slots VERIFIED by rendering the glyph out of a real document. Nothing goes
# in here on the strength of a table or a guess.
_TEX_SLOTS = {
    # Each rendered at 500dpi from its EXACT glyph box out of 1.pdf, side by
    # side, so there is no neighbouring glyph to misread:
    #     16 `=`   18 `~`   24 `!=`   80 `in`   82 `notin`
    ("TeX-matha", 16): "equal",
    ("TeX-matha", 18): "similar",
    ("TeX-matha", 24): "notequal",
    ("TeX-matha", 80): "element",
    ("TeX-matha", 82): "notelement",
    ("TeX-matha", 112): "parenleft",     # rendered: `\pi^{*}(\omega_{0})`
    ("TeX-matha", 113): "parenright",
}


def untrusted_name(fontname: str, glyphname: str | None) -> bool:
    """Is this glyph name the StandardEncoding fallback rather than the font's?

    True means ABSTAIN: the name cannot be relied on, so no LaTeX should be
    emitted for it and the span should defer with the region kept.
    """
    if not glyphname:
        return False
    base = fontname.split("+")[-1]
    if not _NO_LATIN.match(base):
        return False
    if len(glyphname) == 1 and glyphname.isalpha():
        return True
    return glyphname in _STANDARD_TEXT_NAMES


def tex_slot(fontname: str, cid: int) -> str | None:
    """The VERIFIED glyph name for a slot, or None."""
    base = fontname.split("+")[-1]
    for (fam, slot), name in _TEX_SLOTS.items():
        if slot == cid and base.lower().startswith(fam.lower()):
            return name
    return None


# --- log-like operator names ----------------------------------------------
#
# TeX sets `\sup`, `\min`, `\lim` and their kin in UPRIGHT ROMAN, so they
# reach the page as ordinary roman letters with no mark of being one token.
# Read letter by letter they become `\mathrm{m}\mathrm{in}`, and worse: a
# neighbouring glyph then attaches to one of the letters as a script, which
# is how `\sup` became `\mathrm{s}_{<}\mathrm{u}_{i}\mathrm{p}_{-}`.
#
# The list is TeX's own set of log-like functions (Knuth, Appendix B).
# Longest match first, so `limsup` is not read as `lim` followed by `sup`.
OPERATOR_NAMES = (
    "arccos", "arcsin", "arctan", "liminf", "limsup", "injlim", "projlim",
    "varliminf", "varlimsup", "varinjlim", "varprojlim",
    "cosh", "coth", "sinh", "tanh", "det", "dim", "exp", "gcd", "hom",
    "inf", "ker", "lim", "log", "max", "min", "sec", "sup", "arg", "cos",
    "cot", "csc", "deg", "sin", "tan", "ln", "lg", "Pr",
)

_BY_LENGTH = tuple(sorted(OPERATOR_NAMES, key=len, reverse=True))


def operator_name(text: str) -> str | None:
    r"""The LaTeX command for a run of roman letters, if it is one.

    Exact match only. A run spelling `sup` is `\sup`; a run spelling `supp`
    is not, and guessing would turn a variable name into an operator.
    """
    for name in _BY_LENGTH:
        if text == name:
            return "\\" + name
    return None


def operator_prefix(text: str) -> str | None:
    """The longest operator name this run STARTS with, or None."""
    for name in _BY_LENGTH:
        if text.startswith(name):
            return name
    return None
