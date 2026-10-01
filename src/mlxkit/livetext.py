"""Convert between .mlx and MATLAB's plain-text live script format (.m, R2025a+).

    %[text] # Title
    %[text] Some **bold** text with an equation $x^2$.
    x = 1;          <- code lines are written as-is
    %%              <- section break

The .m file is an editing view of its .mlx: when building, paragraphs whose
text is unchanged reuse their original XML, so formatting we don't model
(colors, live controls, bookmarks, tables of contents) survives. Blocks that
have no text representation are written as `%[text:mlxkit:block:<n>]`
pointing at block n of the original .mlx.
"""
from __future__ import annotations

import html
import re
from xml.sax.saxutils import escape

from .document import W, Block, Document, code_paragraph

APPENDIX = '%[appendix]{"version":"1.0"}\n%---\n%[metadata:view]\n%   data: {"layout":"inline"}\n%---\n'
HEADINGS = {"title": "# ", "heading": "## ", "heading2": "### ", "heading3": "#### "}
_MD_SPECIAL = re.compile(r"([\\*`$\[\]<])")
_MARKUP_LINE = re.compile(r"^\s*(%%|%\[)")
CODE_ESCAPE = "%[code]"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


# --- .mlx -> text ------------------------------------------------------

def to_text(doc: Document) -> str:
    out: list[str] = []
    blocks = doc.blocks
    for i, b in enumerate(blocks):
        if b.kind == "code":
            # Code lines that would read as markup (`%% heading`, `%[text]`) get a `%[code]` prefix.
            out.extend(CODE_ESCAPE + l if _MARKUP_LINE.match(l) else l for l in b.code.split("\n"))
        elif b.kind == "sectionbreak":
            out.append("%%")
        else:
            md = paragraph_markdown(b)
            if md is None:
                out.append(f"%[text:mlxkit:block:{i}]")
                continue
            if b.style == "ListParagraph":
                last = i + 1 >= len(blocks) or blocks[i + 1].style != "ListParagraph"
                md = "- " + md + (" \\" if last else "")
            out.append("%[text] " + md if md else "%[text]")
    return "\n".join(out) + "\n" + APPENDIX


def paragraph_markdown(b: Block) -> str | None:
    """Markdown for a text paragraph, or None if it can't be expressed as text."""
    if b.kind != "text":
        return None
    prefix = HEADINGS.get(b.style or "", "")
    if b.style not in (None, "text", "ListParagraph") and not prefix:
        return None
    md = _inline(b.element)
    if not prefix and (md.startswith("#") or md.startswith("- ")):
        md = "\\" + md  # keep literal text from reading as a heading or list item
    return prefix + md


def _inline(el) -> str:
    parts = []
    for child in el:
        tag = child.tag
        if tag == W + "r":
            parts.append(_run(child))
        elif tag == W + "hyperlink":
            target = (child.get(W + "dest") or child.get(W + "docLocation")
                      or ("#" + child.get(W + "anchor") if child.get(W + "anchor") else ""))
            parts.append(f"[{_inline(child)}]({_url(target)})")
        elif tag == W + "customXml":
            kind = child.get(W + "element")
            attrs = {a.get(W + "name"): a.get(W + "val") for a in child.iter(W + "attr")}
            if kind == "equation":
                tex = "".join(t.text or "" for t in child.iter(W + "t"))
                tex = tex.replace("\\", "\\\\").replace("$", "\\$").replace("\n", "<br>")
                parts.append(f"$${tex}$$" if attrs.get("displayStyle") == "true" else f"${tex}$")
            elif kind == "image":
                alt = re.sub(r"([\\\[\]])", r"\\\1", attrs.get("altText") or "")
                parts.append(f"![{alt}](text:image:{attrs.get('relationshipId')})")
            else:
                parts.append(_inline(child))
        elif tag == MC + "AlternateContent":
            choice = child.find(MC + "Choice")
            if choice is not None:
                parts.append(_inline(choice))
        elif tag in (W + "pPr", W + "bookmarkStart", W + "bookmarkEnd", W + "customXmlPr"):
            continue
        else:
            parts.append(_inline(child))
    return "".join(parts)


def _run(r) -> str:
    text = "".join(t.text or "" for t in r.iter(W + "t"))
    if not text:
        return ""
    rpr = r.find(W + "rPr")
    if rpr is None:
        return _md_escape(text)
    mono = any(f.get(W + "cs") == "monospace" for f in rpr.iter(W + "rFonts"))
    body = f"`{text}`" if mono else _md_escape(text)
    lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", body, re.S).groups()
    if not core:
        return body
    if rpr.find(W + "u") is not None:
        core = f"<u>{core}</u>"
    if rpr.find(W + "i") is not None:
        core = f"*{core}*"
    if rpr.find(W + "b") is not None:
        core = f"**{core}**"
    return lead + core + trail


def _url(u: str) -> str:
    return f"<{u}>" if re.search(r"[()\s<>\[\]]", u) else u


def _md_escape(s: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", s).replace("\n", "<br>")


# --- text -> .mlx -------------------------------------------------------

def from_text(text: str, base: Document) -> Document:
    """Build a document from plain-text live code, reusing base paragraphs where unchanged."""
    text = text.split("%[appendix]", 1)[0]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    base_md: list[tuple[int, str]] = []
    for i, b in enumerate(base.blocks):
        if b.kind == "text":
            md = paragraph_markdown(b)
            if md is not None:
                if b.style == "ListParagraph":
                    md = "- " + md
                base_md.append((i, md))
    used: set[int] = set()
    base_code = [b for b in base.code_blocks]

    raws: list[str] = []
    code_buf: list[str] = []
    code_index = 0

    def flush_code():
        nonlocal code_buf, code_index
        if not code_buf:
            return
        code = "\n".join(code_buf)
        old = base_code[code_index] if code_index < len(base_code) else None
        raws.append(old.raw if old is not None and old.code == code else code_paragraph(code, old))
        code_index += 1
        code_buf = []

    for line in lines:
        if line.strip() == "%%" or line.startswith("%% "):
            flush_code()
            raws.append("<w:p><w:pPr><w:sectPr/></w:pPr></w:p>")
            title = line.strip()[2:].strip()
            if title:  # classic `%% Section title` cell syntax
                raws.append(markdown_paragraph("## " + title))
        elif m := re.match(r"^%\[text:mlxkit:block:(\d+)\]\s*$", line):
            flush_code()
            raws.append(base.blocks[int(m.group(1))].raw)
        elif line.startswith("%[text]"):
            flush_code()
            md = line[len("%[text]"):]
            md = md[1:] if md.startswith(" ") else md
            md = re.sub(r"\s\\$", "", md) if md.startswith("- ") else md
            hit = next((i for i, m_ in base_md if i not in used and m_ == md), None)
            if hit is not None:
                used.add(hit)
                raws.append(base.blocks[hit].raw)
            else:
                raws.append(markdown_paragraph(md, base))
        else:
            code_buf.append(line[len(CODE_ESCAPE):] if line.startswith(CODE_ESCAPE) else line)
    flush_code()

    xml = base.to_xml().decode()
    body = re.search(r"<w:body>(.*)</w:body>", xml, re.S)
    return Document(xml[: body.start(1)] + "".join(raws) + xml[body.end(1):])


_INLINE = re.compile(
    r"(?P<esc>\\[\\*`$\[\]<])"
    r"|(?P<img>!\[(?P<alt>(?:\\.|[^\]\\])*)\]\(text:image:(?P<rid>[^)]+)\))"
    r"|(?P<link>\[(?P<ltext>(?:\\.|!\[(?:\\.|[^\]\\])*\]\([^)]*\)|[^\]])*)\]\((?:<(?P<aurl>[^>]*)>|(?P<url>[^)]*))\))"
    r"|(?P<deq>\$\$(?P<dtex>(?:\\.|[^$\\])+?)\$\$)"
    r"|(?P<eq>\$(?P<tex>(?:\\.|[^$\\])+?)\$)"
    r"|(?P<code>`(?P<ctext>[^`]*)`)"
    r"|(?P<bold>\*\*)|(?P<ital>\*)|(?P<u><u>|</u>)|(?P<br><br>)"
)


def markdown_paragraph(md: str, base: Document | None = None) -> str:
    style = "text"
    if md.startswith("\\#") or md.startswith("\\- "):
        md = md[1:]
        return f'<w:p><w:pPr><w:pStyle w:val="text"/><w:jc w:val="left"/></w:pPr>{_inline_xml(md)}</w:p>'
    for s, prefix in sorted(HEADINGS.items(), key=lambda kv: -len(kv[1])):
        if md.startswith(prefix):
            style, md = s, md[len(prefix):]
            break
    else:
        if md.startswith("- "):
            style, md = "ListParagraph", md[2:]
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/>'
    if style == "ListParagraph":
        ppr += '<w:numPr><w:numId w:val="1"/></w:numPr>'
    ppr += '<w:jc w:val="left"/></w:pPr>'
    return f"<w:p>{ppr}{_inline_xml(md)}</w:p>"


def _inline_xml(md: str) -> str:
    out, buf = [], []
    fmt = {"b": False, "i": False, "u": False}

    def flush():
        if buf:
            out.append(_run_xml("".join(buf), fmt))
            buf.clear()

    pos = 0
    for m in _INLINE.finditer(md):
        buf.append(md[pos:m.start()])
        pos = m.end()
        kind = m.lastgroup if m.lastgroup in ("bold", "ital", "u", "br", "esc") else next(
            k for k in ("img", "link", "deq", "eq", "code") if m.group(k))
        if kind == "esc":
            buf.append(m.group(0)[1])
            continue
        if kind == "br":
            buf.append("\n")
            continue
        flush()
        if kind == "bold":
            fmt["b"] = not fmt["b"]
        elif kind == "ital":
            fmt["i"] = not fmt["i"]
        elif kind == "u":
            fmt["u"] = m.group(0) == "<u>"
        elif kind in ("eq", "deq"):
            tex = (m.group("dtex") if kind == "deq" else m.group("tex"))
            tex = re.sub(r"\\\\|\\\$|<br>", lambda t: {"\\\\": "\\", "\\$": "$", "<br>": "\n"}[t.group(0)], tex)
            display = "true" if kind == "deq" else "false"
            out.append('<w:customXml w:element="equation"><w:customXmlPr>'
                       f'<w:attr w:name="displayStyle" w:val="{display}"/></w:customXmlPr>'
                       f"<w:r><w:t>{escape(tex)}</w:t></w:r></w:customXml>")
        elif kind == "code":
            out.append(_run_xml(m.group("ctext"), fmt, mono=True))
        elif kind == "link":
            url = m.group("aurl") if m.group("aurl") is not None else m.group("url")
            attr = (f'w:anchor="{html.escape(url[1:])}"' if url.startswith("#")
                    else f'w:docLocation="{html.escape(url)}"' if url.startswith("matlab:")
                    else f'w:dest="{html.escape(url)}"')
            out.append(f"<w:hyperlink {attr}>{_inline_xml(m.group('ltext'))}</w:hyperlink>")
        elif kind == "img":
            alt = html.escape(re.sub(r"\\(.)", r"\1", m.group("alt")))
            out.append('<w:customXml w:element="image"><w:customXmlPr>'
                       f'<w:attr w:name="altText" w:val="{alt}"/>'
                       f'<w:attr w:name="relationshipId" w:val="{html.escape(m.group("rid"))}"/>'
                       "</w:customXmlPr></w:customXml>")
    buf.append(md[pos:])
    flush()
    return "".join(out)


def _run_xml(text: str, fmt: dict, mono: bool = False) -> str:
    if not text:
        return ""
    rpr = ("<w:b/>" if fmt["b"] else "") + ("<w:i/>" if fmt["i"] else "") + ("<w:u/>" if fmt["u"] else "")
    rpr += '<w:rFonts w:cs="monospace"/>' if mono else ""
    rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
    return f"<w:r>{rpr}<w:t>{escape(text)}</w:t></w:r>"
