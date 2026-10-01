"""A long-lived Octave session for one live script, like a notebook kernel.

Statements run in a persistent workspace, so a section can be run on its own
using variables from earlier sections, as in MATLAB's Live Editor.

Octave runs interactively with its stdin on a pipe. Each request is two input
lines: the work, then `disp('<sentinel>')`. An interrupt (SIGINT) aborts the
work line but keeps the session and its variables; the sentinel line still
runs, so we always know when a request is over.
"""
from __future__ import annotations

import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .outputs import Output
from .regions import Region
from .runner import Cancelled, Workspace, _octave_env, _q, collect


class SessionDied(Exception):
    pass


@dataclass
class ExecResult:
    outputs: list[Output]
    executed: list[int]  # region numbers that ran to completion (or failed)
    error: str | None = None
    cancelled: bool = False
    variables: list[dict] = field(default_factory=list)


class Session:
    def __init__(self, script: Path, octave: str = "octave"):
        self.script = Path(script).resolve()
        self.octave = octave
        self.ws: Workspace | None = None
        self.proc: subprocess.Popen | None = None
        self.log: list[str] = []
        self._waiting: dict[str, threading.Event] = {}
        self.lock = threading.Lock()  # one request at a time
        self.figure_ids: dict[int, str] = {}  # Octave figure handle -> figureId in output.xml
        self.ran: set[str] = set()  # code of statements run in this session
        self.variables: list[dict] = []
        self.busy = False

    # --- lifecycle ---------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        self.ws = Workspace.create(self.script)
        self.proc = subprocess.Popen(
            [self.octave, "--no-gui", "--quiet", "--norc", "--no-history", "--interactive"],
            cwd=self.ws.work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=_octave_env(),
            start_new_session=True)  # own process group: Ctrl+C / Jupyter interrupts reach Octave only through us
        threading.Thread(target=self._read, daemon=True).start()
        self._request("PS1(''); PS2(''); " + self.ws.setup_commands(), timeout=120)

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.write("exit\n")
                self.proc.stdin.flush()
                self.proc.wait(3)
            except Exception:  # noqa: BLE001
                self.proc.kill()
        self.proc = None
        if self.ws is not None:
            self.ws.cleanup()
            self.ws = None
        self.figure_ids.clear()
        self.ran.clear()
        self.variables = []

    def restart(self) -> None:
        self.close()
        self.start()

    def interrupt(self) -> None:
        if self.alive:
            self.proc.send_signal(signal.SIGINT)

    @property
    def notes(self) -> list[str]:
        return self.ws.notes if self.ws else []

    # --- protocol ------------------------------------------------------------

    def _read(self) -> None:
        proc = self.proc
        for line in proc.stdout:
            line = line.rstrip("\n")
            if "MLXKIT_DONE " in line:  # may follow a prompt, e.g. "octave:1> MLXKIT_DONE ..."
                ev = self._waiting.pop(line.split("MLXKIT_DONE ", 1)[1].split()[0], None)
                if ev:
                    ev.set()
                continue
            self.log.append(line)
            del self.log[:-2000]
        for ev in list(self._waiting.values()):  # process exited: wake everyone up
            ev.set()

    def _request(self, command: str, timeout: float | None = None, cancel: threading.Event | None = None,
                 on_tick=None) -> None:
        if not self.alive:
            raise SessionDied("the Octave session is not running")
        token = uuid.uuid4().hex
        done = threading.Event()
        self._waiting[token] = done
        self.proc.stdin.write(command.replace("\n", " ") + "\n")
        self.proc.stdin.write(f"disp('MLXKIT_DONE {token}')\n")
        self.proc.stdin.flush()
        start, interrupted = time.monotonic(), False
        while True:
            try:
                if done.wait(0.2):
                    break
            except KeyboardInterrupt:  # e.g. Jupyter's interrupt reaching this thread
                if not interrupted:
                    self.interrupt()
                    interrupted = True
                continue
            if on_tick:
                on_tick()
            if not self.alive:
                raise SessionDied("Octave exited unexpectedly:\n" + "\n".join(self.log[-30:]))
            if (cancel is not None and cancel.is_set()) or (timeout and time.monotonic() - start > timeout):
                if not interrupted:
                    self.interrupt()
                    interrupted = True
        if not self.alive:
            raise SessionDied("Octave exited unexpectedly:\n" + "\n".join(self.log[-30:]))
        if interrupted:
            raise Cancelled()

    # --- running code ---------------------------------------------------------

    def execute(self, lines: list[str | None], regions: list[Region], numbers: list[int], handles: set[str],
                progress=lambda done, total: None, cancel: threading.Event | None = None,
                on_output=None, whole_script: bool = True) -> ExecResult:
        """Run the given regions (in order) in the session's workspace.

        on_output(Output) is called as soon as each statement's printed output
        is available (figures come at the end). whole_script=False means the
        code is a fragment (a notebook cell): local functions it doesn't define
        are kept.
        """
        with self.lock:
            self.busy = True
            try:
                return self._execute(lines, regions, numbers, handles, progress, cancel, on_output, whole_script)
            finally:
                self.busy = False

    def evaluate(self, code: str, timeout: float = 30) -> str:
        """Run a short command in the workspace and return what it printed (for help/completion)."""
        with self.lock:
            if not self.alive:
                self.close()
                self.start()
            out = self.ws.new_outdir()
            (out / "code.m").write_text(code)
            try:
                self._request(f"mlxkit_capture('{_q(out)}');", timeout=timeout)
            except Cancelled:
                return ""
            f = out / "result.txt"
            return f.read_text(errors="replace") if f.exists() else ""

    def _execute(self, lines, regions, numbers, handles, progress, cancel, on_output, whole_script) -> ExecResult:
        if not self.alive:
            self.close()
            self.start()
        ws = self.ws
        changed = ws.write_functions(lines, regions, handles, replace_all=whole_script)
        to_run = [regions[n] for n in numbers if not regions[n].is_function]
        out = ws.new_outdir()
        ws.write_code(out, lines, to_run, handles)
        nums = " ".join(str(r.number) for r in to_run)
        clear = f"clear {' '.join(sorted(changed))}; rehash(); " if changed else ""
        total = len(to_run)

        emitted: set[int] = set()

        def stream():
            # Statements finish in order; pass on the output of each one that's done.
            for r in to_run:
                if r.number in emitted:
                    continue
                if not (out / f"{r.number}.figs").exists():
                    break
                emitted.add(r.number)
                if on_output:
                    for o in collect([r], out)[0]:
                        if o.kind != "figure":
                            on_output(o)

        def tick():
            progress(sum(1 for _ in out.glob("*.figs")), total)
            stream()

        cancelled = False
        try:
            self._request(f"{clear}mlxkit_exec('{_q(out)}', [{nums}]);", cancel=cancel, on_tick=tick)
        except Cancelled:
            cancelled = True
            # Save what the finished statements drew, and the workspace as it is now.
            handles_touched = sorted({h for f in out.glob("*.figs") for h in f.read_text().split()})
            self._request(f"mlxkit_print_figures('{_q(out)}', [{' '.join(handles_touched)}]); "
                          f"mlxkit_who('{_q(out / 'workspace.tsv')}');", timeout=60)
        progress(total, total)
        stream()

        outputs, error = collect(to_run, out)
        if on_output:
            for o in outputs:
                if o.kind == "figure" or o.regions[0] not in emitted:
                    on_output(o)
        executed = [r.number for r in to_run if (out / f"{r.number}.figs").exists()]
        for n in executed:
            self.ran.add(_signature(lines, regions[n]))
        if error is not None and executed:
            self.ran.discard(_signature(lines, regions[executed[-1]]))
        self.variables = _read_workspace(out / "workspace.tsv")
        return ExecResult(outputs, executed, error, cancelled, self.variables)

    def has_run(self, lines, region: Region) -> bool:
        return _signature(lines, region) in self.ran


def _signature(lines, r: Region) -> str:
    return "\n".join(t or "" for t in lines[r.start_line:r.end_line + 1]).strip()


def _read_workspace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out.append({"name": parts[0], "class": parts[1], "size": parts[2].replace("x", "×")})
    return out
