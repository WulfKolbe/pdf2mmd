#!/usr/bin/env python3
r"""lstprobe — the frame probe: one body, every frame style, with and without
an image above it.

781d — A DRAWN FRAME DELETED THE LISTING.

With `frame=single`, `lines`, `tb`, `shadowbox` or `trBL`, pdf2mmd found
zero listings: the block was classified as a diagram and cropped away,
glyphs and frame rules together. `frame=none` and `frame=leftline` worked,
which is why 291 gold listings never showed it -- none of them draws a
frame the way this one does.

A corpus of real documents has blind spots that one hand-made file finds.
This is that file, crossed out into fourteen.

    python3 lstprobe.py
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GOLD = Path(os.environ.get("PDF2MMD_LSTGOLD",
                           Path.home() / "pdfdrill-library" / "lstgold"))
PROBE = GOLD / "probe"
CODE = Path(os.environ.get("PDF2MMD_CODE", Path(__file__).resolve().parent.parent))

WANT = "for (i in 0..N)\n  for (j in 0..M)\n    S1\n    S2"
#: These two draw no closed box -- `none` draws nothing and `leftline` one
#: edge -- so no rectangle is the RIGHT answer, not a miss.
NO_BOX = ("f-none", "f-none-img", "f-leftline", "f-leftline-img")


def one(tex: Path) -> dict:
    d = Path(tempfile.mkdtemp(prefix="probe-", dir="/tmp/claude-1000"))
    try:
        shutil.copy2(tex, d)
        for extra in PROBE.glob("*.png"):
            shutil.copy2(extra, d)
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-no-shell-escape",
                        "-output-directory", str(d), str(d / tex.name)],
                       capture_output=True, text=True, errors="replace", timeout=180)
        pdf = d / (tex.stem + ".pdf")
        if not pdf.exists():
            return {"id": tex.stem, "ok": False, "why": "did not compile"}
        sys.path.insert(0, str(CODE))
        import docmodel_six as dm
        page = dm.build(str(pdf))[0]
        want_box = tex.stem not in NO_BOX
        row = {"id": tex.stem, "listings": len(page.listings),
               "frames": len(page.frames), "diagrams": len(page.diagrams)}
        if len(page.listings) != 1:
            row.update(ok=False, why="%d listings" % len(page.listings))
            return row
        got = page.listings[0].text()
        if got != WANT:
            row.update(ok=False, why="code read as %r" % got[:40])
            return row
        if want_box and len(page.frames) != 1:
            row.update(ok=False, why="%d frames, wanted 1" % len(page.frames))
            return row
        if not want_box and page.frames:
            row.update(ok=False, why="%d frames, wanted none" % len(page.frames))
            return row
        if page.diagrams:
            row.update(ok=False, why="%d diagram(s)" % len(page.diagrams))
            return row
        row.update(ok=True, why="")
        return row
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> None:
    files = sorted(PROBE.glob("f-*.tex"))
    if not files:
        print("no probe files under %s" % PROBE)
        return
    bad = 0
    for f in files:
        r = one(f)
        mark = "ok  " if r.get("ok") else "FAIL"
        print("  %s %-18s listings=%s frames=%s diagrams=%s %s"
              % (mark, r["id"], r.get("listings", "-"), r.get("frames", "-"),
                 r.get("diagrams", "-"), r.get("why", "")))
        bad += not r.get("ok")
    print("%d of %d" % (len(files) - bad, len(files)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
