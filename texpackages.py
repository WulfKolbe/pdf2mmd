"""texpackages - which package defines each command we emit.

Mathpix produces LaTeX that does not always compile, because the training
data carried the symbol but not the package that defines it. We do not have
that problem: every symbol we emit was chosen from a known FONT, and the font
says which package a LaTeX author would have loaded to get it. MSAM/MSBM is
amssymb, EUFM is amsfonts, RSFS is mathrsfs, and so on.

So the preamble can be derived rather than guessed: scan the document for the
commands actually used and require exactly those packages. A document that
uses no fraktur does not load amsfonts.

Only commands that NEED a package appear here. Everything in plain TeX --
`\\alpha`, `\\sum`, `\\frac`, `\\left`, `\\widehat` -- is deliberately absent.
"""
from __future__ import annotations

import re

# command (without backslash) -> package that defines it
PACKAGE_OF: dict[str, str] = {}


def _add(package: str, *names: str) -> None:
    for n in names:
        PACKAGE_OF[n] = package


# --- amsmath: structures and text-in-maths -----------------------------------
_add("amsmath",
     "text", "operatorname", "binom", "dbinom", "tbinom", "dfrac", "tfrac",
     "cfrac", "overset", "underset", "substack", "boxed", "xrightarrow",
     "xleftarrow", "pmod", "bmod", "implies", "impliedby", "iff",
     "DeclareMathOperator", "begin{align}", "begin{gather}", "begin{cases}")

# --- amssymb: the AMS symbol fonts MSAM and MSBM ------------------------------
_add("amssymb",
     # blackboard bold lives in MSBM
     "mathbb", "Bbbk",
     # relations
     "leqslant", "geqslant", "lesssim", "gtrsim", "eqslantless",
     "eqslantgtr", "precsim", "succsim", "subsetneq", "supsetneq",
     "nsubseteq", "nsupseteq", "nleq", "ngeq", "nless", "ngtr", "nsim",
     "nmid", "nparallel", "nrightarrow", "nleftarrow", "nleftrightarrow",
     "ntriangleleft", "ntriangleright", "varsubsetneq", "thickapprox",
     "approxeq", "backsim", "bumpeq", "Bumpeq", "doteqdot", "risingdotseq",
     "fallingdotseq", "circeq", "triangleq", "therefore", "because",
     # arrows
     "rightsquigarrow", "leftrightsquigarrow", "twoheadrightarrow",
     "twoheadleftarrow", "rightarrowtail", "leftarrowtail", "looparrowright",
     "looparrowleft", "curvearrowright", "curvearrowleft", "circlearrowright",
     "circlearrowleft", "upharpoonright", "downharpoonright",
     # shapes and misc
     "square", "blacksquare", "lozenge", "blacklozenge", "bigstar",
     "circledast", "circledcirc", "circleddash", "divideontimes",
     "leftthreetimes", "rightthreetimes", "varnothing", "complement",
     "hslash", "hbar", "backepsilon", "digamma", "varkappa", "beth",
     "gimel", "daleth", "vartriangleleft", "vartriangleright",
     "trianglelefteq", "trianglerighteq", "smallsetminus", "centerdot",
     "intercal", "doublebarwedge", "veebar", "barwedge", "boxplus",
     "boxminus", "boxtimes", "boxdot", "ltimes", "rtimes", "dotplus",
     "Subset", "Supset", "Cap", "Cup", "pitchfork", "sqsubset", "sqsupset")

# --- fonts that are their own package ----------------------------------------
_add("amsfonts", "mathfrak")
_add("mathrsfs", "mathscr")
_add("stmaryrd",
     "llbracket", "rrbracket", "llparenthesis", "rrparenthesis",
     "shortrightarrow", "shortleftarrow", "bigcurlyvee", "bigcurlywedge",
     "varobslash", "obar", "ogreaterthan", "olessthan")
_add("mathtools",
     "coloneqq", "eqqcolon", "shortmid", "vcentcolon", "colonapprox",
     "Coloneqq", "dblcolon", "xleftrightarrow", "xhookrightarrow",
     "xhookleftarrow", "xmapsto", "DeclarePairedDelimiter", "prescript",
     "adjustlimits", "smashoperator", "mathclap", "mathllap", "mathrlap",
     "underbracket", "overbracket", "splitfrac", "splitdfrac")

# --- bbm: an alternative blackboard bold, drawn from a real font rather than
# amssymb's outline caps. A document that uses it needs BOTH, since amssymb
# supplies the relations.
_add("bbm", "mathbbm", "mathbbmss", "mathbbmtt")

# --- bm: bold maths that follows the symbol's own font, unlike \boldsymbol
_add("bm", "bm", "hm", "bmdefine", "heavymath")

# --- extarrows: extensible arrows with text above AND below
_add("extarrows",
     "xlongequal", "xLongrightarrow", "xLongleftarrow", "xLongleftrightarrow",
     "xleftrightarrow", "xrightharpoondown", "xrightharpoonup",
     "xleftharpoondown", "xleftharpoonup", "xrightleftharpoons",
     "xleftrightharpoons", "xtofrom", "xdashrightarrow", "xdashleftarrow")

# --- cancel: struck-through terms, common in worked derivations
_add("cancel", "cancel", "bcancel", "xcancel", "cancelto")
_add("graphicx", "includegraphics")
# xcolor: text colour and cell backgrounds recovered from the PDF's g/rg/k
# and G/RG/K operators. Markdown cannot express either, so these appear only
# in the LaTeX projection.
_add("xcolor", "textcolor", "colorbox", "fcolorbox", "pagecolor",
     "definecolor", "color")
_add("latexsym", "Box", "Diamond", "lhd", "rhd", "unlhd", "unrhd", "mho")

# Commands that no package reliably provides, so the preamble defines them.
# A `\providecommand` is inert when the document already has the real thing,
# which makes it safe to emit unconditionally for anything we actually used.
#
# These four came from a working Mathpix-output preamble. The BODIES here are
# reasonable standard fallbacks; if the definitions in use differ, replace the
# values -- the mechanism does not care what the body is.
PROVIDE: dict[str, str] = {
    "Perp": r"\providecommand{\Perp}{\perp\!\!\!\perp}",
    "overparen": r"\providecommand{\overparen}[1]{\overset{\frown}{#1}}",
    "oiint": r"\providecommand{\oiint}{\oint\!\!\!\oint}",
    "longdiv": r"\providecommand{\longdiv}[2]{#1\overline{\smash{)}\,#2}}",
}

_CMD = re.compile(r"\\([a-zA-Z]+)")


def provides_for(latex: str) -> list[str]:
    r"""`\providecommand` lines for commands this document uses that no
    package defines."""
    used = {m for m in _CMD.findall(latex) if m in PROVIDE}
    return [PROVIDE[m] for m in sorted(used)]


def packages_for(latex: str) -> list[str]:
    """The packages this LaTeX needs, in a stable load order.

    Derived from what is actually present, so a document with no fraktur does
    not load amsfonts. amsmath is required by amssymb in practice and is
    listed first when either is needed.
    """
    needed = {PACKAGE_OF[m] for m in _CMD.findall(latex) if m in PACKAGE_OF}
    # Dependencies: amssymb and mathtools both assume amsmath is loaded, and
    # mathtools patches it, so it must come after.
    if needed & {"amssymb", "mathtools"}:
        needed.add("amsmath")
    # Load order matters: amsmath before amssymb, mathtools after amsmath
    # (it patches it), and fontspec last because it reconfigures fonts.
    order = ["amsmath", "amssymb", "amsfonts", "bbm", "bm", "mathtools",
             "extarrows", "cancel", "mathrsfs", "stmaryrd", "latexsym",
             "xcolor", "graphicx", "fontspec"]
    return [p for p in order if p in needed] + sorted(needed - set(order))


def unknown_commands(latex: str, known: set[str] | None = None) -> set[str]:
    """Commands we emitted that are in neither plain TeX nor the table above.

    A non-empty result is a warning that the document may not compile -- the
    same failure mode as Mathpix output, surfaced instead of shipped.
    """
    plain = known or PLAIN_TEX
    return {m for m in _CMD.findall(latex)
            if m not in plain and m not in PACKAGE_OF
            and m not in _STRUCTURE and m not in PROVIDE}


# Document-structure commands from the LaTeX kernel. Not symbols, so they
# must not be reported as unknown -- a warning that fires on `\section` tells
# the reader nothing.
_STRUCTURE = {
    "documentclass", "usepackage", "begin", "end", "section", "subsection",
    "subsubsection", "paragraph", "chapter", "part", "label", "ref", "cite",
    "index", "footnote", "caption", "item", "title", "author", "date",
    "maketitle", "tableofcontents", "newpage", "clearpage", "pagebreak",
    "hfill", "vfill", "hspace", "vspace", "centering", "raggedright",
    "textbf", "textit", "texttt", "textrm", "textsf", "emph", "bf", "it",
    "tt", "rm", "sf", "sl", "sc", "TeX", "LaTeX", "textwidth", "linewidth",
    "columnwidth", "textheight", "small", "large", "Large", "LARGE", "huge",
    "Huge", "footnotesize", "scriptsize", "tiny", "normalsize", "par",
    "noindent", "input", "include", "url", "href", "verb",
    # escapes this projector emits for TeX specials in prose
    "textasciitilde", "textasciicircum", "textbackslash",
}


# Commands available without loading anything. Not exhaustive for all of TeX,
# but covers everything this projector emits from plain/LaTeX kernel.
PLAIN_TEX = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi",
    "varpi", "rho", "varrho", "sigma", "varsigma", "tau", "upsilon", "phi",
    "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda",
    "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
    "sum", "prod", "coprod", "int", "oint", "bigcup", "bigcap", "bigvee",
    "bigwedge", "bigoplus", "bigotimes", "bigodot", "biguplus", "bigsqcup",
    "frac", "sqrt", "overline", "underline", "widehat", "widetilde", "hat",
    "tilde", "bar", "vec", "dot", "ddot", "check", "breve", "acute", "grave",
    "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl",
    "Bigr", "biggl", "biggr", "Biggl", "Biggr", "langle", "rangle", "lceil",
    "rceil", "lfloor", "rfloor", "backslash",
    "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal", "boldsymbol",
    "times", "div", "pm", "mp", "cdot", "cdots", "ldots", "vdots", "ddots",
    "circ", "bullet", "star", "ast", "oplus", "ominus", "otimes", "oslash",
    "odot", "cap", "cup", "setminus", "wedge", "vee", "neg", "forall",
    "exists", "nabla", "partial", "infty", "emptyset", "in", "ni", "notin",
    "subset", "supset", "subseteq", "supseteq", "equiv", "sim", "simeq",
    "approx", "cong", "neq", "leq", "geq", "ll", "gg", "prec", "succ",
    "preceq", "succeq", "propto", "perp", "parallel", "mid", "top", "bot",
    "rightarrow", "leftarrow", "leftrightarrow", "Rightarrow", "Leftarrow",
    "Leftrightarrow", "mapsto", "to", "gets", "longrightarrow",
    "longleftarrow", "uparrow", "downarrow", "nearrow", "searrow", "swarrow",
    "nwarrow", "hookrightarrow", "hookleftarrow", "rightharpoonup",
    "rightharpoondown", "rightleftharpoons",
    "log", "ln", "exp", "sin", "cos", "tan", "cot", "sec", "csc", "arcsin",
    "arccos", "arctan", "sinh", "cosh", "tanh", "min", "max", "sup", "inf",
    "lim", "limsup", "liminf", "det", "dim", "ker", "deg", "gcd", "hom",
    "quad", "qquad", "hspace", "vspace", "newpage", "textbackslash",
    "prime", "ell", "Re", "Im", "wp", "aleph", "surd", "angle", "triangle",
    "diamond", "diamondsuit", "heartsuit", "spadesuit", "clubsuit", "flat",
    "sharp", "natural", "dag", "ddag", "S", "P", "copyright", "pounds",
    "dagger", "ddagger", "wr", "amalg", "uplus", "sqcap", "sqcup", "sqsubseteq", "sqsupseteq",
    "vdash", "dashv", "models", "smile", "frown", "asymp", "doteq",
    "triangleleft", "triangleright", "bigtriangleup", "bigtriangledown",
    "not", "colon", "ldotp", "cdotp",
}
