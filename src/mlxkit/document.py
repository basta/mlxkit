"""The live script body (matlab/document.xml): an ordered list of paragraphs.

Each top-level block of <w:body> is kept as its raw XML string, so anything we
don't understand (equations, images, live controls, tables of contents) is
written back byte-for-byte. Only code paragraphs we change are regenerated.

MATLAB numbers document lines from 0: every block counts as one line, except
code paragraphs, which count one line per line of code.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS

_TOKEN = re.compile(r"<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|<(/?)([A-Za-z_][\w:.-]*)[^>]*?(/?)>", re.S)


@dataclass
class Block:
    raw: str
    kind: str  # "code", "text", "sectionbreak" or "other"
    style: str | None = None
    code: str | None = None  # only for kind == "code"
    first_line: int = 0  # 0-based document line of the block
    _el: ET.Element | None = field(default=None, repr=False)

    @property
    def line_count(self) -> int:
        return self.code.count("\n") + 1 if self.kind == "code" else 1

    @property
    def element(self) -> ET.Element:
        if self._el is None:
            self._el = ET.fromstring(f'<root xmlns:w="{W_NS}" '
                                     'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                                     f"{self.raw}</root>")[0]
        return self._el


def _split_top_level(xml: str) -> list[str]:
    """Split the inner XML of <w:body> into its top-level elements."""
    out, depth, start = [], 0, None
    for m in _TOKEN.finditer(xml):
        if m.group(2) is None:  # CDATA, comment or PI
            continue
        closing, selfclosing = m.group(1), m.group(3)
        if not closing and depth == 0:
            start = m.start()
        if closing:
            depth -= 1
        elif not selfclosing:
            depth += 1
        if depth == 0 and start is not None:
            out.append(xml[start:m.end()])
            start = None
    return out


def _classify(raw: str) -> Block:
    if not raw.startswith("<w:p>") and not raw.startswith("<w:p "):
        return Block(raw, "other")
    b = Block(raw, "text")
    el = b.element
    ppr = el.find(W + "pPr")
    style_el = ppr.find(W + "pStyle") if ppr is not None else None
    b.style = style_el.get(W + "val") if style_el is not None else None
    if ppr is not None and ppr.find(W + "sectPr") is not None and b.style is None:
        b.kind = "sectionbreak"
    elif b.style == "code":
        b.kind = "code"
        b.code = "".join(t.text or "" for t in el.iter(W + "t"))
    return b


class Document:
    def __init__(self, xml: bytes | str):
        text = xml.decode("utf-8") if isinstance(xml, bytes) else xml
        m = re.search(r"<w:body>(.*)</w:body>", text, re.S)
        if not m:
            raise ValueError("document.xml has no <w:body>")
        self._prefix, self._suffix = text[: m.start(1)], text[m.end(1):]
        self.blocks = [_classify(raw) for raw in _split_top_level(m.group(1))]
        self._renumber()

    def _renumber(self) -> None:
        line = 0
        for b in self.blocks:
            b.first_line = line
            line += b.line_count

    def to_xml(self) -> bytes:
        return (self._prefix + "".join(b.raw for b in self.blocks) + self._suffix).encode("utf-8")

    # --- code access -------------------------------------------------

    @property
    def code_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.kind == "code"]

    def lines(self) -> list[str | None]:
        """Every document line: code text for code lines, None for anything else."""
        out: list[str | None] = []
        for b in self.blocks:
            out.extend(b.code.split("\n") if b.kind == "code" else [None])
        return out

    def set_code(self, block: Block, code: str) -> None:
        block.raw = code_paragraph(code, block)
        block.code = code
        block._el = None
        self._renumber()

    def script(self) -> str:
        """The executable MATLAB code, with each document line on the same line number."""
        return "\n".join(line if line is not None else "" for line in self.lines())


def code_paragraph(code: str, old: Block | None = None) -> str:
    """XML for a code paragraph. Live controls and bookmarks of `old` are kept
    when the code they point at is unchanged."""
    safe = code.replace("]]>", "]]]]><![CDATA[>")
    extras = []
    if old is not None:
        lines = code.split("\n")
        for raw in _split_top_level(re.sub(r"^<w:p>|</w:p>$", "", old.raw)):
            if raw.startswith("<w:pPr") or raw.startswith("<w:r>") or raw.startswith("<w:r "):
                continue
            if "livecontrol" in raw and not _control_still_valid(raw, lines):
                continue
            extras.append(raw)
    head = [e for e in extras if e.startswith("<w:bookmarkStart")]
    tail = [e for e in extras if not e.startswith("<w:bookmarkStart")]
    return ('<w:p><w:pPr><w:pStyle w:val="code"/></w:pPr>' + "".join(head)
            + f"<w:r><w:t><![CDATA[{safe}]]></w:t></w:r>" + "".join(tail) + "</w:p>")


def _control_still_valid(raw: str, lines: list[str]) -> bool:
    attrs = dict(re.findall(r'<w:attr w:name="(\w+)" w:val="([^"]*)"', raw))
    try:
        line, start, end = int(attrs["startOffsetLine"]), int(attrs["startColumn"]), int(attrs["endColumn"])
        import html
        return line < len(lines) and lines[line][start:end] == html.unescape(attrs["text"])
    except (KeyError, ValueError):
        return False
