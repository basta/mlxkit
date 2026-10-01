"""Convert between .mlx live scripts and Jupyter notebooks (.ipynb).

  .mlx -> .ipynb   text paragraphs become Markdown cells (images as attachments,
                   equations as $...$), code blocks become code cells, section
                   breaks become `---` cells, and saved outputs come along.
  .ipynb -> .mlx   the reverse, built on the original .mlx so formatting the
                   notebook can't express survives. Outputs from the mlxkit
                   kernel carry the line that produced them, so they land on the
                   right statement.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import nbformat

from .document import Document
from .livetext import cells_to_text, from_text, to_cells
from .outputs import Output, build_output_xml, code_line_numbers, output_text, parse_output_xml
from .package import MlxPackage
from .runner import parse_script

SECTION = "---"
_BLOCK = re.compile(r"<!--\s*mlxkit:block:(\d+)\s*-->")
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


# --- Markdown dialects -----------------------------------------------------------
# .mlx Markdown (MATLAB's plain-text format) doubles backslashes inside $...$,
# escapes $ and writes newlines as <br>. Notebook Markdown uses plain TeX.

_MLX_MATH = re.compile(r"\$\$((?:\\.|[^$\\])+?)\$\$|\$((?:\\.|[^$\\])+?)\$")
_NB_MATH = re.compile(r"(?<!\\)\$\$(.+?)\$\$|(?<!\\)\$([^$\n]+?)\$", re.S)


def _tex_from_mlx(s: str) -> str:
    return re.sub(r"\\\\|\\\$|<br>", lambda m: {"\\\\": "\\", "\\$": "$", "<br>": "\n"}[m.group(0)], s)


def _tex_to_mlx(s: str) -> str:
    return s.replace("\\", "\\\\").replace("$", "\\$").replace("\n", "<br>")


def _join_paras(paras: list[str]) -> str:
    """Paragraphs as one Markdown text: list items on consecutive lines, others separated by a blank line."""
    text = ""
    for p in paras:
        sep = "\n" if text and p.startswith("- ") and text.rsplit("\n", 1)[-1].startswith("- ") else "\n\n"
        text = p if not text else text + sep + p
    return text


def _render_paras(paras: list[str]) -> str:
    return md_to_notebook(_join_paras(paras))


def md_to_notebook(md: str) -> str:
    md = _MLX_MATH.sub(lambda m: f"$${_tex_from_mlx(m.group(1))}$$" if m.group(1) is not None
                       else f"${_tex_from_mlx(m.group(2))}$", md)
    return re.sub(r"\]\(text:image:([^)]+)\)", r"](attachment:\1.png)", md)


def md_from_notebook(md: str) -> str:
    md = _NB_MATH.sub(lambda m: f"$${_tex_to_mlx(m.group(1))}$$" if m.group(1) is not None
                      else f"${_tex_to_mlx(m.group(2))}$", md)
    return re.sub(r"\]\(attachment:([^).]+)\.\w+\)", r"](text:image:\1)", md)


# --- .mlx -> notebook ------------------------------------------------------------

def to_notebook(path: str | Path) -> nbformat.NotebookNode:
    path = Path(path)
    pkg = MlxPackage.read(path)
    doc = Document(pkg.document_xml)
    cells = to_cells(doc)
    _, regions, _ = parse_script(doc)
    region_end = {r.number: r.end_line for r in regions}
    outputs = parse_output_xml(pkg.output_xml)

    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "mlxkit", "display_name": "MATLAB (Octave · mlxkit)", "language": "matlab"}
    nb.metadata["language_info"] = {"name": "matlab", "file_extension": ".m", "mimetype": "text/x-matlab",
                                    "codemirror_mode": "octave"}
    nb.metadata["mlxkit"] = {"source": path.name}

    md_buf: list[str] = []

    def flush_md():
        if md_buf:
            text = _join_paras(md_buf)
            # The exact .mlx paragraphs ride along, so an unedited cell converts back unchanged.
            cell = nbformat.v4.new_markdown_cell(md_to_notebook(text), metadata={"mlxkit": {"paras": list(md_buf)}})
            attach = {}
            for rid in re.findall(r"attachment:([^).]+)\.png", cell.source):
                media = pkg.media(rid)
                if media:
                    mime = _MIME.get(Path(media[0]).suffix.lower(), "image/png")
                    attach[f"{rid}.png"] = {mime: base64.b64encode(media[1]).decode()}
            if attach:
                cell.attachments = attach
            nb.cells.append(cell)
            md_buf.clear()

    for i, c in enumerate(cells):
        if c["kind"] == "text":
            md_buf.append(c["md"])
            continue
        flush_md()
        if c["kind"] == "section":
            nb.cells.append(nbformat.v4.new_markdown_cell(SECTION, metadata={"mlxkit": {"section": True}}))
        elif c["kind"] == "block":
            nb.cells.append(nbformat.v4.new_markdown_cell(
                f"<!-- mlxkit:block:{c['index']} -->\n*{c['label']} (kept as is from the .mlx)*"))
        elif c["kind"] == "code":
            block = doc.blocks[i]
            n = block.line_count
            cell = nbformat.v4.new_code_cell(c["code"])
            for o in outputs:
                ends = [region_end[r] for r in o.regions if r in region_end]
                if ends and block.first_line <= max(ends) < block.first_line + n:
                    nb_out = _nb_output(o, max(ends) - block.first_line)
                    if o.kind == "figure":
                        nb_out.metadata["mlxkit"]["lines"] = [e - block.first_line for e in ends
                                                              if e >= block.first_line]
                    cell.outputs.append(nb_out)
            nb.cells.append(cell)
    flush_md()
    _functions_first(nb)
    return nb


_FN_NOTE = ("*Local functions — moved here from the end of the live script so the notebook runs top to bottom. "
            "`mlx build` puts them back at the end.*")


def _is_function_code(src: str) -> bool:
    code = [l for l in src.split("\n") if l.strip() and not l.strip().startswith("%")]
    return bool(code) and code[0].lstrip().startswith("function")


def _functions_first(nb) -> None:
    """MATLAB lets a script call local functions defined at its end; a notebook
    run top to bottom can't, so function-only cells move to the top."""
    fn = [k for k, c in enumerate(nb.cells) if c.cell_type == "code" and _is_function_code(c.source)]
    if not fn:
        return
    moved = []
    for k in fn:
        cell = nb.cells[k]
        cell.metadata.setdefault("mlxkit", {})["functions_from"] = k
        moved.append(cell)
    rest = [c for k, c in enumerate(nb.cells) if k not in fn]
    note = nbformat.v4.new_markdown_cell(_FN_NOTE, metadata={"mlxkit": {"note": True}})
    nb.cells = [note, *moved, *rest]


def _functions_back(cells: list) -> list:
    """Undo _functions_first: drop the note and put function cells back where they were.

    Function-only cells written in the notebook itself go to the end: a live
    script may only define local functions after all of its other code.
    """
    cells = [c for c in cells if not c.get("metadata", {}).get("mlxkit", {}).get("note")]
    new_fn = [c for c in cells if c.cell_type == "code" and _is_function_code(c.source)
              and "functions_from" not in c.get("metadata", {}).get("mlxkit", {})]
    if new_fn:
        cells = [c for c in cells if c not in new_fn] + new_fn
    fn = [c for c in cells if "functions_from" in c.get("metadata", {}).get("mlxkit", {})]
    if not fn:
        return cells
    rest = [c for c in cells if c not in fn]
    for c in sorted(fn, key=lambda c: c.metadata["mlxkit"]["functions_from"]):
        rest.insert(min(c.metadata["mlxkit"]["functions_from"], len(rest)), c)
    return rest


def _nb_output(o: Output, line: int):
    meta = {"line": line, "kind": o.kind}
    if o.kind == "figure":
        meta["id"] = o.extra.get("id")
        w, h = o.size
        return nbformat.v4.new_output("display_data", data={
            "image/png": base64.b64encode(o.png or b"").decode(), "text/plain": f"<Figure {w}x{h}>"},
            metadata={"image/png": {"width": w, "height": h}, "mlxkit": meta})
    if o.raw is not None:
        meta["raw"] = o.raw  # exact MATLAB output, for a lossless trip back
    text = output_text(o)
    if o.kind == "error":
        return nbformat.v4.new_output("error", ename="Error", evalue=text.strip(), traceback=[text.strip()])
    return nbformat.v4.new_output("display_data", data={"text/plain": text.rstrip("\n")},
                                  metadata={"mlxkit": meta})


# --- notebook -> .mlx ------------------------------------------------------------

def notebook_cells(nb) -> list[dict]:
    """Editor cells (see livetext.to_cells) from notebook cells; code cells keep their notebook index."""
    cells: list[dict] = []
    for k, c in enumerate(nb.cells):
        if c.get("metadata", {}).get("mlxkit", {}).get("note"):
            continue
        src = c.source if isinstance(c.source, str) else "".join(c.source)
        if c.cell_type == "code":
            cells.append({"kind": "code", "code": src, "nb": k})
        elif c.cell_type in ("markdown", "raw"):
            if src.strip() == SECTION or c.get("metadata", {}).get("mlxkit", {}).get("section"):
                cells.append({"kind": "section"})
                continue
            m = _BLOCK.search(src)
            if m:
                cells.append({"kind": "block", "index": int(m.group(1))})
                continue
            paras = c.get("metadata", {}).get("mlxkit", {}).get("paras")
            if paras is not None and src == _render_paras(paras):
                cells.extend({"kind": "text", "md": p} for p in paras)  # unedited: exact original
                continue
            originals = {md_to_notebook(p).strip(): p for p in (paras or [])}
            for chunk in re.split(r"\n\s*\n", src):
                lines = [l for l in chunk.split("\n") if l.strip()]
                if not lines:
                    continue
                if chunk.strip() in originals:  # this paragraph wasn't touched
                    cells.append({"kind": "text", "md": originals[chunk.strip()]})
                elif lines[0].startswith(("- ", "* ")):
                    # A list: each item starts with "- "; other lines continue the item above.
                    items: list[str] = []
                    for l in lines:
                        if l.startswith(("- ", "* ")):
                            items.append("- " + l[2:].strip())
                        else:
                            items[-1] += " " + l.strip()
                    cells.extend({"kind": "text", "md": originals.get(it, md_from_notebook(it))} for it in items)
                else:
                    cells.append({"kind": "text", "md": md_from_notebook(" ".join(l.strip() for l in lines))})
    return cells


def from_notebook(nb, base_path: str | Path | None, out_path: str | Path) -> int:
    """Write the notebook into an .mlx based on base_path (None: a new live script).
    Returns the number of outputs kept."""
    from .package import new_package

    pkg = MlxPackage.read(base_path) if base_path is not None and Path(base_path).exists() else new_package()
    base = Document(pkg.document_xml)
    nb = nbformat.from_dict(dict(nb))
    nb.cells = _functions_back(nb.cells)
    cells = notebook_cells(nb)
    doc = from_text(cells_to_text(cells), base)
    pkg.document_xml = doc.to_xml()

    lines, regions, _ = parse_script(doc)
    # Where each notebook code cell starts in the document (adjacent code cells share a block).
    starts: dict[int, int] = {}
    code_blocks = iter(b for b in doc.blocks if b.kind == "code")
    block, used = next(code_blocks, None), 0
    for c in (c for c in cells if c["kind"] == "code"):
        if block is None:
            break
        starts[c["nb"]] = block.first_line + used
        used += c["code"].count("\n") + 1
        if used >= block.line_count:
            block, used = next(code_blocks, None), 0

    outputs: list[Output] = []
    for k, cell in enumerate(nb.cells):
        if cell.cell_type != "code" or k not in starts:
            continue
        n = (cell.source if isinstance(cell.source, str) else "".join(cell.source)).count("\n") + 1
        for out in cell.get("outputs", []):
            meta = out.get("metadata", {}).get("mlxkit", {})
            line = starts[k] + min(int(meta.get("line", n - 1)), n - 1)
            region = next((r.number for r in regions if r.start_line <= line <= r.end_line and not r.is_function), None)
            if region is None:
                region = next((r.number for r in reversed(regions)
                               if r.start_line <= line and not r.is_function), None)
            if region is None:
                continue
            o = _from_nb_output(out, meta, region)
            if o is None:
                continue
            if o.kind == "figure" and meta.get("lines"):
                regs = set()
                for ln in meta["lines"]:
                    doc_line = starts[k] + min(int(ln), n - 1)
                    regs |= {r.number for r in regions if r.start_line <= doc_line <= r.end_line}
                o.regions = sorted(regs) or o.regions
            outputs.append(o)
    outputs.sort(key=lambda o: (max(o.regions), o.kind == "figure"))
    pkg.output_xml = build_output_xml(regions, outputs, code_line_numbers(lines))
    pkg.write(out_path)
    return len(outputs)


def _from_nb_output(out, meta: dict, region: int) -> Output | None:
    kind = meta.get("kind")
    data = out.get("data", {})
    if out.output_type == "error":
        return Output("error", [region], text=out.get("evalue", ""))
    if "image/png" in data:
        png = base64.b64decode(data["image/png"])
        import struct

        w, h = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (560, 420)
        return Output("figure", [region], png=png, size=(w, h), extra={"id": meta.get("id")})
    if meta.get("raw") is not None:
        return Output(kind, [region], raw=meta["raw"])
    if kind in ("variable", "matrix", "variableString"):
        return Output(kind, [region], name=meta.get("name", ""), text=meta.get("text", ""),
                      header=meta.get("header", ""), rows=meta.get("rows", 1), columns=meta.get("columns", 1),
                      var_type=meta.get("var_type", "double"))
    text = out.get("text") if out.output_type == "stream" else data.get("text/plain")
    if text is None:
        return None
    text = text if isinstance(text, str) else "".join(text)
    if kind == "warning" or (out.output_type == "stream" and out.get("name") == "stderr"
                             and text.startswith("Warning")):
        return Output("warning", [region], text=text.rstrip("\n"))
    return Output("text", [region], text=text if text.endswith("\n") else text + "\n")
