"""A Jupyter kernel that runs MATLAB code in GNU Octave through mlxkit.

Each cell runs in one persistent Octave session with mlxkit's compatibility
layer (newer MATLAB syntax rewrites, .mat conversion, shims). Install with
`mlx kernel install`; then pick "MATLAB (Octave · mlxkit)" in JupyterLab or VS Code.
"""
from __future__ import annotations

import base64
import re
import threading
from pathlib import Path

from ipykernel.kernelbase import Kernel

from . import __version__
from .outputs import Output, output_text
from .regions import split_regions
from .runner import Cancelled
from .session import Session, SessionDied
from .transform import find_handle_vars

KEYWORDS = ["break", "case", "catch", "classdef", "continue", "else", "elseif", "end", "for", "function",
            "global", "if", "otherwise", "parfor", "persistent", "return", "switch", "try", "while"]


class MlxKernel(Kernel):
    implementation = "mlxkit"
    implementation_version = __version__
    banner = "MATLAB code in GNU Octave, via mlxkit"
    language_info = {
        "name": "matlab",
        "mimetype": "text/x-matlab",
        "file_extension": ".m",
        "codemirror_mode": "octave",
        "pygments_lexer": "matlab",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # The session mirrors the folder the notebook lives in (Jupyter starts kernels there).
        self.octave = Session(Path.cwd() / "notebook.mlx")
        self.history: list[str] = []  # code of cells run so far, to know which variables hold graphics handles

    # --- execution -----------------------------------------------------------

    def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False):
        if not code.strip():
            return self._ok()
        lines = code.split("\n")
        regions = split_regions(lines)
        self.history.append(code)
        handles = find_handle_vars("\n".join(self.history))
        cancel = threading.Event()
        result, failure = {}, {}
        self._regions = regions

        def work():
            try:
                result["r"] = self.octave.execute(lines, regions, [r.number for r in regions], handles,
                                                   cancel=cancel, whole_script=False,
                                                   on_output=None if silent else self._publish)
            except BaseException as e:  # noqa: BLE001 - shown in the notebook (incl. stray KeyboardInterrupt)
                failure["e"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()
        while True:
            try:
                t.join(0.1)
                if not t.is_alive():
                    break
            except KeyboardInterrupt:  # Jupyter's interrupt: stop Octave, keep the workspace
                cancel.set()
        if "r" not in result and "e" not in failure:
            failure["e"] = Cancelled()
        if "e" in failure:
            e = failure["e"]
            if isinstance(e, (KeyboardInterrupt, Cancelled)):
                return self._error("KeyboardInterrupt", "interrupted (the workspace is kept)")
            if isinstance(e, SessionDied):
                self._stream("stderr", f"{e}\nThe workspace was lost; the next cell starts a fresh Octave.\n")
            return self._error(type(e).__name__, str(e))
        r = result["r"]
        if r.cancelled:
            return self._error("KeyboardInterrupt", "interrupted (the workspace is kept)")
        if r.error:
            return self._error("OctaveError", r.error)
        return self._ok()

    def _publish(self, o: Output) -> None:
        # Outputs carry where they came from and their MATLAB type, so a notebook
        # can be turned back into an .mlx with every output on its line.
        if o.kind == "error":
            return  # shown by Jupyter's own error message (see _error)
        ends = [self._regions[r].end_line for r in o.regions]
        meta = {"line": max(ends), "kind": o.kind}
        if o.kind == "figure":
            meta["lines"] = ends  # every statement that drew on it
        if o.kind == "figure":
            w, h = o.size
            self.send_response(self.iopub_socket, "display_data", {
                "data": {"image/png": base64.b64encode(o.png).decode(), "text/plain": f"<Figure {w}x{h}>"},
                "metadata": {"image/png": {"width": w, "height": h}, "mlxkit": meta},
            })
            return
        if o.kind in ("variable", "matrix", "variableString"):
            meta.update(name=o.name, text=o.text, header=o.header, rows=o.rows, columns=o.columns,
                        var_type=o.var_type)
        text = output_text(o)
        self.send_response(self.iopub_socket, "display_data", {
            "data": {"text/plain": text.rstrip("\n")}, "metadata": {"mlxkit": meta}})

    def _stream(self, name: str, text: str) -> None:
        self.send_response(self.iopub_socket, "stream", {"name": name, "text": text})

    def _ok(self):
        return {"status": "ok", "execution_count": self.execution_count, "payload": [], "user_expressions": {}}

    def _error(self, ename: str, evalue: str):
        tb = [f"\x1b[31mError: {evalue}\x1b[0m"]
        self.send_response(self.iopub_socket, "error", {"ename": ename, "evalue": evalue, "traceback": tb})
        return {"status": "error", "execution_count": self.execution_count, "ename": ename, "evalue": evalue,
                "traceback": tb}

    # --- completion and help -----------------------------------------------------

    def do_complete(self, code, cursor_pos):
        m = re.search(r"[A-Za-z_]\w*$", code[:cursor_pos])
        prefix = m.group(0) if m else ""
        start = cursor_pos - len(prefix)
        names = {v["name"] for v in self.octave.variables} | set(KEYWORDS)
        names |= set(re.findall(r"\b[A-Za-z_]\w*\b", "\n".join(self.history[-50:])))
        if len(prefix) >= 2 and self.octave.alive and not self.octave.busy:
            listing = self.octave.evaluate(f"x__ = completion_matches('{prefix}'); disp(x__); clear x__")
            names |= {n.strip() for n in listing.splitlines() if n.strip()}
        matches = sorted(n for n in names if n.startswith(prefix) and n != prefix)
        return {"status": "ok", "matches": matches[:200], "cursor_start": start, "cursor_end": cursor_pos,
                "metadata": {}}

    def do_inspect(self, code, cursor_pos, detail_level=0, omit_sections=()):
        left = re.search(r"[A-Za-z_]\w*$", code[:cursor_pos])
        right = re.match(r"\w*", code[cursor_pos:])
        name = (left.group(0) if left else "") + (right.group(0) if right else "")
        if not name or self.octave.busy:
            return {"status": "ok", "found": False, "data": {}, "metadata": {}}
        text = self.octave.evaluate(f"help {name}")
        found = bool(text.strip()) and not text.startswith("error:")
        return {"status": "ok", "found": found, "data": {"text/plain": text} if found else {}, "metadata": {}}

    def do_shutdown(self, restart):
        self.octave.close()
        return {"status": "ok", "restart": restart}


def install(user: bool = True, prefix: str | None = None) -> str:
    """Register the kernel with Jupyter. Returns the install location."""
    import json
    import sys
    import tempfile

    from jupyter_client.kernelspec import KernelSpecManager

    spec = {
        "argv": [sys.executable, "-m", "mlxkit.kernel", "-f", "{connection_file}"],
        "display_name": "MATLAB (Octave · mlxkit)",
        "language": "matlab",
        "interrupt_mode": "signal",
        "metadata": {"debugger": False},
    }
    with tempfile.TemporaryDirectory() as d:
        Path(d, "kernel.json").write_text(json.dumps(spec, indent=1))
        return KernelSpecManager().install_kernel_spec(d, "mlxkit", user=user, prefix=prefix)


def uninstall() -> None:
    from jupyter_client.kernelspec import KernelSpecManager

    KernelSpecManager().remove_kernel_spec("mlxkit")


if __name__ == "__main__":
    from ipykernel.kernelapp import IPKernelApp

    IPKernelApp.launch_instance(kernel_class=MlxKernel)
