"""Round-trip every .mlx in a corpus through the plain-text format.

usage: python tools/roundtrip_check.py CORPUS_DIR
"""
import sys
from pathlib import Path

from mlxkit.document import Document
from mlxkit.livetext import from_text, markdown_paragraph, paragraph_markdown, to_text
from mlxkit.package import MlxPackage

WRAP = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{}</w:body></w:document>'


def md_of(b):
    md = paragraph_markdown(b)
    return "- " + md if md is not None and b.style == "ListParagraph" else md


def main():
    ok = fail = regen_ok = regen_fail = 0
    examples = []
    for f in sorted(Path(sys.argv[1]).glob("*.mlx")):
        doc = Document(MlxPackage.read(f).document_xml)
        if from_text(to_text(doc), doc).to_xml() == doc.to_xml():
            ok += 1
        else:
            fail += 1
            print("ROUNDTRIP FAIL", f.name)
        # Regenerating a paragraph from its markdown must give back the same markdown.
        for b in doc.blocks:
            md = md_of(b)
            if md is None:
                continue
            md2 = md_of(Document(WRAP.format(markdown_paragraph(md))).blocks[0])
            if md2 == md:
                regen_ok += 1
            else:
                regen_fail += 1
                if len(examples) < 6:
                    examples.append((f.name, md, md2))
    print(f"roundtrip ok={ok} fail={fail}; paragraph regeneration ok={regen_ok} fail={regen_fail}")
    for name, a, b in examples:
        print(name, "\n  A", repr(a[:200]), "\n  B", repr(b[:200]))


if __name__ == "__main__":
    main()
