#!/usr/bin/env python3
r"""lstlang — how well can the language be guessed, and from what?

The gold set is a LABELLED language corpus: 168 of its 291 listings carry
the `language=` the author chose. So the detector can be measured here
rather than trusted, and measured on the two texts that matter:

  THE AUTHOR'S BODY   the ceiling. Perfect code, exactly as written.
  WHAT WE READ        what a reader would actually have. If the reading is
                      wrong the guess inherits it, and a language guessed
                      from a damaged body is worse than no language.

A listings `language=` is a DIALECT NAME, not always a language: JuliaMin,
ASPlang and WikiText are `\lstdefinelanguage` names the author invented.
Only the unambiguous aliases are mapped; the rest are reported apart,
because scoring a guess against a name no detector has heard of measures
nothing.

    python3 lstlang.py
    python3 lstlang.py --limit 40
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
from pathlib import Path

GOLD = Path(os.environ.get("PDF2MMD_LSTGOLD",
                           Path.home() / "pdfdrill-library" / "lstgold"))
CODE = Path(os.environ.get("PDF2MMD_CODE", Path(__file__).resolve().parent.parent))
LANG_PY = Path(os.environ.get("PDF2MMD_LANG_PY",
                              Path.home() / ".wtc-venv" / "bin" / "python"))
_LST = re.compile(r"\\begin\{lstlisting\}(?:\[([^\]]*)\])?\n(.*?)\\end\{lstlisting\}",
                  re.S)
_DECL = re.compile(r"^% language\s*:\s*(.+)$", re.M)

#: The author's dialect name -> what a detector would call it. Only the
#: unambiguous ones: `ASPlang` is answer-set programming written in a
#: Prolog-like syntax and no detector has a class for it, so it is NOT
#: mapped to prolog -- that would score our guess against our own opinion.
ALIAS = {
    "juliamin": "julia", "c++": "cpp", "cpp": "cpp", "c": "c",
    "python": "python", "python3": "python", "html": "html", "xml": "xml",
    "json": "json", "sql": "sql", "java": "java", "bash": "bash",
    "sh": "bash", "shell": "bash", "javascript": "javascript",
    "typescript": "typescript", "tex": "tex", "latex": "tex", "awk": "awk",
    "r": "r", "ruby": "ruby", "go": "go", "rust": "rust", "yaml": "yaml",
    "matlab": "matlab", "scala": "scala", "haskell": "haskell",
    "lisp": "lisp", "php": "php", "perl": "perl", "css": "css",
}


def declared(tex: str) -> str:
    m = _DECL.search(tex)
    if not m or "not declared" in m.group(1):
        return ""
    return m.group(1).strip().lower()


def body(tex: str) -> str:
    m = _LST.search(tex)
    if not m:
        return ""
    text = m.group(2)
    for ch in re.findall(r"escapechar\s*=\s*\\?(.)", tex):
        text = re.sub(re.escape(ch) + r".*?" + re.escape(ch), "", text, flags=re.S)
    return text


def read_back(stem: str) -> str:
    """The fenced code pdf2mmd read from the rendered page."""
    md = GOLD / "read" / (stem + ".md")
    if not md.exists():
        return ""
    t = md.read_text(encoding="utf-8", errors="replace")
    out = []
    for b in re.findall(r"```(.*?)```", t, re.S):
        out += [re.sub(r"^\s*\d+\s", "", x) for x in b.split("\n")]
    return "\n".join(out)


def guess(corpus: dict) -> dict:
    if not LANG_PY.is_file():
        return {}
    r = subprocess.run([str(LANG_PY), str(CODE / "out" / "langdetect.py")],
                       input=json.dumps(corpus), capture_output=True,
                       text=True, timeout=900)
    try:
        out = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return {} if "__error__" in out else out


def score(truth: dict, got: dict) -> tuple:
    hit = miss = 0
    wrong = collections.Counter()
    for k, want in truth.items():
        g = (got.get(k) or "").lower()
        if g == want:
            hit += 1
        else:
            miss += 1
            wrong[(want, g or "(none)")] += 1
    return hit, miss, wrong


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    A = ap.parse_args()
    files = sorted(glob.glob(str(GOLD / "lst-*.tex")))
    if A.limit:
        files = files[:A.limit]
    truth, bodies, readings, unmapped = {}, {}, {}, collections.Counter()
    for f in files:
        p = Path(f)
        tex = p.read_text(encoding="utf-8", errors="replace")
        d = declared(tex)
        b = body(tex)
        if not b.strip():
            continue
        bodies[p.stem] = b
        r = read_back(p.stem)
        if r.strip():
            readings[p.stem] = r
        if not d:
            continue
        if d not in ALIAS:
            unmapped[d] += 1
            continue
        truth[p.stem] = ALIAS[d]

    if not LANG_PY.is_file():
        print("no detector: %s does not exist" % LANG_PY)
        print("   python3 -m venv ~/.wtc-venv && ~/.wtc-venv/bin/pip install whats_that_code")
        return
    gb = guess(bodies)
    gr = guess(readings)
    print("gold listings                %d" % len(bodies))
    print("  declared a language        %d" % (len(truth) + sum(unmapped.values())))
    print("  ...with a mappable name    %d" % len(truth))
    if unmapped:
        print("  ...a dialect no detector knows: %s"
              % ", ".join("%s %d" % kv for kv in unmapped.most_common()))
    for label, got in (("the AUTHOR'S body", gb), ("what pdf2mmd READ", gr)):
        sub = {k: v for k, v in truth.items() if k in got}
        hit, miss, wrong = score(sub, got)
        n = hit + miss
        print("\n  guessed from %-20s %d of %d  (%.0f%%)"
              % (label, hit, n, 100 * hit / n if n else 0))
        for (want, g), c in wrong.most_common(8):
            print("       %-12s guessed %-14s %d" % (want, g, c))


if __name__ == "__main__":
    main()
