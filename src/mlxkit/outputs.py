"""Embedded outputs (matlab/output.xml).

Layout, as saved by MATLAB R2021a-R2026b:

  <embeddedOutputs>
    <metaData>...</metaData>
    <outputArray type="array">  one <element> per output: <type>, <outputData>, <lineNumbers>
    <regionArray type="array">  one <element> per region: line span, section flags, <outputIndexes>

<lineNumbers> holds 1-based line numbers counting code lines only, for every
line of the regions that produced the output. MATLAB R2021a omits it; later
releases write both it and <outputIndexes>, so we write both.
"""
from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .regions import Region


@dataclass
class Output:
    kind: str  # "text", "warning", "error", "figure", "variable", "matrix", "variableString"
    regions: list[int]  # region numbers that produced it (figures: every region that drew on it)
    text: str = ""
    png: bytes | None = None
    size: tuple[int, int] = (560, 420)
    name: str = ""  # variable outputs
    header: str = ""
    rows: int = 1
    columns: int = 1
    var_type: str = "double"
    extra: dict = field(default_factory=dict)


def _tag(name: str, value) -> str:
    return f"<{name}>{escape(str(value))}</{name}>"


def _array(name: str, values) -> str:
    if not values:
        return f'<{name} type="array"/>'
    return f'<{name} type="array">' + "".join(f"<element>{escape(str(v))}</element>" for v in values) + f"</{name}>"


_TRUNC = ("<truncationInfo><wasTruncatedAtLineBreak>false</wasTruncatedAtLineBreak>"
          "<wasTruncatedMidLine>false</wasTruncatedMidLine></truncationInfo>")


def _output_data(o: Output) -> str:
    if o.kind == "figure":
        uri = "data:image/png;base64," + base64.b64encode(o.png or b"").decode()
        return (_tag("figureId", o.extra.get("id") or uuid.uuid4()) + _tag("figureUri", uri)
                + _array("figureSize", list(o.size)) + _array("regionNumbers", o.regions)
                + _tag("originalRegionNumber", o.regions[0]))
    if o.kind in ("text", "error"):
        return _tag("text", o.text) + _TRUNC
    if o.kind == "warning":
        return _tag("text", o.text)
    if o.kind == "variable":
        return _tag("name", o.name) + _tag("value", o.text) + _tag("rows", o.rows) + _tag("columns", o.columns)
    if o.kind == "variableString":
        return (_tag("name", o.name) + _tag("value", o.text) + _TRUNC + _tag("header", o.header)
                + _tag("rows", o.rows) + _tag("columns", o.columns))
    if o.kind == "matrix":
        return ("<header />" + _tag("name", o.name) + _tag("varSize", f"{o.rows}×{o.columns}")
                + _tag("rows", o.rows) + _tag("columns", o.columns) + _tag("varType", o.var_type)
                + _tag("value", o.text) + _tag("type", o.var_type) + _tag("subtype", o.var_type))
    raise ValueError(f"unknown output kind {o.kind!r}")


def build_output_xml(regions: list[Region], outputs: list[Output], code_line_numbers: dict[int, int],
                     layout: str = "inline") -> bytes:
    """code_line_numbers maps 0-based document lines to 1-based code-only line numbers."""
    by_region: dict[int, list[int]] = {}
    elements = []
    for idx, o in enumerate(outputs):
        lines = []
        for r in o.regions:
            by_region.setdefault(r, []).append(idx)
            reg = regions[r]
            lines += [code_line_numbers[ln] for ln in range(reg.start_line, reg.end_line + 1) if ln in code_line_numbers]
        elements.append(f"<element>{_tag('type', o.kind)}<outputData>{_output_data(o)}</outputData>"
                        f"{_array('lineNumbers', lines)}</element>")
    region_xml = []
    for r in regions:
        region_xml.append(
            "<element><code>" + _tag("sectionBreak", str(r.section_break).lower())
            + _tag("endOfSection", str(r.end_of_section).lower()) + _tag("regionNumber", r.number) + "</code>"
            + _tag("startLine", r.start_line) + _tag("endLine", r.end_line)
            + _array("outputIndexes", by_region.get(r.number, [])) + "</element>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?><embeddedOutputs><metaData>'
           "<evaluationState>manual</evaluationState>" + _tag("layoutState", layout)
           + "<outputStatus>ready</outputStatus></metaData>"
           + _array_raw("outputArray", elements) + _array_raw("regionArray", region_xml) + "</embeddedOutputs>")
    return xml.encode("utf-8")


def _array_raw(name: str, items: list[str]) -> str:
    return f'<{name} type="array">' + "".join(items) + f"</{name}>" if items else f'<{name} type="array"/>'


def code_line_numbers(lines: list[str | None]) -> dict[int, int]:
    out, k = {}, 0
    for i, t in enumerate(lines):
        if t is not None:
            k += 1
            out[i] = k
    return out
