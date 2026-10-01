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
from .outputs import build_output_xml, code_line_numbers, parse_output_xml, remap_outputs
from .package import MlxPackage
from .render import _md_html, _read_outputs
from .runner import parse_script

WEB = Path(__file__).parent / "web"


class Conflict(Exception):
    pass


class App:
    def __init__(self, root: Path, octave: str = "octave"):
        self.root = root.resolve()
        self.octave = octave
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()  # one writer at a time
        self.sessions: dict[str, "Session"] = {}

    def session(self, rel: str):
        from .session import Session

        if rel not in self.sessions:
            self.sessions[rel] = Session(self.resolve(rel), self.octave)
        return self.sessions[rel]

    def close_all(self) -> None:
        for sess in self.sessions.values():
            sess.close()

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
        sess = self.sessions.get(rel)
        info = {"alive": False, "busy": False, "variables": []}
        if sess is not None and sess.alive:
            info = {"alive": True, "busy": sess.busy, "variables": sess.variables}
            lines, regions, _ = parse_script(doc)
            for c, b in zip(cells, doc.blocks):
                if c["kind"] != "code":
                    continue
                inside = [r for r in regions if not r.is_function
                          and b.first_line <= r.start_line < b.first_line + b.line_count]
                c["ran"] = bool(inside) and all(sess.has_run(lines, r) for r in inside)
        return {"path": rel, "title": title, "mtime": path.stat().st_mtime, "cells": cells,
                "backup": path.with_name(path.name + ".bak").exists(), "session": info}

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
        if any(j["path"] == rel and j["state"] == "running" for j in self.jobs.values()) or (
                rel in self.sessions and self.sessions[rel].busy):
            raise Conflict("A run is in progress for this file; wait for it to finish or cancel it.")
        with self.lock:
            if mtime is not None and abs(path.stat().st_mtime - mtime) > 1e-6:
                raise Conflict("The file changed on disk since it was opened. Reload to see the new version.")
            pkg = MlxPackage.read(path)
            base = Document(pkg.document_xml)
            doc = from_text(cells_to_text(cells), base)
            if doc.to_xml() != pkg.document_xml:
                pkg.document_xml = doc.to_xml()
                if pkg.output_xml is not None:
                    pkg.output_xml = carry_outputs(pkg.output_xml, base, doc)
                shutil.copy2(path, path.with_name(path.name + ".bak"))
                pkg.write(path)
        return self.doc(rel)

    # --- running -------------------------------------------------------------

    def start_run(self, rel: str, mode: str = "all", cell: int | None = None) -> str:
        """Run in the file's Octave session: everything (fresh workspace), the
        section holding `cell`, or every statement up to the end of `cell`."""
        from .runner import Cancelled

        path = self.resolve(rel)
        sess = self.session(rel)
        if sess.busy:
            raise Conflict("Something is already running in this file's session.")
        job_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        job = {"id": job_id, "path": rel, "state": "running", "done": 0, "total": 0, "error": None,
               "notes": [], "log": "", "cancel": cancel, "mode": mode}
        self.jobs[job_id] = job

        def progress(done, total):
            job["done"], job["total"] = done, total

        def work():
            try:
                doc = Document(MlxPackage.read(path).document_xml)
                lines, regions, handles = parse_script(doc)
                numbers = select_regions(doc, regions, mode, cell)
                if mode == "all" or not sess.alive:
                    job["phase"] = "starting Octave"
                    sess.restart() if mode == "all" else sess.start()
                job["phase"] = "running"
                start_log = len(sess.log)
                result = sess.execute(lines, regions, numbers, handles, progress=progress, cancel=cancel)
                with self.lock:
                    pkg = MlxPackage.read(path)
                    if Document(pkg.document_xml).to_xml() != doc.to_xml():
                        raise Conflict("The file changed while running; outputs were not saved.")
                    existing = [] if mode == "all" else parse_output_xml(pkg.output_xml)
                    outputs = merge_outputs(existing, result.outputs, set(numbers), sess.figure_ids)
                    layout = "inline"
                    pkg.output_xml = build_output_xml(regions, outputs, code_line_numbers(lines), layout)
                    pkg.write(path)
                job["notes"], job["error"] = sess.notes, result.error
                job["log"] = "\n".join(sess.log[start_log:])[-20000:]
                job["ran"] = len(result.executed)
                job["state"] = "cancelled" if result.cancelled else "done"
            except Cancelled:
                job["state"] = "cancelled"
            except Exception as e:  # noqa: BLE001 - reported to the UI
                job["state"], job["error"] = "failed", f"{type(e).__name__}: {e}"
                job["log"] = traceback.format_exc()

        threading.Thread(target=work, daemon=True).start()
        return job_id

    def restart(self, rel: str) -> None:
        sess = self.session(rel)
        if sess.busy:
            sess.interrupt()
        sess.close()

    def job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return {k: v for k, v in job.items() if k != "cancel"}


def select_regions(doc: Document, regions, mode: str, cell: int | None) -> list[int]:
    """Region numbers to run. `cell` indexes doc.blocks (cells and blocks match after a save)."""
    runnable = [r for r in regions if not r.is_function]
    if mode == "all" or cell is None:
        return [r.number for r in runnable]
    block = doc.blocks[cell]
    if mode == "upto":
        end = block.first_line + block.line_count
        return [r.number for r in runnable if r.start_line < end]
    # section: between the section breaks around the cell
    breaks = [b.first_line for b in doc.blocks if b.kind == "sectionbreak"]
    lo = max([ln for ln in breaks if ln <= block.first_line], default=-1)
    hi = min([ln for ln in breaks if ln > block.first_line], default=10**9)
    return [r.number for r in runnable if lo < r.start_line < hi]


def merge_outputs(existing, new, requested: set[int], figure_ids: dict[int, str]):
    """Replace the outputs of the statements that were run; keep everything else.

    A figure drawn again by this run (same Octave figure) replaces its old
    picture and keeps the old picture's other regions.
    """
    new_figs = {o.extra["handle"]: o for o in new if o.kind == "figure"}
    by_id = {fid: h for h, fid in figure_ids.items()}
    kept = []
    for o in existing:
        if o.kind == "figure":
            h = by_id.get(o.extra.get("id"))
            if h in new_figs:
                fresh = new_figs[h]
                fresh.regions = sorted({r for r in o.regions if r not in requested} | set(fresh.regions))
                continue
            o.regions = [r for r in o.regions if r not in requested]
            if o.regions:
                kept.append(o)
        elif not any(r in requested for r in o.regions):
            kept.append(o)
    for h, o in new_figs.items():
        o.extra["id"] = figure_ids.setdefault(h, str(uuid.uuid4()))
    merged = kept + list(new)
    merged.sort(key=lambda o: min(o.regions) if o.regions else 0)
    return merged


def carry_outputs(output_xml: bytes, old: Document, new: Document) -> bytes:
    """output.xml for an edited document: outputs of unchanged statements stay."""
    old_lines, old_regions, _ = parse_script(old)
    new_lines, new_regions, _ = parse_script(new)
    outputs = remap_outputs(parse_output_xml(output_xml), old_lines, old_regions, new_lines, new_regions)
    return build_output_xml(new_regions, outputs, code_line_numbers(new_lines))


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
                    self._json({"id": app.start_run(body["path"], body.get("mode", "all"), body.get("cell"))})
                elif url.path == "/api/cancel":
                    job = app.jobs[body["id"]]
                    job["cancel"].set()
                    self._json({"ok": True})
                elif url.path == "/api/restart":
                    app.restart(body["path"])
                    self._json({"ok": True})
                else:
                    self._error(HTTPStatus.NOT_FOUND, "not found")

            self._dispatch(handle)

    return Handler


def serve(root: Path, port: int = 8765, octave: str = "octave", open_browser: bool = True, file: str | None = None):
    app = App(root, octave)
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), make_handler(app))
            break
        except OSError:
            continue  # port in use (maybe another `mlx serve`): try the next one
    else:
        raise SystemExit(f"no free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if file:
        url += f"#{file}"
    print(f"mlx editor running at {url}  (serving {app.root}; Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        app.close_all()
        server.server_close()
