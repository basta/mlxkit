"""Turn Octave's command-window text into MATLAB live script outputs.

Octave prints `x = 42`, or `x =` followed by an indented block, whenever a
statement isn't terminated by a semicolon. MATLAB's Live Editor shows those as
typed variable outputs, so we cut the text into variable displays and plain
text. Each displayed variable's class and size come from Octave (`<k>.vars`).
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from .outputs import Output

_SCALAR = re.compile(r"^(\w+) = (.*)$")
_BLOCK = re.compile(r"^(\w+) =$")


@dataclass
class VarInfo:
    cls: str
    size: tuple[int, ...]


def parse_vars(text: str) -> dict[str, VarInfo]:
    out = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = VarInfo(parts[1], tuple(int(x) for x in parts[2].split("x") if x.isdigit()))
    return out


def convert(text: str, region: int, vars: dict[str, VarInfo]) -> list[Output]:
    """Split one region's printed text into text and variable outputs, in order."""
    lines = text.splitlines()
    outputs: list[Output] = []
    plain: list[str] = []

    def flush_plain():
        body = "\n".join(plain).strip("\n")
        if body.strip():
            outputs.append(Output("text", [region], text=body + "\n"))
        plain.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _BLOCK.match(line)
        if m and m.group(1) in vars:
            j = i + 1
            while j < len(lines) and not (lines[j] and not lines[j][0].isspace() and lines[j][0] not in "{}"):
                j += 1
            body = "\n".join(lines[i + 1:j]).strip("\n")
            flush_plain()
            outputs.append(_block_output(m.group(1), body, vars[m.group(1)], region))
            i = j
            continue
        m = _SCALAR.match(line)
        if m and m.group(1) in vars:
            flush_plain()
            outputs.append(_scalar_output(m.group(1), m.group(2), vars[m.group(1)], region))
            i += 1
            continue
        plain.append(line)
        i += 1
    flush_plain()
    return outputs


def _scalar_output(name: str, value: str, info: VarInfo, region: int) -> Output:
    if info.cls == "char":
        value = f"'{value}'"
    elif info.cls == "string":
        value = f'"{value}"'
    rows, cols = (info.size + (1, 1))[:2]
    return Output("variable", [region], name=name, text=value, rows=rows, columns=cols)


def _block_output(name: str, body: str, info: VarInfo, region: int) -> Output:
    rows, cols = (info.size + (1, 1))[:2]
    numeric = info.cls in ("double", "single", "logical") or info.cls.startswith(("int", "uint"))
    if numeric and len(info.size) == 2:
        return Output("matrix", [region], name=name, text=textwrap.dedent(body).rstrip() + "\n",
                      rows=rows, columns=cols, var_type=info.cls)
    if info.cls == "struct":
        fields = re.sub(r"^\s*(scalar )?structure containing the fields:\s*\n", "", body.strip("\n") + "\n")
        fields = re.sub(r"^(\s*)(\w+) = ", r"\1\2: ", textwrap.dedent(fields), flags=re.M)
        header = "struct with fields:" if rows * cols == 1 else f"{rows}×{cols} struct array with fields:"
        return Output("variableString", [region], name=name, header=header,
                      text=textwrap.indent(fields.strip("\n"), "    ") + "\n", rows=rows, columns=cols)
    if info.cls == "cell":
        inner = "\n".join(l for l in body.splitlines() if l.strip() not in ("{", "}"))
        return Output("variableString", [region], name=name, header=f"{rows}×{cols} cell array",
                      text=textwrap.indent(textwrap.dedent(inner).strip("\n"), "    ") + "\n", rows=rows, columns=cols)
    return Output("variableString", [region], name=name, header=f"{rows}×{cols} {info.cls}",
                  text=textwrap.dedent(body).rstrip() + "\n", rows=rows, columns=cols)
