"""Compare mlxkit's parsing against what MATLAB saved in a corpus of real .mlx files.

usage: python tools/corpus_check.py CORPUS_DIR [-v]
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from mlxkit.document import Document
from mlxkit.package import MlxPackage
from mlxkit.regions import split_regions


def matlab_regions(output_xml: bytes):
    root = ET.fromstring(output_xml)
    ra = root.find("regionArray")
    if ra is None:
        return None
    out = []
    for e in ra:
        c = e.find("code")
        out.append((int(e.find("startLine").text), int(e.find("endLine").text),
                    c.find("sectionBreak").text == "true", c.find("endOfSection").text == "true"))
    return out


def main():
    corpus, verbose = Path(sys.argv[1]), "-v" in sys.argv
    stats = {"files": 0, "roundtrip_ok": 0, "with_regions": 0, "regions_exact": 0, "lines_exact": 0}
    for f in sorted(corpus.glob("*.mlx")):
        stats["files"] += 1
        pkg = MlxPackage.read(f)
        doc = Document(pkg.document_xml)
        if doc.to_xml() == pkg.document_xml:
            stats["roundtrip_ok"] += 1
        elif verbose:
            print("ROUNDTRIP FAIL", f.name)
        if not pkg.output_xml:
            continue
        expected = matlab_regions(pkg.output_xml)
        if not expected:
            continue
        lines = doc.lines()
        if not all(s < len(lines) and lines[s] is not None and lines[s].strip() for s, *_ in expected):
            stats["stale"] = stats.get("stale", 0) + 1  # saved before later edits; not comparable
            continue
        stats["with_regions"] += 1
        sections = {b.first_line for b in doc.blocks if b.kind == "sectionbreak"}
        got = [(r.start_line, r.end_line, r.section_break, r.end_of_section)
               for r in split_regions(doc.lines(), sections)]
        if got == expected:
            stats["regions_exact"] += 1
            stats["lines_exact"] += 1
            continue
        if [g[:2] for g in got] == [e[:2] for e in expected]:
            stats["lines_exact"] += 1
        if verbose:
            print(f"\n=== {f.name}: got {len(got)} regions, MATLAB {len(expected)}")
            gs, es = set(got), set(expected)
            for r in sorted(es - gs)[:6]:
                print("  missing ", r, repr(lines[r[0]]) if r[0] < len(lines) else "")
            for r in sorted(gs - es)[:6]:
                print("  extra   ", r, repr(lines[r[0]]))
    print(stats)


if __name__ == "__main__":
    main()
