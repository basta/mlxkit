import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mlxkit.document import Document
from mlxkit.livetext import from_text, to_text
from mlxkit.outputs import Output, build_output_xml, code_line_numbers
from mlxkit.package import MlxPackage
from mlxkit.regions import split_regions

# A live script saved by MATLAB, with outputs (BSD-licensed, see fixtures/README.md).
FIXTURE = Path(__file__).parent / "fixtures" / "OpAmpLabSoln.mlx"

# A script with local functions, as MATLAB requires them: after all other code.
WITH_FUNCTIONS = """%[text] # Projectile
v0 = 20;
y = height(v0, 1)
%%
%[text] ## Helpers
%[text] Local functions live at the end of a live script.
function y = height(v, t)
    y = v*t - 9.81*t^2/2; % your code goes here
end
function g = gravity()
    g = 9.81;
end
"""


def make_script(path, text=WITH_FUNCTIONS):
    """A new .mlx written by mlxkit from plain-text live code."""
    from mlxkit.package import new_package

    pkg = new_package()
    pkg.document_xml = from_text(text, Document(pkg.document_xml)).to_xml()
    pkg.write(path)
    return path


def load():
    pkg = MlxPackage.read(FIXTURE)
    return pkg, Document(pkg.document_xml)


def regions_of(doc):
    sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
    return split_regions(doc.lines(), sections)


def matlab_regions(pkg):
    out = []
    for e in ET.fromstring(pkg.output_xml).find("regionArray"):
        c = e.find("code")
        out.append((int(e.find("startLine").text), int(e.find("endLine").text),
                    c.find("sectionBreak").text == "true", c.find("endOfSection").text == "true"))
    return out


def test_document_roundtrips_byte_for_byte():
    pkg, doc = load()
    assert doc.to_xml() == pkg.document_xml


def test_package_roundtrip(tmp_path):
    pkg, _ = load()
    pkg.write(tmp_path / "copy.mlx")
    assert MlxPackage.read(tmp_path / "copy.mlx").parts == pkg.parts


def test_regions_match_matlab():
    pkg, doc = load()
    got = [(r.start_line, r.end_line, r.section_break, r.end_of_section) for r in regions_of(doc)]
    assert got == matlab_regions(pkg)


@pytest.mark.parametrize("code, expected", [
    ("a = 1;\nb = 2;", [(0, 0), (1, 1)]),
    ("for k = 1:3\n  disp(k)\nend", [(0, 2)]),
    ("x = [1 2\n 3 4];", [(0, 1)]),
    ("y = f(1, ...\n  2);", [(0, 1)]),
    ("s = 'it''s % not a comment'; t = s';\nz = 1", [(0, 0), (1, 1)]),
    ("% just a comment\n\nq = 3", [(2, 2)]),
    ("%{\nblock comment\n%}\nw = 1", [(3, 3)]),
    ("if a, b = 1; end\nc = x(end)", [(0, 0), (1, 1)]),
    ("hold on\nplot(x, y)", [(0, 0), (1, 1)]),
])
def test_statement_splitting(code, expected):
    regions = split_regions(code.split("\n"))
    assert [(r.start_line, r.end_line) for r in regions] == expected


def test_livetext_roundtrip():
    pkg, doc = load()
    assert from_text(to_text(doc), doc).to_xml() == pkg.document_xml


def test_livetext_edit_code_and_text():
    _, doc = load()
    text = to_text(doc)
    text = text.replace("R1_part1 = 2 % Replace NaN with your answer in kOhm", "R1_part1 = 2.5 % edited")
    text = text.replace("%[text] # Operational Amplifiers Lab", "%[text] # Op Amps **edited**")
    new = from_text(text, doc)
    code = "\n".join(b.code for b in new.code_blocks)
    assert "R1_part1 = 2.5 % edited" in code
    assert len(new.blocks) == len(doc.blocks)
    edited = [b for b in new.blocks if "Op Amps" in b.raw][0]
    assert edited.style == "title" and "<w:b/>" in edited.raw
    # Untouched paragraphs keep their original XML, including the image.
    assert sum(b.raw == o.raw for b, o in zip(new.blocks, doc.blocks)) == len(doc.blocks) - 2


def test_local_function_regions_follow_matlab():
    # How MATLAB R2021a split a script ending in local functions: the first
    # function's header and body statements are regions, then everything from
    # its `end` to the last line is one region.
    lines = ["a = 1;", "function y = f(x)", "  % comment", "  y = 2*x;", "end",
             "function z = g()", "  z = 3;", "end"]
    regions = split_regions(lines)
    assert [(r.start_line, r.end_line, r.is_function) for r in regions] == [
        (0, 0, False), (1, 1, True), (3, 3, True), (4, 7, True)]
    assert regions[0].section_break and regions[-1].end_of_section


def test_classic_section_titles_become_headings():
    _, doc = load()
    new = from_text("%% Setup\nx = 1;\n", doc)
    assert [b.kind for b in new.blocks] == ["sectionbreak", "text", "code"]
    assert new.blocks[1].style == "heading"


def test_output_xml_links_outputs_to_regions():
    _, doc = load()
    regions = regions_of(doc)
    lines = doc.lines()
    out = build_output_xml(regions, [Output("text", [1], text="hi\n")], code_line_numbers(lines))
    root = ET.fromstring(out)
    element = root.find("outputArray")[0]
    assert element.find("type").text == "text"
    region = root.find("regionArray")[1]
    assert [i.text for i in region.find("outputIndexes")] == ["0"]
    # lineNumbers count code lines only, 1-based.
    expected = code_line_numbers(lines)[regions[1].start_line]
    assert [int(x.text) for x in element.find("lineNumbers")] == [expected]


@pytest.mark.skipif(shutil.which("octave") is None, reason="GNU Octave not installed")
def test_run_in_octave(tmp_path):
    from mlxkit.runner import run

    src = tmp_path / "script.mlx"
    pkg, doc = load()
    doc = from_text("%[text] # Test\nx = 6*7\ndisp('hello')\nplot(1:3)\nwarning('careful')\n"
                    "M = [1 2; 3 4]\nname = 'Ada'\ns.a = 1;\ns\n", doc)
    pkg.document_xml = doc.to_xml()
    pkg.write(src)
    result = run(src, tmp_path / "out.mlx")
    kinds = [(o.kind, o.text) for o in result.outputs if o.kind != "figure"]
    assert ("variable", "42") in kinds
    assert ("variable", "'Ada'") in kinds
    matrix = next(o for o in result.outputs if o.kind == "matrix")
    assert (matrix.name, matrix.rows, matrix.columns) == ("M", 2, 2)
    struct = next(o for o in result.outputs if o.kind == "variableString")
    assert struct.header == "struct with fields:" and "a: 1" in struct.text
    assert ("text", "hello\n") in kinds
    assert ("warning", "Warning: careful") in kinds
    assert any(o.kind == "figure" and o.png.startswith(b"\x89PNG") for o in result.outputs)
    assert result.error is None
    assert MlxPackage.read(tmp_path / "out.mlx").output_xml


@pytest.mark.parametrize("src, expected", [
    ("plot(x, y, LineWidth=2)", "plot(x, y, 'LineWidth', 2)"),
    ("t = table(a, b, VariableNames=[\"a\" \"b\"]);", "t = table(a, b, 'VariableNames', [\"a\" \"b\"]);"),
    ("if (a == b), c = 1; end", "if (a == b), c = 1; end"),
    ("s = 'x=1'; f(s)", "s = 'x=1'; f(s)"),
    ("x(k) = 3;", "x(k) = 3;"),
])
def test_name_value_args(src, expected):
    from mlxkit.transform import name_value_args
    assert name_value_args(src) == expected


def test_arguments_block_removed_with_defaults():
    from mlxkit.transform import strip_arguments_blocks
    src = ("function y = f(x, n)\narguments\n    x (1,:) double {mustBeFinite}\n"
           "    n (1,1) double = 3 % count\nend\ny = x * n;\nend")
    out = strip_arguments_blocks(src)
    assert "mustBeFinite" not in out
    assert "if ~exist('n', 'var'), n = 3; end" in out
    assert out.count("\n") == src.count("\n")


def test_arguments_as_variable_name_is_not_a_block():
    regions = split_regions(["arguments = 3;", "y = arguments + 1;"])
    assert [(r.start_line, r.end_line) for r in regions] == [(0, 0), (1, 1)]


@pytest.mark.parametrize("src, expected", [
    ('disp("Total: " + n + " items")', 'disp(mlxkit_strplus("Total: ", n, " items"))'),
    ('msg = "a" + x;', 'msg = mlxkit_strplus("a", x);'),
    ("y = a + b;", "y = a + b;"),
    ('z = g("a" + b) + 1;', 'z = g(mlxkit_strplus("a", b)) + 1;'),
    ('x = ["a" + 1, 2];', 'x = ["a" + 1, 2];'),
    ("plot(x, y, LineWidth=2, SeriesIndex=1)", "plot(x, y, 'LineWidth', 2)"),
])
def test_transform(src, expected):
    from mlxkit.transform import transform
    assert transform(src) == expected


def test_handle_dot_notation():
    from mlxkit.transform import transform
    code = ("s = scatter(x, y);\ns.XData = [s.XData 1];\nax = gca;\nax.YAxis.Exponent = 0;\n"
            "d = ax.YLim(2) - ax.YLim(1);\nt.a = 1; disp(t.a)")
    out = transform(code).split("\n")
    assert out[1] == "mlxkit_set(s, 'XData', [get(s, 'XData') 1]);"
    assert out[3] == "mlxkit_set(mlxkit_get(ax, 'YAxis'), 'Exponent', 0);"
    assert out[4] == "d = get(ax, 'YLim')(2) - get(ax, 'YLim')(1);"
    assert out[5] == "t.a = 1; disp(t.a)"  # structs are left alone


def test_handle_indexed_assignment():
    from mlxkit.transform import transform
    out = transform("s = scatter(1, 2);\ns.XData(end+1) = 3;").split("\n")[1]
    assert out == ("mlxkit_tmp_ = get(s, 'XData'); mlxkit_tmp_(end+1) = 3; "
                   "mlxkit_set(s, 'XData', mlxkit_tmp_); clear mlxkit_tmp_;")


def test_server_save_roundtrip(tmp_path):
    from mlxkit.server import App, Conflict

    shutil.copy(FIXTURE, tmp_path / "k.mlx")
    app = App(tmp_path)
    assert [f["path"] for f in app.files()] == ["k.mlx"]
    doc = app.doc("k.mlx")
    figure_cells = [c for c in doc["cells"] if c["kind"] == "code" and c["outputs"]]
    assert figure_cells, "saved outputs are attached to code cells"
    # Saving unchanged cells leaves the file byte-identical.
    before = (tmp_path / "k.mlx").read_bytes()
    app.save("k.mlx", doc["mtime"], doc["cells"])
    assert (tmp_path / "k.mlx").read_bytes() == before
    # An edit changes only that paragraph.
    cells = doc["cells"]
    cells[1]["md"] = "*by someone else*"
    new = app.save("k.mlx", app.doc("k.mlx")["mtime"], cells)
    assert new["cells"][1]["md"] == "*by someone else*"
    # Stale mtime is rejected; paths outside the root are refused.
    with pytest.raises(Conflict):
        app.save("k.mlx", 1.0, cells)
    with pytest.raises(PermissionError):
        app.doc("../etc/passwd.mlx")


def test_outputs_survive_unrelated_edits():
    from mlxkit.server import carry_outputs
    from mlxkit.outputs import parse_output_xml

    pkg, doc = load()
    first = "R1_part1 = 2 % Replace NaN with your answer in kOhm"
    text = to_text(doc).replace(first, first + "\nscale = 1;")
    new = from_text(text, doc)
    outs = parse_output_xml(carry_outputs(pkg.output_xml, doc, new))
    # All four outputs belong to statements that didn't change; later ones shift by one region.
    assert [(o.kind, o.regions[0]) for o in outs] == [("variable", 0), ("variable", 2), ("figure", 8), ("figure", 19)]
    # Editing a plotting statement drops only the figure it fed.
    text2 = to_text(doc).replace("ylim([-10 10])", "ylim([-5 5])")
    outs2 = parse_output_xml(carry_outputs(pkg.output_xml, doc, from_text(text2, doc)))
    assert [o.kind for o in outs2] == ["variable", "variable", "figure"]


def test_merge_outputs_replaces_only_what_ran():
    from mlxkit.server import merge_outputs

    old = [Output("text", [1], text="a"), Output("figure", [2, 3], png=b"old", extra={"id": "F"}),
           Output("text", [5], text="b")]
    new = [Output("figure", [3], png=b"new", extra={"handle": 1}), Output("text", [3], text="c")]
    merged = merge_outputs(old, new, {3}, {1: "F"})
    assert [(o.kind, o.regions) for o in merged] == [("text", [1]), ("figure", [2, 3]), ("text", [3]), ("text", [5])]
    assert merged[1].png == b"new" and merged[1].extra["id"] == "F"


def test_select_regions_by_section():
    from mlxkit.runner import parse_script
    from mlxkit.server import select_regions

    _, doc = load()
    _, regions, _ = parse_script(doc)
    first_code = next(i for i, b in enumerate(doc.blocks) if b.kind == "code")
    assert select_regions(doc, regions, "section", first_code) == list(range(11))
    assert select_regions(doc, regions, "upto", first_code) == [0]
    assert len(select_regions(doc, regions, "all", None)) == 21


@pytest.mark.skipif(shutil.which("octave") is None, reason="GNU Octave not installed")
def test_session_keeps_workspace_and_survives_interrupt(tmp_path):
    import threading
    from mlxkit.regions import split_regions
    from mlxkit.session import Session

    script = tmp_path / "s.mlx"
    shutil.copy(FIXTURE, script)
    lines = ["a = 20;", "b = a + 22", "k = 0; while true, k = k + 1; end", "c = b * 2"]
    regions = split_regions(lines)
    sess = Session(script)
    sess.start()
    try:
        r = sess.execute(lines, regions, [0], set())
        r = sess.execute(lines, regions, [1], set())  # uses `a` from the earlier request
        assert [(o.kind, o.text) for o in r.outputs] == [("variable", "42")]
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        r = sess.execute(lines, regions, [2], set(), cancel=cancel)
        assert r.cancelled and sess.alive
        r = sess.execute(lines, regions, [3], set())
        assert [(o.kind, o.text) for o in r.outputs] == [("variable", "84")]
        assert {"a", "b", "c", "k"} <= {v["name"] for v in r.variables}
    finally:
        sess.close()


def test_notebook_roundtrip(tmp_path):
    nbformat = pytest.importorskip("nbformat")
    from mlxkit.notebook import from_notebook, to_notebook
    from mlxkit.outputs import parse_output_xml

    nb = nbformat.reads(nbformat.writes(to_notebook(FIXTURE)), as_version=4)
    nbformat.validate(nb)
    md = "\n".join(c.source for c in nb.cells if c.cell_type == "markdown")
    assert "$$V_{out} = - 4V_{in} + 0.02 \\frac{dV_{in}}{dt}$$" in md  # plain TeX in the notebook
    with_image = next(c for c in nb.cells if "attachment:rId1.png" in c.source)
    assert "rId1.png" in with_image.attachments  # images travel as cell attachments
    out = tmp_path / "back.mlx"
    from_notebook(nb, FIXTURE, out)
    pkg, back = MlxPackage.read(FIXTURE), MlxPackage.read(out)
    assert back.document_xml == pkg.document_xml
    assert [(o.kind, o.regions) for o in parse_output_xml(back.output_xml)] == \
        [(o.kind, o.regions) for o in parse_output_xml(pkg.output_xml)]


def test_notebook_edits_flow_back(tmp_path):
    nbformat = pytest.importorskip("nbformat")
    from mlxkit.notebook import from_notebook, to_notebook

    src = make_script(tmp_path / "proj.mlx")
    nb = to_notebook(src)
    # Local functions come first so the notebook runs top to bottom.
    fn = nb.cells[1]
    assert fn.cell_type == "code" and fn.source.startswith("function y = height")
    fn.source = fn.source.replace("y = v*t - 9.81*t^2/2; % your code goes here", "y = v*t - gravity()*t^2/2;")
    heading = next(c for c in nb.cells if c.cell_type == "markdown" and "## Helpers" in c.source)
    heading.source = heading.source.replace("## Helpers", "## Helpers (edited) with $\\alpha^2$")
    out = tmp_path / "edited.mlx"
    from_notebook(nb, src, out)
    doc = Document(MlxPackage.read(out).document_xml)
    assert "y = v*t - gravity()*t^2/2;" in doc.script()
    # Functions are back at the end of the script, after the code that uses them.
    assert doc.script().index("y = height(v0, 1)") < doc.script().index("function y = height")
    edited = next(b for b in doc.blocks if "edited" in b.raw)
    assert edited.style == "heading" and "\\alpha^2" in edited.raw


def test_new_function_cells_move_to_the_end(tmp_path):
    nbformat = pytest.importorskip("nbformat")
    from mlxkit.notebook import from_notebook

    nb = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell("function y = twice(x)\n  y = 2*x;\nend"),
        nbformat.v4.new_markdown_cell("Use it:"),
        nbformat.v4.new_code_cell("twice(21)"),
    ])
    from_notebook(nb, None, tmp_path / "new.mlx")  # no base: a brand-new live script
    script = Document(MlxPackage.read(tmp_path / "new.mlx").document_xml).script()
    assert script.index("twice(21)") < script.index("function y = twice")


@pytest.mark.skipif(shutil.which("octave") is None, reason="GNU Octave not installed")
def test_kernel_over_jupyter_protocol(tmp_path, monkeypatch):
    pytest.importorskip("ipykernel")
    from jupyter_client.manager import start_new_kernel
    from mlxkit.kernel import install

    prefix = tmp_path / "jupyter"
    install(user=False, prefix=str(prefix))
    monkeypatch.setenv("JUPYTER_PATH", str(prefix / "share" / "jupyter"))
    monkeypatch.chdir(tmp_path)
    km, kc = start_new_kernel(kernel_name="mlxkit")
    try:
        def run(code):
            msg_id = kc.execute(code)
            outs = []
            while True:
                m = kc.get_iopub_msg(timeout=60)
                if m["parent_header"].get("msg_id") != msg_id:
                    continue
                if m["msg_type"] == "status" and m["content"]["execution_state"] == "idle":
                    return outs
                if m["msg_type"] in ("display_data", "error"):
                    outs.append(m["content"])
        assert run("a = 20;")  == []
        out = run("b = a + 22")
        assert out[0]["data"]["text/plain"] == "b = 42" and out[0]["metadata"]["mlxkit"]["kind"] == "variable"
        out = run("plot(1:3)")
        assert "image/png" in out[0]["data"]
        out = run("nope + 1")
        assert out[-1].get("ename") == "OctaveError"
    finally:
        kc.stop_channels()
        km.shutdown_kernel(now=True)
