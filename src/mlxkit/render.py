"""Render a live script with its embedded outputs as a standalone HTML page."""
from __future__ import annotations

import base64
import html
import re
import xml.etree.ElementTree as ET

from .document import Document
from .livetext import paragraph_markdown
from .package import MlxPackage

CSS = """
:root { --bg:#fff; --fg:#1f2328; --muted:#59636e; --code-bg:#f6f8fa; --border:#d1d9e0; --out-bg:#fbfbfb; --warn:#9a6700; --err:#d1242f; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --code-bg:#151b23; --border:#3d444d; --out-bg:#10151c; --warn:#d29922; --err:#f85149; } }
body { background:var(--bg); color:var(--fg); font:16px/1.55 -apple-system,Segoe UI,Helvetica,Arial,sans-serif; max-width:860px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:1.9em; margin:.2em 0 .4em; } h2 { font-size:1.4em; margin:1.4em 0 .4em; } h3 { font-size:1.15em; }
pre { margin:0; white-space:pre-wrap; word-break:break-word; font:13.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }
.code { background:var(--code-bg); border:1px solid var(--border); border-radius:6px; padding:10px 12px; margin:12px 0 4px; overflow-x:auto; }
.out { border-left:3px solid var(--border); background:var(--out-bg); padding:6px 12px; margin:4px 0 12px; }
.out img { max-width:100%; height:auto; background:#fff; border-radius:4px; }
.warning pre { color:var(--warn); } .error pre { color:var(--err); }
.section { border-top:1px dashed var(--border); margin:20px 0; }
.eq-display { text-align:center; margin:.6em 0; }
code { background:var(--code-bg); padding:.1em .3em; border-radius:4px; font-size:.9em; }
.var b { font-family:ui-monospace,Menlo,monospace; }
img.inline { max-width:100%; }
"""

KATEX = ('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">'
         '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>'
         '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" '
         'onload="renderMathInElement(document.body,{delimiters:[{left:\'\\\\[\',right:\'\\\\]\',display:true},'
         '{left:\'\\\\(\',right:\'\\\\)\',display:false}],throwOnError:false})"></script>')


def render_html(path) -> str:
    pkg = MlxPackage.read(path)
    doc = Document(pkg.document_xml)
    outputs, region_lines = _read_outputs(pkg.output_xml)
    # Show each output after the last line of the last region that produced it.
    after_line: dict[int, list[str]] = {}
    for out_html, regions in outputs:
        ends = [region_lines[r] for r in regions if r in region_lines]
        if ends:
            after_line.setdefault(max(ends), []).append(out_html)

    title = "Live script"
    body: list[str] = []
    for b in doc.blocks:
        if b.kind == "code":
            lines = b.code.split("\n")
            chunk: list[str] = []
            for i, line in enumerate(lines):
                chunk.append(line)
                ln = b.first_line + i
                if ln in after_line:
                    body.append(f'<div class="code"><pre>{html.escape(chr(10).join(chunk))}</pre></div>')
                    body.extend(after_line[ln])
                    chunk = []
            if chunk and "".join(chunk).strip():
                body.append(f'<div class="code"><pre>{html.escape(chr(10).join(chunk))}</pre></div>')
        elif b.kind == "sectionbreak":
            body.append('<div class="section"></div>')
        else:
            md = paragraph_markdown(b)
            if md is None:
                continue
            if b.style == "title":
                title = re.sub(r"<[^>]+>", "", _md_html(md[2:], pkg))
            body.append(_block_html(b.style, md, pkg))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style>{KATEX}</head><body>"
            + "\n".join(body) + "</body></html>")


def _block_html(style, md, pkg) -> str:
    for prefix, tag in (("#### ", "h4"), ("### ", "h3"), ("## ", "h2"), ("# ", "h1")):
        if md.startswith(prefix):
            return f"<{tag}>{_md_html(md[len(prefix):], pkg)}</{tag}>"
    if style == "ListParagraph":
        return f"<ul><li>{_md_html(md, pkg)}</li></ul>"
    if md.startswith("\\"):
        md = md[1:]
    return f"<p>{_md_html(md, pkg)}</p>"


_TOKENS = re.compile(r"\\([\\*`$\[\]<])|!\[((?:\\.|[^\]\\])*)\]\(text:image:([^)]+)\)|\[((?:\\.|[^\]])*)\]\((?:<([^>]*)>|([^)]*))\)"
                     r"|\$\$((?:\\.|[^$\\])+?)\$\$|\$((?:\\.|[^$\\])+?)\$|`([^`]*)`|\*\*|\*|<u>|</u>|<br>")


def _md_html(md: str, pkg) -> str:
    out, pos, open_ = [], 0, {"**": False, "*": False}
    for m in _TOKENS.finditer(md):
        out.append(html.escape(md[pos:m.start()]))
        pos = m.end()
        tok = m.group(0)
        if m.group(1):
            out.append(html.escape(m.group(1)))
        elif m.group(3):
            media = pkg.media(m.group(3))
            if media:
                mime = "image/png" if media[0].endswith(".png") else "image/jpeg" if media[0].endswith((".jpg", ".jpeg")) else "image/gif"
                data = base64.b64encode(media[1]).decode()
                out.append(f'<img class="inline" alt="{html.escape(m.group(2))}" src="data:{mime};base64,{data}">')
        elif m.group(4) is not None:
            url = m.group(5) if m.group(5) is not None else m.group(6)
            href = url if url.startswith(("http://", "https://", "#")) else "#"
            out.append(f'<a href="{html.escape(href)}">{_md_html(m.group(4), pkg)}</a>')
        elif m.group(7):
            out.append('<div class="eq-display">\\[' + html.escape(_tex(m.group(7))) + "\\]</div>")
        elif m.group(8):
            out.append("\\(" + html.escape(_tex(m.group(8))) + "\\)")
        elif m.group(9) is not None:
            out.append(f"<code>{html.escape(m.group(9))}</code>")
        elif tok in ("**", "*"):
            open_[tok] = not open_[tok]
            tag = "strong" if tok == "**" else "em"
            out.append(f"<{tag}>" if open_[tok] else f"</{tag}>")
        elif tok == "<u>":
            out.append("<u>")
        elif tok == "</u>":
            out.append("</u>")
        elif tok == "<br>":
            out.append("<br>")
    out.append(html.escape(md[pos:]))
    return "".join(out)


def _tex(s: str) -> str:
    return re.sub(r"\\\\|\\\$|<br>", lambda t: {"\\\\": "\\", "\\$": "$", "<br>": "\n"}[t.group(0)], s)


def _read_outputs(xml: bytes | None):
    if not xml:
        return [], {}
    root = ET.fromstring(xml)
    region_lines, owners = {}, {}
    ra = root.find("regionArray")
    for e in (ra if ra is not None else []):
        num = int(e.find("code/regionNumber").text)
        region_lines[num] = int(e.find("endLine").text)
        for i in e.find("outputIndexes"):
            owners.setdefault(int(i.text), []).append(num)
    result = []
    oa = root.find("outputArray")
    for idx, e in enumerate(oa if oa is not None else []):
        kind = e.find("type").text
        d = e.find("outputData")
        get = lambda tag: (d.find(tag).text or "") if d.find(tag) is not None else ""
        if kind == "figure":
            inner = f'<img alt="figure" src="{html.escape(get("figureUri"))}">'
        elif kind in ("variable", "variableString", "matrix"):
            sep = "\n" if "\n" in get("value").strip() or get("header") else " "
            inner = f'<pre class="var"><b>{html.escape(get("name"))} =</b>{sep}{html.escape(get("value"))}</pre>'
        elif kind == "symbolic":
            inner = f"<div>{get('value')}</div>"  # MathML from MATLAB
        else:
            inner = f"<pre>{html.escape(get('text') or get('value'))}</pre>"
        result.append((f'<div class="out {kind}">{inner}</div>', owners.get(idx, [])))
    return result, region_lines
