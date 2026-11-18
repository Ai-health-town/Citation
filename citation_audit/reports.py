"""Downloadable reports and validated, portable audit history."""
from __future__ import annotations

import csv
import html
import json
import re
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .models import Audit, LABELS, STYLE_LABELS

MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024


def claim_rows(audit: Audit) -> list[dict]:
    return [{"Claim": c.id, "Paragraph": c.paragraph, "Original statement": c.text,
             "Current citation": c.current_citation, "Status": LABELS[c.status],
             "Source #": c.source_number, "Supporting source": c.source_title,
             "Evidence summary": c.evidence, "Evidence quote": c.evidence_quote,
             "Suggested citation": c.suggested_citation, "Suggested rewrite": c.suggested_rewrite,
             "Confidence": c.confidence, "Notes": c.notes} for c in audit.claims]


def source_rows(audit: Audit) -> list[dict]:
    return [{"#": s.number, "Title": s.title, "Author": s.author, "Year": s.year,
             "URL": s.url, "DOI": s.doi, "Status": s.status, "Type": s.source_type,
             "Metadata retrieved": s.metadata_verified, "Text characters": len(s.text),
             "Notes": s.notes, "Error": s.error} for s in audit.sources]


def csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    # Protect CSV exports from executing spreadsheet formulas.
    for row in rows:
        writer.writerow({k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v)
                         for k, v in row.items()})
    return buffer.getvalue().encode("utf-8-sig")


def attention_report(audit: Audit) -> str:
    claims = [c for c in audit.claims if c.status != "correct"]
    if not claims:
        return "No flagged claims in this audit. Review the scope and limitations before accepting the result."
    blocks = [f"# Claims requiring attention\n\n{len(claims)} of {len(audit.claims)} audited statements require attention."]
    for c in claims:
        blocks.append(f"## Claim {c.id} · Paragraph {c.paragraph}\n\n{c.text}\n\nStatus: {LABELS[c.status]}\n\nEvidence: {c.evidence or 'Not assessed'}\n\nQuote: {c.evidence_quote or 'No verified quote'}\n\nNotes: {c.notes}")
    return "\n\n".join(blocks)


def failed_report(audit: Audit) -> str:
    unavailable = [s for s in audit.sources if s.status != "success"]
    blocks = ["# Source access report"]
    if not unavailable:
        blocks.append("Readable text was obtained for all sources. Some sources may be limited to excerpts or abstracts; check each source's notes.")
    for s in unavailable:
        blocks.append(f"## Source {s.number}: {s.title}\n\n{s.url}\n\nStatus: {s.status}\n\n{s.error}\n\n{s.notes}")
    return "\n\n".join(blocks)


def full_report(audit: Audit) -> str:
    blocks = [f"# Citation Audit Pro\n\n{audit.title}",
              f"Created: {audit.created_at}\n\nMode: {audit.mode}\n\nStyle: {STYLE_LABELS[audit.citation_style]}",
              f"Sources: {len(audit.sources)} · Audited statements: {len(audit.claims)} · Requires attention: {audit.needs_attention}",
              "## Scope\n\nAI evidence judgments and proposed rewrites require author review. Retrieved excerpts may not cover the entire source. A word match is not evidence of support. This is a citation audit, not a plagiarism or Turnitin report."]
    if audit.is_demo:
        blocks.append("DEMO: All example documents and results are fictional demonstration data.")
    if audit.warnings:
        blocks.append("## Audit notes\n\n" + "\n\n".join(audit.warnings))
    for c in audit.claims:
        blocks.append(f"## Claim {c.id} · Paragraph {c.paragraph}\n\n{c.text}\n\nStatus: {LABELS[c.status]} · Confidence: {c.confidence}\n\nCurrent citation: {c.current_citation or 'None detected'}\n\nSupporting source: {c.source_number or 'Not identified'} — {c.source_title}\n\nEvidence: {c.evidence or 'Not assessed'}\n\nQuote: {c.evidence_quote or 'No verified quote'}\n\nSuggested citation: {c.suggested_citation or 'None'}\n\nSuggested rewrite: {c.suggested_rewrite or 'None'}\n\nNotes: {c.notes}")
    blocks.extend(["# Proposed corrected draft\n\n" + audit.corrected_paper,
                   "# Reference list\n\n" + audit.reference_list, failed_report(audit)])
    return "\n\n".join(blocks)


def html_report(audit: Audit) -> bytes:
    body = []
    for chunk in full_report(audit).split("\n\n"):
        if chunk.startswith("# "):
            body.append("<h1>" + html.escape(chunk[2:]) + "</h1>")
        elif chunk.startswith("## "):
            body.append("<h2>" + html.escape(chunk[3:]) + "</h2>")
        else:
            body.append("<p>" + html.escape(chunk).replace("\n", "<br>") + "</p>")
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Citation Audit Pro report</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:920px;margin:50px auto;padding:0 25px;color:#172337}h1{color:#9b242b}h2{border-top:1px solid #ddd;padding-top:20px;font-size:20px}p{overflow-wrap:anywhere;white-space:normal}@media print{body{margin:0;max-width:none}h2{break-after:avoid}}</style><body>' + "\n".join(body) + "</body></html>"
    return document.encode("utf-8")


def docx_bytes(audit: Audit, corrected_only: bool = False) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin = Inches(.75)
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(11)
    for name in ["Title", "Heading 1", "Heading 2"]:
        document.styles[name].font.color.rgb = RGBColor.from_string("9B242B")
    text = ("# Proposed corrected draft\n\n" + audit.corrected_paper + "\n\n# References\n\n" + audit.reference_list) if corrected_only else full_report(audit)
    for chunk in text.split("\n\n"):
        chunk = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", chunk)
        if chunk.startswith("# "):
            document.add_heading(chunk[2:], level=1)
        elif chunk.startswith("## "):
            document.add_heading(chunk[3:], level=2)
        else:
            document.add_paragraph(chunk)
    footer = section.footer.paragraphs[0]
    footer.text = "Citation Audit Pro · Review proposed changes before submission"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_bytes(audit: Audit) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    font_path = Path(__file__).resolve().parent.parent / "assets" / "DejaVuSans.ttf"
    font = "Helvetica"
    if font_path.exists():
        if "AuditUnicode" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("AuditUnicode", str(font_path)))
        font = "AuditUnicode"
    styles = getSampleStyleSheet()
    body = ParagraphStyle("AuditBody", parent=styles["BodyText"], fontName=font, fontSize=9,
                          leading=14, spaceAfter=9, alignment=TA_LEFT, wordWrap="LTR")
    heading = ParagraphStyle("AuditHeading", parent=body, fontSize=14, leading=19,
                             textColor=colors.HexColor("#9b242b"), spaceBefore=10, keepWithNext=True)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=42, bottomMargin=42, title=audit.title, author="Citation Audit Pro")
    story = []
    for chunk in full_report(audit).split("\n\n"):
        is_heading = chunk.startswith("#")
        chunk = chunk.lstrip("# ") if is_heading else chunk
        # Bound individual flowables even for very long paragraphs/URLs.
        pieces = [chunk[i:i + 1800] for i in range(0, len(chunk), 1800)] or [""]
        for piece in pieces:
            story.append(Paragraph(html.escape(piece).replace("\n", "<br/>"), heading if is_heading else body))
        story.append(Spacer(1, 3))
    def page_number(canvas, document):
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawRightString(A4[0] - 42, 24, f"Citation Audit Pro · {document.page}")
    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return buffer.getvalue()


def snapshot_bytes(audits: list[Audit]) -> bytes:
    return json.dumps({"format": "citation-audit-pro", "version": 1, "audits": [a.to_dict() for a in audits]}, ensure_ascii=False, indent=2).encode("utf-8")


def load_snapshot(data: bytes) -> list[Audit]:
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("The audit backup is larger than 50 MB.")
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("Upload a valid Citation Audit Pro JSON backup.") from exc
    if not isinstance(payload, dict) or payload.get("format") != "citation-audit-pro" or payload.get("version") != 1:
        raise ValueError("This is not a supported Citation Audit Pro backup.")
    audits = payload.get("audits")
    if not isinstance(audits, list) or not 1 <= len(audits) <= 20:
        raise ValueError("A backup must contain between 1 and 20 audits.")
    return [Audit.from_dict(audit) for audit in audits]


def all_exports(audit: Audit) -> dict[str, bytes]:
    return {
        "full_report.docx": docx_bytes(audit), "full_report.pdf": pdf_bytes(audit),
        "full_report.html": html_report(audit), "full_report.md": full_report(audit).encode("utf-8"),
        "proposed_paper.docx": docx_bytes(audit, corrected_only=True),
        "proposed_paper.txt": audit.corrected_paper.encode("utf-8"),
        "references.txt": audit.reference_list.encode("utf-8"),
        "claim_audit.csv": csv_bytes(claim_rows(audit)), "sources.csv": csv_bytes(source_rows(audit)),
        "claims_to_review.md": attention_report(audit).encode("utf-8"),
        "source_access.md": failed_report(audit).encode("utf-8"), "audit_backup.json": snapshot_bytes([audit]),
    }


def export_zip(audit: Audit, exports: dict[str, bytes] | None = None) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, data in (exports if exports is not None else all_exports(audit)).items():
            archive.writestr(name, data)
    return buffer.getvalue()
