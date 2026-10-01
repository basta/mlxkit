"""`mlx serve`: a local notebook-style editor for live scripts in the browser.

Binds to 127.0.0.1 only and serves files under one root folder.

  GET  /                        the editor (web/index.html)
  GET  /api/files               .mlx files under the root
  GET  /api/doc?path=           cells + outputs of one file
  POST /api/save   {path, mtime, cells}         write edits into the .mlx
  POST /api/render {path, md}                   markdown -> HTML preview
  POST /api/run    {path, mtime?, cells?}       save (if cells given), then run in Octave
  GET  /api/job?id=             progress of a run
  POST /api/cancel {id}
"""
from __future__ import annotations

import json
import shutil
import threading
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .document import Document
from .livetext import cells_to_text, from_text, to_cells
from .outputs import build_output_xml, code_line_numbers
from .package import MlxPackage
from .regions import split_regions
from .render import _md_html, _read_outputs

WEB = Path(__file__).parent / "web"


class Conflict(Exception):
    pass


class App:
    def __init__(self, root: Path, octave: str = "octave"):
        self.root = root.resolve()
        self.octave = octave
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()  # one writer at a time

    # --- paths ---------------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError("path outside the served folder")
        if p.suffix.lower() != ".mlx":
            raise PermissionError("not an .mlx file")
        return p

    def files(self) -> list[dict]:
        out = []
        for p in sorted(self.root.rglob("*.mlx")):
            if any(part.startswith(".") for part in p.relative_to(self.root).parts):
                continue
            out.append({"path": str(p.relative_to(self.root)), "mtime": p.stat().st_mtime})
            if len(out) >= 1000:
                break
        return out

    # --- documents -------------------------------------------------------------

    def doc(self, rel: str) -> dict:
        path = self.resolve(rel)
        pkg = MlxPackage.read(path)
        doc = Document(pkg.document_xml)
        cells = to_cells(doc)
        title = path.stem
        for c in cells:
            if c["kind"] == "text":
                c["html"] = self._text_html(c["md"], pkg)
                if c["md"].startswith("# ") and title == path.stem:
                    title = c["md"][2:]
        # Attach each output to the code cell holding the last line of its regions.
        outputs, region_end = _read_outputs(pkg.output_xml)
        code_cells = [c for c in cells if c["kind"] == "code"]
        for c in code_cells:
            c["outputs"] = []
        for html, regions in outputs:
            ends = [region_end[r] for r in regions if r in region_end]
            if not ends:
                continue
            line = max(ends)
            for c in code_cells:
                n = c["code"].count("\n") + 1
                if c["first_line"] <= line < c["first_line"] + n:
                    c["outputs"].append({"line": line - c["first_line"], "html": html})
                    break
        return {"path": rel, "title": title, "mtime": path.stat().st_mtime, "cells": cells,
                "backup": path.with_name(path.name + ".bak").exists()}

    def _text_html(self, md: str, pkg) -> str:
        from .render import _block_html
        style = "ListParagraph" if md.startswith("- ") else None
        if style:
            return f"<ul><li>{_md_html(md[2:], pkg)}</li></ul>"
        return _block_html(None, md, pkg)

    def render(self, rel: str, md: str) -> str:
        pkg = MlxPackage.read(self.resolve(rel))
        return "\n".join(self._text_html(line, pkg) for line in md.split("\n") if line.strip()) or ""

    def save(self, rel: str, mtime: float | None, cells: list[dict]) -> dict:
        path = self.resolve(rel)
        if any(j["path"] == rel and j["state"] == "running" for j in self.jobs.values()):
            raise Conflict("A run is in progress for this file; wait for it to finish or cancel it.")
        with self.lock:
            if mtime is not None and abs(path.stat().st_mtime - mtime) > 1e-6:
                raise Conflict("The file changed on disk since it was opened. Reload to see the new version.")
            pkg = MlxPackage.read(path)
            base = Document(pkg.document_xml)
            doc = from_text(cells_to_text(cells), base)
            if doc.to_xml() != pkg.document_xml:
                code_changed = [b.code for b in doc.code_blocks] != [b.code for b in base.code_blocks]
                pkg.document_xml = doc.to_xml()
                if code_changed and pkg.output_xml is not None:
                    sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
                    pkg.output_xml = build_output_xml(split_regions(doc.lines(), sections), [],
                                                      code_line_numbers(doc.lines()))
                shutil.copy2(path, path.with_name(path.name + ".bak"))
                pkg.write(path)
        return self.doc(rel)

    # --- running -------------------------------------------------------------

    def start_run(self, rel: str) -> str:
        from .runner import Cancelled, run

        path = self.resolve(rel)
        job_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        job = {"id": job_id, "path": rel, "state": "running", "done": 0, "total": 0, "error": None,
               "notes": [], "log": "", "cancel": cancel}
        self.jobs[job_id] = job

        def progress(done, total):
            job["done"], job["total"] = done, total

        def work():
            try:
                with self.lock:
                    tmp_out = path.with_name(f".{path.stem}.mlxkit-run.mlx")
                    result = run(path, tmp_out, octave=self.octave, progress=progress, cancel=cancel)
                    shutil.copy2(path, path.with_name(path.name + ".bak"))
                    tmp_out.replace(path)
                job["notes"], job["error"], job["log"] = result.notes, result.error, result.octave_log[-20000:]
                job["state"] = "done"
            except Cancelled:
                job["state"] = "cancelled"
            except Exception as e:  # noqa: BLE001 - reported to the UI
                job["state"], job["error"] = "failed", f"{type(e).__name__}: {e}"
                job["log"] = traceback.format_exc()
            finally:
                for leftover in path.parent.glob(f".{path.stem}.mlxkit-run.mlx"):
                    leftover.unlink(missing_ok=True)

        threading.Thread(target=work, daemon=True).start()
        return job_id

    def job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return {k: v for k, v in job.items() if k != "cancel"}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, status, body: bytes, ctype="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=HTTPStatus.OK):
            self._send(status, json.dumps(obj).encode())

        def _error(self, status, msg):
            self._json({"error": msg}, status)

        def _dispatch(self, fn):
            try:
                fn()
            except PermissionError as e:
                self._error(HTTPStatus.FORBIDDEN, str(e))
            except FileNotFoundError as e:
                self._error(HTTPStatus.NOT_FOUND, str(e))
            except KeyError as e:
                self._error(HTTPStatus.NOT_FOUND, f"unknown: {e}")
            except Conflict as e:
                self._error(HTTPStatus.CONFLICT, str(e))
            except Exception as e:  # noqa: BLE001
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}

            def handle():
                if url.path in ("/", "/index.html"):
                    self._send(HTTPStatus.OK, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/files":
                    self._json({"root": str(app.root), "files": app.files()})
                elif url.path == "/api/doc":
                    self._json(app.doc(q["path"]))
                elif url.path == "/api/job":
                    self._json(app.job(q["id"]))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not found")

            self._dispatch(handle)

        def do_POST(self):
            url = urlparse(self.path)
            # Only accept requests from our own page (blocks cross-site form posts).
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected JSON")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

            def handle():
                if url.path == "/api/save":
                    self._json(app.save(body["path"], body.get("mtime"), body["cells"]))
                elif url.path == "/api/render":
                    self._json({"html": app.render(body["path"], body.get("md", ""))})
                elif url.path == "/api/run":
                    if body.get("cells") is not None:
                        app.save(body["path"], body.get("mtime"), body["cells"])
                    self._json({"id": app.start_run(body["path"])})
                elif url.path == "/api/cancel":
                    job = app.jobs[body["id"]]
                    job["cancel"].set()
                    self._json({"ok": True})
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not found")

            self._dispatch(handle)

    return Handler


def serve(root: Path, port: int = 8765, octave: str = "octave", open_browser: bool = True, file: str | None = None):
    app = App(root, octave)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if file:
        url += f"#{file}"
    print(f"mlx editor running at {url}  (serving {app.root}; Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
