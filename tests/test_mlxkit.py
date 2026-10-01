import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mlxkit.document import Document
from mlxkit.livetext import from_text, to_text
from mlxkit.outputs import Output, build_output_xml, code_line_numbers
from mlxkit.package import MlxPackage
from mlxkit.regions import split_regions

FIXTURE = Path(__file__).parent / "fixtures" / "OpAmpLabSoln.mlx"


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
    text = text.replace("    beta = 0; % todo", "    beta = atan(lr*tan(df)/(lf+lr));")
    text = text.replace("%[text] ## Introduction", "%[text] ## Intro **edited**")
    new = from_text(text, doc)
    code = "\n".join(b.code for b in new.code_blocks)
    assert "beta = atan(lr*tan(df)/(lf+lr));" in code
    assert len(new.blocks) == len(doc.blocks)
    edited = [b for b in new.blocks if "Intro" in b.raw][0]
    assert edited.style == "heading" and "<w:b/>" in edited.raw
    # Untouched paragraphs keep their original XML, including the image.
    assert sum(b.raw == o.raw for b, o in zip(new.blocks, doc.blocks)) == len(doc.blocks) - 2


def test_classic_section_titles_become_headings():
    _, doc = load()
    new = from_text("%% Setup\nx = 1;\n", doc)
    assert [b.kind for b in new.blocks] == ["sectionbreak", "text", "code"]
    assert new.blocks[1].style == "heading"


def test_output_xml_links_outputs_to_regions():
    _, doc = load()
    regions = regions_of(doc)
    lines = doc.lines()
    out = build_output_xml(regions, [Output("text", [65], text="hi\n")], code_line_numbers(lines))
    root = ET.fromstring(out)
    element = root.find("outputArray")[0]
    assert element.find("type").text == "text"
    region = root.find("regionArray")[65]
    assert [i.text for i in region.find("outputIndexes")] == ["0"]
    # lineNumbers count code lines only, 1-based.
    expected = code_line_numbers(lines)[regions[65].start_line]
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
    text = to_text(doc).replace("lr = 1420; % m", "lr = 1420; % m\nwheelbase = lf + lr;")
    new = from_text(text, doc)
    outs = parse_output_xml(carry_outputs(pkg.output_xml, doc, new))
    # All five outputs belong to statements that didn't change; they shift by one region.
    assert [o.kind for o in outs] == ["figure", "figure", "figure", "text", "text"]
    assert outs[3].regions == [66]
    # Editing a plotting statement drops only the figure it fed.
    text2 = to_text(doc).replace("title('Path'", "title('Path'")
    outs2 = parse_output_xml(carry_outputs(pkg.output_xml, doc, from_text(text2, doc)))
    assert [o.kind for o in outs2] == ["figure", "figure", "text", "text"]


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
    assert select_regions(doc, regions, "section", first_code) == [0, 1]
    upto = select_regions(doc, regions, "upto", first_code)
    assert upto == [0, 1]
    assert len(select_regions(doc, regions, "all", None)) == 68


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
