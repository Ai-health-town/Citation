import csv
import json
from io import BytesIO, StringIO
from zipfile import ZipFile

import pytest
from docx import Document
from pypdf import PdfReader

from citation_audit.demo import demo_audit
from citation_audit.documents import extract_document
from citation_audit.reports import (
    all_exports, csv_bytes, export_zip, html_report, load_snapshot, snapshot_bytes,
)


def test_exports_open_in_their_native_readers():
    audit = demo_audit()
    files = all_exports(audit)
    document = Document(BytesIO(files["full_report.docx"]))
    assert "Citation Audit Pro" in "\n".join(p.text for p in document.paragraphs)
    pdf = PdfReader(BytesIO(files["full_report.pdf"]))
    assert len(pdf.pages) >= 1
    assert "Citation Audit Pro" in pdf.pages[0].extract_text()
    rows = list(csv.DictReader(StringIO(files["claim_audit.csv"].decode("utf-8-sig"))))
    assert len(rows) == len(audit.claims)
    with ZipFile(BytesIO(export_zip(audit, files))) as archive:
        assert set(archive.namelist()) == set(files)
        assert archive.testzip() is None


def test_json_roundtrip_preserves_unicode_and_evidence():
    audit = demo_audit()
    audit.title = "تحسين المراجع · Citation audit"
    result = load_snapshot(snapshot_bytes([audit]))[0]
    assert result.to_dict() == audit.to_dict()


@pytest.mark.parametrize("field,value", [("status", "invented_status"), ("paragraph", 999), ("source_number", 999), ("confidence", "certain")])
def test_invalid_snapshot_claims_are_rejected(field, value):
    payload = json.loads(snapshot_bytes([demo_audit()]))
    payload["audits"][0]["claims"][0][field] = value
    with pytest.raises(ValueError):
        load_snapshot(json.dumps(payload).encode())


def test_html_export_escapes_document_content():
    audit = demo_audit()
    audit.title = '<script>alert("x")</script>'
    report = html_report(audit).decode()
    assert "<script>" not in report
    assert "&lt;script&gt;" in report


def test_csv_formulas_are_not_executable():
    data = csv_bytes([{"Text": '=HYPERLINK("https://example.org")'}]).decode("utf-8-sig")
    assert "'=HYPERLINK" in data


def test_upload_word_and_pdf_extraction():
    files = all_exports(demo_audit())
    assert "Citation Audit Pro" in extract_document(files["full_report.docx"], "report.docx")
    assert "Citation Audit Pro" in extract_document(files["full_report.pdf"], "report.pdf")


def test_empty_and_wrong_file_types_fail_cleanly():
    with pytest.raises(ValueError, match="nonempty"):
        extract_document(b"", "empty.txt")
    with pytest.raises(ValueError, match="Use a PDF"):
        extract_document(b"This file is not a supported document.", "bad.exe")


def test_invalid_snapshot_is_rejected():
    with pytest.raises(ValueError):
        load_snapshot(b'{"api_key":"not-an-audit"}')
