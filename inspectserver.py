#!/usr/bin/env python3
"""inspectserver.py — resolve MathPix CDN crop links from pdfdrill's
`inspect/pages/` folder, with no Deep-Zoom pyramid.

`tools/imageserver/mathpix_server.py` already does this from a DZI pyramid.
This is the same contract over the plain page PNGs that `pdfdrill inspect`
has already written, so a document can be hydrated without building a pyramid
first. Use the pyramid server when you need deep zoom or the pages are too
large to hold in memory; use this one to look at a document now.

ROUTES (identical in shape to mathpix_server.py, so `docinspect --image-base`
and any Mathpix-syntax Markdown work against either)

  /cropped/<image_id>.jpg?height=H&width=W&top_left_y=Y&top_left_x=X[&page=N]
                            -> the crop, as JPEG (or .png for PNG)
  /pages/p<N>.png           -> the whole page image
  /render/p<N>.png?dpi=400  -> rasterized ON THE FLY by Ghostscript to stdout
                               (needs --pdf); optionally cropped with the same
                               rectangle params
  /healthz                  -> JSON status and a resolvable sample URL
  /                         -> a plain index of the pages found

TWO RESOLUTIONS, ON PURPOSE
  The stored pages exist to be LOOKED AT, so 250 dpi is enough -- and 250 dpi
  is exactly what Mathpix renders at (measured: 250.05 dpi on every page of a
  377-page book), so crop rectangles from a lines.json need no rescaling.

  Glyph TOPOLOGY is not stable at 250 dpi. Measured against a 1200 dpi
  reference over 194 isolated glyphs: at 250 dpi 2.6% of glyphs have the wrong
  component count and 6.7% the wrong hole count; at 400 dpi that falls to 0.5%
  and 5.2%. A 7pt alpha is two components at 250 dpi and one at 400. Since hole
  count IS a primary inkdrill feature, anything topological must be measured on
  a 400 dpi (or finer) render, never on the viewing image. `/render` exists so
  that raster can be produced on demand instead of stored.

PAGE RESOLUTION, in order
  1. ?page=N
  2. image_id found in a loaded lines.json -> its page
  3. trailing integer of the image_id      (…g-209 -> page 209)

SCALE RESOLUTION, in order
  1. lines.json page_width/page_height for that page -> exact ratio to the
     page image's pixel size
  2. --coord-width/--coord-height          -> the same ratio, stated by hand
  3. 1.0, with a warning ONCE per process: the request's pixels are assumed to
     already be page-image pixels. A silently wrong scale produces a crop of
     the wrong part of the page, which looks like a recognition failure rather
     than a configuration error, so it is never assumed quietly.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment dependent
    sys.exit("inspectserver needs Pillow: pip install pillow")

import subprocess

class CropOutOfRange(ValueError):
    """A rectangle that lands outside the page, carrying the reason why."""

    def __init__(self, info: dict):
        super().__init__(info.get("error", "empty crop"))
        self.info = info


PAGE_RE = re.compile(r"p(\d+)\.png$", re.I)
TRAILING_INT = re.compile(r"(\d+)\s*$")


class Pages:
    """The page images, plus any lines.json that fixes the coordinate space."""

    def __init__(self, pages_dir: str, lines: list[str] | None = None,
                 coord: tuple[int, int] | None = None,
                 pdf: str | None = None, gs: str = "gs",
                 assume_pixels: bool = False):
        self.root = pages_dir
        self.dir = pages_dir
        self.coord = coord
        self.pdf = pdf
        self.gs = gs
        self.assume_pixels = assume_pixels
        self.page_pt: dict[int, tuple[float, float]] = {}
        if pdf:
            self._load_page_sizes(pdf)
        self.by_page: dict[int, str] = {}
        for name in os.listdir(pages_dir):
            m = PAGE_RE.search(name)
            if m:
                self.by_page[int(m.group(1))] = os.path.join(pages_dir, name)
        self.dims: dict[int, tuple[float, float]] = {}
        self.px_per_pt: dict[int, float] = {}
        self.id_page: dict[str, int] = {}
        for path in lines or []:
            self._load_lines(path)
        self._warned = False

    def _load_page_sizes(self, pdf: str) -> None:
        """Page sizes in POINTS, needed to rescale a rectangle to another dpi."""
        try:
            from pdfminer.pdfpage import PDFPage
            from pdfminer.pdfparser import PDFParser
            from pdfminer.pdfdocument import PDFDocument
            with open(pdf, "rb") as fh:
                doc = PDFDocument(PDFParser(fh))
                for i, pg in enumerate(PDFPage.create_pages(doc), start=1):
                    x0, y0, x1, y1 = pg.mediabox
                    self.page_pt[i] = (abs(x1 - x0), abs(y1 - y0))
        except Exception as exc:                       # pragma: no cover
            print(f"  ! could not read page sizes from {pdf}: {exc}",
                  file=sys.stderr)

    def _load_lines(self, path: str) -> None:
        if os.path.isdir(path):
            for n in sorted(os.listdir(path)):
                if n.endswith("lines.json"):
                    self._load_lines(os.path.join(path, n))
            return
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  ! could not read {path}: {exc}", file=sys.stderr)
            return
        stem = re.sub(r"[._-]?lines\.json$", "", os.path.basename(path))
        for pg in doc.get("pages", []):
            n = pg.get("page")
            if n is None:
                continue
            w, h = pg.get("page_width"), pg.get("page_height")
            if w and h:
                self.dims[int(n)] = (float(w), float(h))
            if "px_per_pt" in pg:
                self.px_per_pt[int(n)] = float(pg["px_per_pt"])
            img = pg.get("image_id")
            if img:
                self.id_page[str(img)] = int(n)
        if stem:
            self.id_page.setdefault(stem, next(iter(self.dims), 1))

    # -------------------------------------------------------------- resolve
    def check_identity(self, lines_paths, pdf: str | None) -> list[str]:
        """Do these page images belong to the document we were given?

        Page images used to share one folder across documents, so a crop could
        show a figure from an entirely different book under the right caption.
        The renderer now writes a manifest; this compares it with the PDF and
        the lines.json, and says so plainly when they disagree.
        """
        notes: list[str] = []
        man = os.path.join(self.root, "manifest.json")
        doc = None
        if os.path.exists(man):
            try:
                with open(man, encoding="utf-8") as fh:
                    info = json.load(fh)
                doc = info.get("document")
            except (OSError, ValueError):
                doc = None

        for src in (pdf, *(lines_paths or [])):
            if not src or not doc:
                continue
            stem = os.path.basename(src)
            for suffix in (".pdf", ".lines.json", ".json"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            if stem and doc and stem != doc:
                notes.append(
                    f"these page images were rendered from '{doc}' but you "
                    f"passed '{stem}'. Every crop will show the wrong page. "
                    f"Point --pages at that document's own folder.")
                break

        if doc is None and (pdf or lines_paths):
            notes.append(
                f"{self.root} has no manifest.json, so the images cannot be "
                f"matched to a document. Re-run pdf2mmd.sh to write one.")
        return notes

    def check_consistency(self) -> list[str]:
        """Warn at STARTUP about a coordinate space that cannot be right.

        Waiting for a request to fail means the first symptom is an HTTP 400
        on an image a Markdown viewer requested silently, which reads as "the
        server is broken". The mismatch is visible before any request: a
        lines.json written in PDF points declares a page a few hundred units
        wide while the page image is a few thousand pixels.
        """
        notes = []
        for page, (w, h) in sorted(self.dims.items()):
            path = self.by_page.get(page)
            if not path:
                continue
            try:
                with Image.open(path) as im:
                    iw, ih = im.width, im.height
            except OSError:
                continue
            ratio = iw / float(w) if w else 0.0
            if ratio > 1.5 and page not in self.px_per_pt:
                notes.append(
                    f"page {page}: lines.json declares {w:.0f}x{h:.0f} but the "
                    f"page image is {iw}x{ih} (ratio {ratio:.2f}). If that "
                    f"lines.json is in PDF POINTS while your crop URLs are in "
                    f"pixels, every crop will land off the page. Regenerate it "
                    f"with a current pdf2mmd, or start with --assume-pixels.")
            break                 # one page is enough to show the mismatch
        return notes

    def page_of(self, image_id: str, q: dict) -> int:
        if "page" in q:
            return int(q["page"][0])
        if image_id in self.id_page:
            return self.id_page[image_id]
        m = TRAILING_INT.search(image_id)
        if m:
            return int(m.group(1))
        raise ValueError(f"cannot tell which page {image_id!r} refers to; "
                         "add ?page=N")

    def scale(self, page: int, img_w: int, img_h: int) -> tuple[float, float]:
        if self.assume_pixels:
            return 1.0, 1.0
        src = self.dims.get(page) or self.coord
        if src:
            return img_w / float(src[0]), img_h / float(src[1])
        if not self._warned:
            self._warned = True
            print("  ! no lines.json and no --coord-width/--coord-height: "
                  "assuming request coordinates are already page-image pixels",
                  file=sys.stderr)
        return 1.0, 1.0

    def diagnose(self, page: int, q: dict, img_w: int, img_h: int) -> dict:
        """Explain a rectangle that lands off the page.

        A bare "empty crop" says nothing about WHY, and the usual cause is a
        units mismatch between the request and the lines.json: one in PDF
        points, the other in page-image pixels. The numbers make that obvious
        the moment they are shown side by side.
        """
        src = self.dims.get(page) or self.coord
        sx, sy = self.scale(page, img_w, img_h)

        def num(name):
            try:
                return float(q[name][0])
            except (KeyError, ValueError, IndexError):
                return float("nan")

        left, top = num("top_left_x") * sx, num("top_left_y") * sy
        info = {
            "error": "empty crop: the rectangle maps outside the page",
            "page": page,
            "page_image_px": [img_w, img_h],
            "coordinate_space": list(src) if src else None,
            "space_source": ("lines.json" if page in self.dims else
                             ("--coord-width/--coord-height" if self.coord
                              else "assumed page-image pixels")),
            "scale_applied": [round(sx, 4), round(sy, 4)],
            "requested": {k: q[k][0] for k in
                          ("top_left_x", "top_left_y", "width", "height")
                          if k in q},
            "mapped_to_px": [round(left), round(top),
                             round(left + num("width") * sx),
                             round(top + num("height") * sy)],
        }
        hints = []
        if src and abs(sx - 1.0) > 0.05:
            fits_as_pixels = (num("top_left_x") < img_w
                              and num("top_left_y") < img_h)
            if fits_as_pixels:
                hints.append(
                    f"The request fits inside the page image as-is "
                    f"({img_w}x{img_h}), but the declared coordinate space is "
                    f"{src[0]}x{src[1]}, so everything was scaled by "
                    f"{sx:.2f}. That is the signature of a lines.json written "
                    f"in PDF POINTS while the URL is in PIXELS. Regenerate "
                    f"lines.json with a current pdf2mmd (it writes pixels and "
                    f"records px_per_pt), or restart the server without "
                    f"--lines, or pass --assume-pixels.")
        if not hints:
            hints.append(
                "Check that the page number and the rectangle belong to the "
                "same document as the page images.")
        info["hint"] = " ".join(hints)
        return info

    def crop(self, image_id: str, q: dict, fmt: str) -> tuple[bytes, str]:
        page = self.page_of(image_id, q)
        path = self.by_page.get(page)
        if not path:
            raise FileNotFoundError(
                f"no page image for page {page} in {self.dir} "
                f"(have {sorted(self.by_page)[:8]}…)")

        def num(name: str) -> float:
            if name not in q:
                raise ValueError(
                    "need top_left_x, top_left_y, width and height")
            return float(q[name][0])

        with Image.open(path) as im:
            im.load()
            sx, sy = self.scale(page, im.width, im.height)
            left = int(round(num("top_left_x") * sx))
            top = int(round(num("top_left_y") * sy))
            right = left + max(1, int(round(num("width") * sx)))
            bottom = top + max(1, int(round(num("height") * sy)))
            # clamp rather than fail: a rect one pixel past the edge is a
            # rounding artefact, not a bad request
            left, top = max(0, left), max(0, top)
            right, bottom = min(im.width, right), min(im.height, bottom)
            if right <= left or bottom <= top:
                raise CropOutOfRange(
                    self.diagnose(page, q, im.width, im.height))
            out = im.crop((left, top, right, bottom))
            buf = io.BytesIO()
            if fmt == "png":
                out.save(buf, "PNG")
                return buf.getvalue(), "image/png"
            out.convert("RGB").save(buf, "JPEG", quality=92)
            return buf.getvalue(), "image/jpeg"

    def render(self, page: int, dpi: int, device: str = "png16m",
               rect: tuple[int, int, int, int] | None = None
               ) -> tuple[bytes, str]:
        """Rasterize a page with Ghostscript straight to stdout.

        png16m, not png256: Ghostscript does not halftone truecolour, so what
        reaches a topology pass is the document\'s own content rather than the
        rasterizer\'s dithering.
        """
        if not self.pdf:
            raise ValueError("no --pdf given, so /render is unavailable")
        cmd = [self.gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
               f"-sDEVICE={device}", f"-r{dpi}",
               f"-dFirstPage={page}", f"-dLastPage={page}",
               "-sOutputFile=-", self.pdf]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=180)
        except FileNotFoundError:
            raise ValueError(f"{self.gs!r} not found; pass --gs")
        if proc.returncode != 0 or not proc.stdout:
            tail = proc.stderr.decode("utf-8", "replace")[-300:]
            raise ValueError(f"ghostscript failed: {tail}")
        if rect is None:
            return proc.stdout, "image/png"
        with Image.open(io.BytesIO(proc.stdout)) as im:
            im.load()
            left, top, w, h = rect
            box = (max(0, left), max(0, top),
                   min(im.width, left + max(1, w)),
                   min(im.height, top + max(1, h)))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError(f"empty crop after clamping to {im.size}")
            buf = io.BytesIO()
            im.crop(box).save(buf, "PNG")
            return buf.getvalue(), "image/png"

    def rect_at_dpi(self, page: int, q: dict, dpi: int):
        """Scale a lines.json-space rectangle into pixels at `dpi`.

        The coordinate space of a request is whatever the page images use
        (Mathpix\'s 250 dpi for a lines.json). Rendering at another resolution
        means the rectangle must be rescaled, and getting that wrong returns a
        plausible crop of the wrong region -- so the source space must be known,
        never guessed.
        """
        src = self.dims.get(page) or self.coord
        if not src:
            raise ValueError("cannot scale a rectangle without lines.json or "
                             "--coord-width/--coord-height")
        if page not in self.page_pt:
            raise ValueError("page size in points unknown; cannot rescale")
        w_pt, h_pt = self.page_pt[page]
        sx = (w_pt * dpi / 72.0) / float(src[0])
        sy = (h_pt * dpi / 72.0) / float(src[1])

        def num(name):
            if name not in q:
                raise ValueError("need top_left_x, top_left_y, width, height")
            return float(q[name][0])

        return (int(round(num("top_left_x") * sx)),
                int(round(num("top_left_y") * sy)),
                int(round(num("width") * sx)),
                int(round(num("height") * sy)))

    def page_bytes(self, page: int) -> tuple[bytes, str]:
        path = self.by_page.get(page)
        if not path:
            raise FileNotFoundError(f"no page image for page {page}")
        with open(path, "rb") as fh:
            return fh.read(), "image/png"


class Handler(BaseHTTPRequestHandler):
    pages: Pages = None            # set on the class before serving
    server_version = "inspectserver/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fail(self, code: int, msg: str) -> None:
        self._send(code, json.dumps({"error": msg}).encode(), "application/json")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path == "/healthz":
                sample = None
                if self.pages.by_page:
                    n = sorted(self.pages.by_page)[0]
                    sample = (f"/cropped/sampleg-{n}.jpg?height=200&width=400"
                              f"&top_left_y=100&top_left_x=100")
                return self._send(200, json.dumps({
                    "ok": True,
                    "pages": len(self.pages.by_page),
                    "page_numbers": sorted(self.pages.by_page)[:20],
                    "lines_json_pages": len(self.pages.dims),
                    "sample": sample,
                }, indent=1).encode(), "application/json")

            if path.startswith("/render/"):
                m = PAGE_RE.search(path)
                if not m:
                    return self._fail(404, "expected /render/p<N>.png")
                page = int(m.group(1))
                dpi = int(q.get("dpi", ["400"])[0])
                device = q.get("device", ["png16m"])[0]
                rect = None
                if "width" in q and "height" in q:
                    # the rectangle arrives in the SAME coordinate space as
                    # /cropped (the lines.json page space), so it is rescaled
                    # to the requested dpi here rather than by the caller
                    rect = self.pages.rect_at_dpi(page, q, dpi)
                body, ctype = self.pages.render(page, dpi, device, rect)
                return self._send(200, body, ctype)

            if path.startswith("/pages/"):
                m = PAGE_RE.search(path)
                if not m:
                    return self._fail(404, "expected /pages/p<N>.png")
                body, ctype = self.pages.page_bytes(int(m.group(1)))
                return self._send(200, body, ctype)

            if path.startswith("/cropped/"):
                tail = path[len("/cropped/"):]
                fmt = "png" if tail.lower().endswith(".png") else "jpg"
                image_id = re.sub(r"\.(jpe?g|png)$", "", tail, flags=re.I)
                body, ctype = self.pages.crop(image_id, q, fmt)
                return self._send(200, body, ctype)

            if path == "/":
                rows = "".join(
                    f'<li><a href="/pages/p{n}.png">p{n}.png</a></li>'
                    for n in sorted(self.pages.by_page))
                html = (f"<h1>inspectserver</h1><p>{len(self.pages.by_page)} "
                        f"pages from {self.pages.dir}</p><ul>{rows}</ul>")
                return self._send(200, html.encode(), "text/html; charset=utf-8")

            self._fail(404, f"no route {path}")
        except FileNotFoundError as exc:
            self._fail(404, str(exc))
        except CropOutOfRange as exc:
            self._send(400, json.dumps(exc.info, indent=1).encode(),
                       "application/json")
        except ValueError as exc:
            self._fail(400, str(exc))
        except Exception as exc:                      # pragma: no cover
            self._fail(500, f"{type(exc).__name__}: {exc}")

    def log_message(self, fmt, *args):
        if os.environ.get("INSPECTSERVER_QUIET"):
            return
        super().log_message(fmt, *args)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", required=True,
                    help="folder of page images named p<N>.png "
                         "(pdfdrill writes <drill>/inspect/pages)")
    ap.add_argument("--lines", action="append", default=[],
                    help="a lines.json, or a folder of them, fixing the "
                         "coordinate space (repeatable)")
    ap.add_argument("--coord-width", type=int,
                    help="page width in the coordinate space requests use")
    ap.add_argument("--coord-height", type=int)
    ap.add_argument("--assume-pixels", action="store_true",
                    help="ignore any declared coordinate space and treat "
                         "request coordinates as page-image pixels")
    ap.add_argument("--pdf", help="source PDF, enabling /render (on-the-fly "
                                  "Ghostscript rasterization at any dpi)")
    ap.add_argument("--gs", default="gs", help="Ghostscript binary")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    coord = None
    if args.coord_width and args.coord_height:
        coord = (args.coord_width, args.coord_height)
    Handler.pages = Pages(args.pages, args.lines, coord, args.pdf, args.gs,
                          args.assume_pixels)
    if not Handler.pages.by_page:
        sys.exit(f"no p<N>.png found in {args.pages}")
    print(f"inspectserver: {len(Handler.pages.by_page)} pages from {args.pages}")
    for note in (Handler.pages.check_identity(args.lines, args.pdf)
                 + Handler.pages.check_consistency()):
        print(f"  ! {note}", file=sys.stderr)
    if Handler.pages.dims:
        print(f"  coordinate space from lines.json for "
              f"{len(Handler.pages.dims)} pages")
    elif coord:
        print(f"  coordinate space stated: {coord[0]}x{coord[1]}")
    print(f"  http://{args.host}:{args.port}/healthz")
    print(f"  http://{args.host}:{args.port}/cropped/<id>g-<page>.jpg"
          f"?height=..&width=..&top_left_y=..&top_left_x=..")
    if args.pdf:
        print(f"  http://{args.host}:{args.port}/render/p<N>.png?dpi=400"
              f"   (topology-grade raster, on the fly)")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
