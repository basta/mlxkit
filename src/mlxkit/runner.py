"""Execute a live script in GNU Octave and embed the results as MATLAB outputs."""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
import time
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import display, matcompat
from .document import Document
from .outputs import Output, build_output_xml, code_line_numbers
from .package import MlxPackage
from .transform import find_handle_vars, transform
from .regions import Region, _State, scan_line, split_regions

OCTAVE_SUPPORT = Path(__file__).parent / "octave"
_FUNC_NAME = re.compile(r"^\s*function\s+(?:(?:\[[^\]]*\]|\w+)\s*=\s*)?([A-Za-z]\w*)")


@dataclass
class RunResult:
    outputs: list[Output]
    error: str | None = None  # first error, if execution stopped early
    notes: list[str] = field(default_factory=list)
    octave_log: str = ""


def split_local_functions(lines: list[str | None], regions: list[Region]) -> dict[str, str]:
    """Local functions at the end of the script, as {name: source} (one file each for Octave)."""
    fn_regions = [r for r in regions if r.is_function]
    if not fn_regions:
        return {}
    funcs, cur, st = {}, [], _State()
    for ln in range(fn_regions[0].start_line, fn_regions[-1].end_line + 1):
        text = lines[ln]
        if text is None:
            continue
        if not cur and not text.strip():
            continue
        cur.append(text)
        scan_line(text, st)
        if not st.blocks and not st.open:
            m = next((_FUNC_NAME.match(l) for l in cur if _FUNC_NAME.match(l)), None)
            if m:
                funcs[m.group(1)] = "\n".join(cur) + "\n"
            cur = []
    return funcs


class Cancelled(Exception):
    pass


@dataclass
class Workspace:
    """Temporary folders for running one script: a mirror of its folder, local functions, outputs."""
    script: Path
    tmp: Path
    work: Path
    functions: Path
    extra_path: list[Path]
    notes: list[str]

    @classmethod
    def create(cls, script: Path) -> "Workspace":
        tmp = Path(tempfile.mkdtemp(prefix="mlxkit_"))
        work, fndir = tmp / "work", tmp / "functions"
        work.mkdir()
        fndir.mkdir()
        notes: list[str] = []
        extra = _prepare_workdir(script.parent, work, notes)
        return cls(script, tmp, work, fndir, extra, notes)

    def setup_commands(self) -> str:
        """Octave commands that prepare a fresh session (paths, shims, headless graphics)."""
        return (f"addpath('{_q(OCTAVE_SUPPORT)}'); mlxkit_setup_compat('{_q(self.tmp / 'compat')}'); "
                + "".join(f"addpath('{_q(p)}', '-end'); " for p in self.extra_path[1:])
                + (f"addpath('{_q(self.extra_path[0])}'); " if self.extra_path else "")
                + f"addpath('{_q(self.functions)}'); "
                + f"mlxkit_project_root('{_q(find_project_root(self.script.parent) or self.script.parent)}'); "
                "graphics_toolkit('qt'); set(0, 'defaultfigurevisible', 'off'); more off; "
                "warning('off', 'Octave:latex-markup-not-supported-for-tick-marks'); "
                "warning('off', 'Octave:LaTeX:internal-error'); ")

    def write_functions(self, lines, regions, handles) -> set[str]:
        """(Re)write local function files; returns names that changed or disappeared."""
        funcs = {name: transform(src, handles) for name, src in split_local_functions(lines, regions).items()}
        changed = set()
        for f in self.functions.glob("*.m"):
            if f.stem not in funcs:
                f.unlink()
                changed.add(f.stem)
        for name, src in funcs.items():
            f = self.functions / f"{name}.m"
            if not f.exists() or f.read_text() != src:
                f.write_text(src)
                changed.add(name)
        return changed

    def new_outdir(self) -> Path:
        out = Path(tempfile.mkdtemp(prefix="run_", dir=self.tmp))
        return out

    @staticmethod
    def write_code(out: Path, lines, regions_to_run, handles) -> None:
        for r in regions_to_run:
            code = "\n".join(t or "" for t in lines[r.start_line:r.end_line + 1])
            (out / f"code_{r.number}.m").write_text(transform(code, handles) + "\n")

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def parse_script(doc: Document):
    lines = doc.lines()
    sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
    regions = split_regions(lines, sections)
    handles = find_handle_vars("\n".join(t or "" for t in lines))
    return lines, regions, handles


def run(mlx_in: str | Path, mlx_out: str | Path, octave: str = "octave", timeout: float | None = 1800,
        keep_workdir: bool = False, log=lambda msg: None, progress=lambda done, total: None,
        cancel: threading.Event | None = None) -> RunResult:
    """Run a live script in a fresh Octave and write it, with outputs, to mlx_out.

    progress(done, total) is called as regions finish; setting `cancel` stops Octave.
    """
    mlx_in = Path(mlx_in).resolve()
    pkg = MlxPackage.read(mlx_in)
    lines, regions, handles = parse_script(Document(pkg.document_xml))
    result = RunResult(outputs=[])
    ws = Workspace.create(mlx_in)
    result.notes = ws.notes
    try:
        ws.write_functions(lines, regions, handles)
        executable = [r for r in regions if not r.is_function]
        out = ws.new_outdir()
        ws.write_code(out, lines, executable, handles)
        nums = " ".join(str(r.number) for r in executable)
        script = ws.setup_commands() + f"mlxkit_run('{_q(out)}', [{nums}]);"
        log(f"running {len(executable)} regions in Octave...")
        result.octave_log = _run_octave([octave, "--no-gui", "--quiet", "--norc", "--no-history", "--eval", script],
                                        ws.work, out, len(executable), timeout, progress, cancel)
        result.outputs, result.error = collect(executable, out)
    finally:
        if keep_workdir:
            log(f"work directory kept at {ws.tmp}")
        else:
            ws.cleanup()

    layout = "inline"
    if pkg.output_xml and (m := re.search(rb"<layoutState>(\w+)</layoutState>", pkg.output_xml)):
        layout = m.group(1).decode()
    pkg.output_xml = build_output_xml(regions, result.outputs, code_line_numbers(lines), layout)
    pkg.write(mlx_out)
    return result


def _run_octave(cmd, cwd, out: Path, total: int, timeout, progress, cancel) -> str:
    log_file = out / "octave.log"
    with open(log_file, "w") as log:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=_octave_env())
        start, done = time.monotonic(), -1
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.wait(0.25):
                    raise Cancelled()
                if cancel is None:
                    time.sleep(0.25)
                if timeout and time.monotonic() - start > timeout:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                now = sum(1 for _ in out.glob("*.figs"))
                if now != done:
                    done = now
                    progress(done, total)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    progress(total, total)
    return log_file.read_text(errors="replace")


def _octave_env() -> dict[str, str]:
    env = dict(os.environ)
    # Octave's symbolic package runs SymPy through $PYTHON; use ours if it has SymPy.
    if "PYTHON" not in env and importlib.util.find_spec("sympy") is not None:
        env["PYTHON"] = sys.executable
    return env


def find_project_root(folder: Path) -> Path | None:
    """The enclosing MATLAB project (a folder with a .prj file), if any."""
    for d in [folder, *folder.parents][:6]:
        if any(d.glob("*.prj")):
            return d
    return None


def _prepare_workdir(src: Path, work: Path, notes: list[str]) -> list[Path]:
    """Mirror the script's folder (or its MATLAB project) into the work directory.

    Returns extra folders to put on the Octave path. Inside a MATLAB project,
    MATLAB starts in the project root with every project folder on the path,
    so we do the same.
    """
    root = find_project_root(src) or src
    if root != src:
        notes.append(f"MATLAB project at {root}: running from its root with all project folders on the path")
    converted = work.parent / "converted"
    converted.mkdir(exist_ok=True)
    for entry in root.iterdir():
        target = work / entry.name
        if entry.suffix.lower() == ".mat" and matcompat.needs_conversion(entry):
            notes += matcompat.convert(entry, target)
        else:
            target.symlink_to(entry)
    # Helper .m files using syntax Octave lacks get rewritten copies, first on the path.
    for mfile in root.rglob("*.m"):
        if ".git" in mfile.parts or mfile.stat().st_size > 2_000_000:
            continue
        try:
            code = mfile.read_text()
        except UnicodeDecodeError:
            continue
        new = transform(code)
        if new != code and not (converted / mfile.name).exists():
            (converted / mfile.name).write_text(new)
            notes.append(f"rewrote {mfile.relative_to(root)} for Octave (newer MATLAB syntax)")
    if root == src:
        return [converted]
    # .mat files elsewhere in the project: converted copies go first on the path.
    for mat in root.rglob("*.mat"):
        if ".git" in mat.parts or mat.parent == root or mat.stat().st_size > 200_000_000:
            continue
        if matcompat.needs_conversion(mat) and not (converted / mat.name).exists():
            notes += matcompat.convert(mat, converted / mat.name)
    folders = [d for d in [root, *root.rglob("*")] if d.is_dir() and _on_project_path(d, root)]
    return [converted, *folders]


def _on_project_path(d: Path, root: Path) -> bool:
    parts = d.relative_to(root).parts
    return not any(p.startswith((".", "+", "@")) or p in ("private", "resources", "__pycache__") for p in parts)


_WARNING = re.compile(r"^warning: (.*)$")


def _split_warnings(text: str) -> tuple[list[str], list[str]]:
    """Separate Octave warnings (and their `called from` stack traces) from regular output."""
    body, warnings = [], []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        m = _WARNING.match(lines[i].rstrip("\n"))
        if not m:
            body.append(lines[i])
            i += 1
            continue
        if m.group(1) != "called from":
            warnings.append(m.group(1))
        i += 1
        while i < len(lines) and lines[i].startswith("    "):
            i += 1
        if i < len(lines) and not lines[i].strip() and m.group(1) == "called from":
            i += 1
    return body, warnings


def collect(executable: list[Region], out: Path) -> tuple[list[Output], str | None]:
    """Outputs of the regions that ran (in order) from Octave's per-region files in `out`."""
    pending: list[tuple[int, int, Output]] = []  # (region, order, output)
    fig_regions: dict[int, list[int]] = {}
    error = None
    for r in executable:
        txt_file = out / f"{r.number}.txt"
        if not txt_file.exists():
            break  # not reached: an earlier region failed
        text = txt_file.read_text(errors="replace")
        body, warnings = _split_warnings(text)
        vars_file = out / f"{r.number}.vars"
        variables = display.parse_vars(vars_file.read_text()) if vars_file.exists() else {}
        for o in display.convert("".join(body), r.number, variables):
            pending.append((r.number, len(pending), o))
        for w in warnings:
            pending.append((r.number, len(pending), Output("warning", [r.number], text=f"Warning: {w}")))
        figs = (out / f"{r.number}.figs").read_text().split()
        for h in figs:
            fig_regions.setdefault(int(h), []).append(r.number)
        err_file = out / f"{r.number}.err"
        if err_file.exists():
            error = err_file.read_text().strip()
            pending.append((r.number, len(pending), Output("error", [r.number], text=error)))
            break
    for handle, regs in fig_regions.items():
        png = out / f"fig_{handle}.png"
        if png.exists():
            data = png.read_bytes()
            w, h = struct.unpack(">II", data[16:24])
            pending.append((regs[0], len(pending), Output("figure", regs, png=data, size=(w, h),
                                                          extra={"handle": handle})))
    pending.sort(key=lambda p: (p[0], p[1]))
    return [p[2] for p in pending], error


def _q(path) -> str:
    return str(path).replace("'", "''")
