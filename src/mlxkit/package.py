"""Reading and writing the .mlx container (an Open Packaging Conventions zip)."""
from __future__ import annotations

import zipfile
from pathlib import Path

DOCUMENT = "matlab/document.xml"
OUTPUT = "matlab/output.xml"
DOCUMENT_RELS = "matlab/_rels/document.xml.rels"

OUTPUT_REL = (
    '<Relationship Id="rId2" Target="matlab/output.xml" '
    'Type="http://schemas.mathworks.com/matlab/code/2013/relationships/output"/>'
)
OUTPUT_CONTENT_TYPE = '<Override ContentType="text/xml" PartName="/matlab/output.xml"/>'


class MlxPackage:
    """All parts of an .mlx file, kept in their original order so unknown parts survive a round trip."""

    def __init__(self, parts: dict[str, bytes]):
        self.parts = parts

    @classmethod
    def read(cls, path: str | Path) -> "MlxPackage":
        with zipfile.ZipFile(path) as z:
            return cls({info.filename: z.read(info) for info in z.infolist()})

    def write(self, path: str | Path) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            # [Content_Types].xml must come first for OPC readers.
            names = sorted(self.parts, key=lambda n: n != "[Content_Types].xml")
            for name in names:
                z.writestr(name, self.parts[name])

    @property
    def document_xml(self) -> bytes:
        return self.parts[DOCUMENT]

    @document_xml.setter
    def document_xml(self, data: bytes) -> None:
        self.parts[DOCUMENT] = data

    @property
    def output_xml(self) -> bytes | None:
        return self.parts.get(OUTPUT)

    @output_xml.setter
    def output_xml(self, data: bytes) -> None:
        if OUTPUT not in self.parts:
            self._register_output_part()
        self.parts[OUTPUT] = data

    def _register_output_part(self) -> None:
        rels = self.parts["_rels/.rels"].decode()
        if "matlab/output.xml" not in rels:
            rels = rels.replace("</Relationships>", OUTPUT_REL.replace("rId2", "rIdOutput") + "</Relationships>")
            self.parts["_rels/.rels"] = rels.encode()
        ct = self.parts["[Content_Types].xml"].decode()
        if "/matlab/output.xml" not in ct:
            ct = ct.replace("</Types>", OUTPUT_CONTENT_TYPE + "</Types>")
            self.parts["[Content_Types].xml"] = ct.encode()

    def media(self, rel_id: str) -> tuple[str, bytes] | None:
        """Resolve an image relationship id from document.xml to (part name, bytes)."""
        import re

        rels = self.parts.get(DOCUMENT_RELS, b"").decode()
        m = re.search(rf'<Relationship[^>]*Id="{re.escape(rel_id)}"[^>]*/>', rels)
        if not m:
            return None
        target = re.search(r'Target="([^"]+)"', m.group(0)).group(1)
        name = str(Path("matlab", target)).replace("matlab/../", "")
        return (name, self.parts[name]) if name in self.parts else None
