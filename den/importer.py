"""Import text-bearing PDFs and the main body of Word documents without retaining binary files."""

import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from .documents import MAX_TEXT


def extract_document(body):
    name, encoded = body.get("name"), body.get("data")
    if not isinstance(name,str) or not isinstance(encoded,str):
        raise ValueError("Choose a PDF or Word document.")
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf",".docx"}:
        raise ValueError("Choose a PDF or DOCX file.")
    content = base64.b64decode(encoded, validate=True)
    if not 0 < len(content) <= 5_000_000:
        raise ValueError("Choose a document up to 5 MB.")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                part = archive.getinfo("word/document.xml")
                if part.file_size > 5_000_000:
                    raise ValueError("The Word document exceeds the text import limit.")
                xml = archive.read(part)
            if b"<!DOCTYPE" in xml.upper():
                raise ValueError("Unsupported Word document structure.")
            root = ET.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            text = "\n".join("".join(t.text or "" for t in paragraph.iter(ns+"t"))
                             for paragraph in root.iter(ns+"p"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError) as error:
            raise ValueError("Unable to read this Word document.") from error
    else:
        try:
            proc = subprocess.run([sys.executable,"-m","den.importer"], input=content, capture_output=True,
                                  cwd=Path(__file__).resolve().parent.parent, timeout=20,
                                  creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except subprocess.TimeoutExpired as error:
            raise ValueError("PDF extraction timed out.") from error
        if proc.returncode:
            raise ValueError("Unable to read this PDF. Check for encryption or install pypdf.")
        payload = json.loads(proc.stdout)
        if "error" in payload:
            raise ValueError(payload["error"])
        text = payload["text"]
    if not text.strip() or len(text)>MAX_TEXT:
        raise ValueError("Import a document with selectable text, up to 120,000 extracted characters.")
    return name, text


def read_pdf():
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(sys.stdin.buffer.read()))
    if reader.is_encrypted or len(reader.pages)>300:
        return {"error":"Choose an unencrypted PDF of up to 300 pages."}
    lines=[]
    size=0
    for number,page in enumerate(reader.pages,1):
        content=(page.extract_text() or "").strip()
        if content:
            passage=f"[Page {number}]\n{content}"
            lines.append(passage)
            size+=len(passage)+2
            if size>MAX_TEXT:
                return {"error":"PDF exceeds 120,000 extracted characters."}
    return {"text":"\n\n".join(lines)}


if __name__=="__main__":
    print(json.dumps(read_pdf()))
