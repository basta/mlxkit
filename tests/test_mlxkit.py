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
