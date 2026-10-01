"""Execute a live script in GNU Octave and embed the results as MATLAB outputs."""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import matcompat
from .document import Document
from .outputs import Output, build_output_xml, code_line_numbers
from .package import MlxPackage
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


def run(mlx_in: str | Path, mlx_out: str | Path, octave: str = "octave", timeout: float | None = 1800,
        keep_workdir: bool = False, log=lambda msg: None) -> RunResult:
    mlx_in = Path(mlx_in).resolve()
    pkg = MlxPackage.read(mlx_in)
    doc = Document(pkg.document_xml)
    lines = doc.lines()
    sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
    regions = split_regions(lines, sections)
    result = RunResult(outputs=[])

    tmp = Path(tempfile.mkdtemp(prefix="mlxkit_"))
    work, fndir, out = tmp / "work", tmp / "functions", tmp / "out"
    for d in (work, fndir, out):
        d.mkdir()
    try:
        _prepare_workdir(mlx_in.parent, work, result.notes)
        for name, src in split_local_functions(lines, regions).items():
            (fndir / f"{name}.m").write_text(src)
        executable = [r for r in regions if not r.is_function]
        for r in executable:
            code = "\n".join(t or "" for t in lines[r.start_line:r.end_line + 1])
            (out / f"code_{r.number}.m").write_text(code + "\n")

        nums = " ".join(str(r.number) for r in executable)
        script = (f"addpath('{_q(OCTAVE_SUPPORT)}'); mlxkit_setup_compat('{_q(tmp / 'compat')}'); "
                  f"addpath('{_q(fndir)}'); "
                  "graphics_toolkit('qt'); set(0, 'defaultfigurevisible', 'off'); more off; "
                  "warning('off', 'Octave:latex-markup-not-supported-for-tick-marks'); "
                  "warning('off', 'Octave:LaTeX:internal-error'); "
                  f"mlxkit_run('{_q(out)}', [{nums}]);")
        log(f"running {len(executable)} regions in Octave...")
        proc = subprocess.run([octave, "--no-gui", "--quiet", "--norc", "--no-history", "--eval", script],
                              cwd=work, capture_output=True, text=True, timeout=timeout)
        result.octave_log = proc.stdout + proc.stderr
        result.outputs, result.error = _collect(regions, executable, out)
    finally:
        if keep_workdir:
            log(f"work directory kept at {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    layout = "inline"
    if pkg.output_xml and (m := re.search(rb"<layoutState>(\w+)</layoutState>", pkg.output_xml)):
        layout = m.group(1).decode()
    pkg.output_xml = build_output_xml(regions, result.outputs, code_line_numbers(lines), layout)
    pkg.write(mlx_out)
    return result


def _prepare_workdir(src: Path, work: Path, notes: list[str]) -> None:
    """Mirror the script's folder into the work directory, converting .mat files Octave can't read."""
    for entry in src.iterdir():
        target = work / entry.name
        if entry.suffix.lower() == ".mat" and matcompat.needs_conversion(entry):
            notes += matcompat.convert(entry, target)
        else:
            target.symlink_to(entry)


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


def _collect(regions: list[Region], executable: list[Region], out: Path) -> tuple[list[Output], str | None]:
    pending: list[tuple[int, int, Output]] = []  # (region, order, output)
    fig_regions: dict[int, list[int]] = {}
    error = None
    for r in executable:
        txt_file = out / f"{r.number}.txt"
        if not txt_file.exists():
            break  # not reached: an earlier region failed
        text = txt_file.read_text(errors="replace")
        body, warnings = _split_warnings(text)
        body_text = "".join(body).strip("\n")
        if body_text.strip():
            pending.append((r.number, len(pending), _text_output(r.number, body_text)))
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
            pending.append((regs[0], len(pending), Output("figure", regs, png=data, size=(w, h))))
    pending.sort(key=lambda p: (p[0], p[1]))
    return [p[2] for p in pending], error


_VAR_SCALAR = re.compile(r"^(\w+) = (\S.*)$")


def _text_output(region: int, text: str) -> Output:
    """Octave prints `x = 5`; MATLAB live scripts show that as a variable output."""
    m = _VAR_SCALAR.match(text)
    if m and "\n" not in text:
        return Output("variable", [region], name=m.group(1), text=m.group(2))
    return Output("text", [region], text=text + "\n")


def _q(path) -> str:
    return str(path).replace("'", "''")
