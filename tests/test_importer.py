import base64
import io
import unittest
import zipfile
from importlib.util import find_spec

from den.importer import extract_document


def payload(name,data):
    return {"name":name,"data":base64.b64encode(data).decode()}


def small_pdf():
    stream = b"BT /F1 14 Tf 30 100 Td (The code is Rowan-27.) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length %d >>\nstream\n" % len(stream)+stream+b"\nendstream"]
    data=bytearray(b"%PDF-1.4\n")
    offsets=[]
    for n,item in enumerate(objects,1):
        offsets.append(len(data))
        data.extend(b"%d 0 obj\n" % n+item+b"\nendobj\n")
    xref=len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(b"%010d 00000 n \n" % offset)
    data.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % xref)
    return bytes(data)


class ImportTests(unittest.TestCase):
    def test_docx_paragraphs(self):
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,"w") as doc:
            doc.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_document(payload("notes.docx",archive.getvalue())),("notes.docx","First\nSecond"))
        with self.assertRaises(ValueError):
            extract_document(payload("broken.docx",b"bad"))

    @unittest.skipUnless(find_spec("pypdf"),"Optional PDF reader unavailable")
    def test_pdf_page_reference(self):
        _, text=extract_document(payload("notes.pdf",small_pdf()))
        self.assertIn("[Page 1]",text)
        self.assertIn("Rowan-27",text)
