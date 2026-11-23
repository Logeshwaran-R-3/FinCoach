"""DOCX extraction: python-docx with a stdlib zip/xml fallback."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

from fincoach.ingest.extract_pdf import txns_from_lines


def _fallback_lines(data: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ElementTree.fromstring(xml)
    lines = []
    for para in root.findall(".//w:p", ns):
        texts = [t.text for t in para.findall(".//w:t", ns) if t.text]
        if texts:
            lines.append(" ".join(texts))
    return lines


def extract_docx(data: bytes, filename: str = "") -> list:
    try:
        import docx  # python-docx

        doc = docx.Document(io.BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text for c in row.cells))
    except ImportError:
        lines = _fallback_lines(data)
    return txns_from_lines(lines)
