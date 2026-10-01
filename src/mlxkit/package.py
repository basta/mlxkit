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


# A new, empty live script, laid out exactly like one saved by MATLAB R2021a.
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default ContentType="image/png" Extension="png"/>'
    '<Default ContentType="application/vnd.openxmlformats-package.relationships+xml" Extension="rels"/>'
    '<Default ContentType="application/vnd.mathworks.matlab.code.document+xml" Extension="xml"/>'
    '<Override ContentType="text/xml" PartName="/matlab/output.xml"/>'
    '<Override ContentType="application/vnd.openxmlformats-package.core-properties+xml" PartName="/metadata/coreProperties.xml"/>'
    '<Override ContentType="application/vnd.mathworks.package.coreProperties+xml" PartName="/metadata/mwcoreProperties.xml"/>'
    '<Override ContentType="application/vnd.mathworks.package.corePropertiesExtension+xml" PartName="/metadata/mwcorePropertiesExtension.xml"/>'
    '<Override ContentType="application/vnd.mathworks.package.corePropertiesReleaseInfo+xml" PartName="/metadata/mwcorePropertiesReleaseInfo.xml"/>'
    "</Types>")
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="matlab/document.xml" Type="http://schemas.mathworks.com/matlab/code/2013/relationships/document"/>'
    '<Relationship Id="rId2" Target="matlab/output.xml" Type="http://schemas.mathworks.com/matlab/code/2013/relationships/output"/>'
    '<Relationship Id="rId3" Target="metadata/mwcoreProperties.xml" Type="http://schemas.mathworks.com/package/2012/relationships/coreProperties"/>'
    '<Relationship Id="rId4" Target="metadata/mwcorePropertiesExtension.xml" Type="http://schemas.mathworks.com/package/2014/relationships/corePropertiesExtension"/>'
    '<Relationship Id="rId5" Target="metadata/mwcorePropertiesReleaseInfo.xml" Type="http://schemas.mathworks.com/package/2019/relationships/corePropertiesReleaseInfo"/>'
    '<Relationship Id="rId6" Target="metadata/coreProperties.xml" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"/>'
    "</Relationships>")
_RELEASE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Version information for MathWorks R2021a Release -->
<MathWorks_version_info>
  <version>9.10.0.1739362</version>
  <release>R2021a</release>
  <description>Update 5</description>
  <date>Aug 09 2021</date>
  <checksum>355558435</checksum>
</MathWorks_version_info>
"""


def new_package() -> MlxPackage:
    """An empty live script (no text, code or outputs)."""
    import datetime
    import uuid

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    mw = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><mwcoreProperties '
          'xmlns="http://schemas.mathworks.com/package/2012/coreProperties"><contentType>application/vnd.mathworks.matlab.code'
          '</contentType><contentTypeFriendlyName>MATLAB Code</contentTypeFriendlyName><matlabRelease>R2021a</matlabRelease>'
          '</mwcoreProperties>')
    ext = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><mwcoreProperties '
           f'xmlns="http://schemas.mathworks.com/package/2014/corePropertiesExtension"><uuid>{uuid.uuid4()}</uuid>'
           '</mwcoreProperties>')
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document '
           'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body></w:body></w:document>')
    out = ('<?xml version="1.0" encoding="UTF-8"?><embeddedOutputs><metaData><evaluationState>manual</evaluationState>'
           '<layoutState>inline</layoutState><outputStatus>ready</outputStatus></metaData>'
           '<outputArray type="array"/><regionArray type="array"/></embeddedOutputs>')
    return MlxPackage({
        "[Content_Types].xml": _CONTENT_TYPES.encode(),
        "_rels/.rels": _RELS.encode(),
        DOCUMENT: doc.encode(),
        OUTPUT: out.encode(),
        "metadata/coreProperties.xml": core.encode(),
        "metadata/mwcoreProperties.xml": mw.encode(),
        "metadata/mwcorePropertiesExtension.xml": ext.encode(),
        "metadata/mwcorePropertiesReleaseInfo.xml": _RELEASE_INFO.encode(),
    })
