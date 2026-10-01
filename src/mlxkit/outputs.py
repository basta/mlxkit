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
    raw: str | None = None  # outputData XML kept verbatim (outputs read back from a file)


def _tag(name: str, value) -> str:
    return f"<{name}>{escape(str(value))}</{name}>"


def _array(name: str, values) -> str:
    if not values:
        return f'<{name} type="array"/>'
    return f'<{name} type="array">' + "".join(f"<element>{escape(str(v))}</element>" for v in values) + f"</{name}>"


_TRUNC = ("<truncationInfo><wasTruncatedAtLineBreak>false</wasTruncatedAtLineBreak>"
          "<wasTruncatedMidLine>false</wasTruncatedMidLine></truncationInfo>")


def _output_data(o: Output) -> str:
    if o.raw is not None:
        return o.raw
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


# --- reading outputs back, and keeping them across edits --------------------------

def parse_output_xml(xml: bytes | None) -> list[Output]:
    """Outputs saved in output.xml, with the region numbers they belong to.

    Figures are decoded (their region lists live inside outputData); every
    other output keeps its outputData verbatim.
    """
    import xml.etree.ElementTree as ET

    if not xml:
        return []
    root = ET.fromstring(xml)
    owners: dict[int, list[int]] = {}
    ra = root.find("regionArray")
    for e in (ra if ra is not None else []):
        num = int(e.find("code/regionNumber").text)
        for i in e.find("outputIndexes"):
            owners.setdefault(int(i.text), []).append(num)
    result = []
    oa = root.find("outputArray")
    for idx, e in enumerate(oa if oa is not None else []):
        kind = e.find("type").text
        data = e.find("outputData")
        if data is None:
            continue
        if kind == "figure":
            uri = data.findtext("figureUri") or ""
            png = base64.b64decode(uri.split(",", 1)[1]) if "," in uri else b""
            size = tuple(int(x.text) for x in data.find("figureSize")) if data.find("figureSize") is not None else (560, 420)
            regs = [int(x.text) for x in data.find("regionNumbers")] if data.find("regionNumbers") is not None else []
            result.append(Output("figure", regs or owners.get(idx, []), png=png, size=size,
                                 extra={"id": data.findtext("figureId")}))
        else:
            inner = "".join(ET.tostring(c, encoding="unicode") for c in data)
            result.append(Output(kind, owners.get(idx, []), raw=inner))
    return result


def region_signatures(lines: list[str | None], regions: list[Region]) -> list[str]:
    return ["\n".join(t or "" for t in lines[r.start_line:r.end_line + 1]).strip() for r in regions]


def remap_outputs(outputs: list[Output], old_lines, old_regions, new_lines, new_regions) -> list[Output]:
    """Carry outputs over to an edited document: an output survives if the
    statements that produced it are unchanged (matched by their code)."""
    import difflib

    old_sig, new_sig = region_signatures(old_lines, old_regions), region_signatures(new_lines, new_regions)
    mapping: dict[int, int] = {}
    sm = difflib.SequenceMatcher(None, old_sig, new_sig, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k
    kept = []
    for o in outputs:
        if not o.regions or any(r not in mapping for r in o.regions):
            continue  # some statement behind it changed: the output is stale
        o.regions = [mapping[r] for r in o.regions]
        kept.append(o)
    return kept
