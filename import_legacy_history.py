"""Convert the original Prisma SQLite history to a Streamlit JSON backup.

Run locally. The input database is always opened read-only. This script never
loads the old .env or .z-ai-config files.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from citation_audit.models import Audit, Claim, Paragraph, Source, STATUSES, STYLE_LABELS
from citation_audit.parsing import paragraphs_from_text
from citation_audit.reports import snapshot_bytes


def decode_records(value: str) -> list[dict]:
    try:
        result = json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    return [x for x in result if isinstance(x, dict)] if isinstance(result, list) else []


def convert_row(row: dict) -> Audit:
    paper = str(row.get("paperText", ""))
    paragraphs = [Paragraph(int(p["paragraphNumber"]), str(p.get("originalText", "")),
                            [str(c) for c in p.get("existingCitations", [])])
                  for p in decode_records(row.get("paragraphs", "[]")) if isinstance(p.get("paragraphNumber"), int)]
    paragraphs = paragraphs or paragraphs_from_text(paper)
    source_status = {"success": "success", "failed": "failed", "pending": "pending", "extracting": "pending"}
    sources = [Source(number=int(s["sourceNumber"]), url=str(s.get("url", "")), title=str(s.get("title", "")),
                      author=str(s.get("author", "")), year=str(s.get("publicationDate", ""))[:4],
                      source_type=str(s.get("sourceType", "webpage")), status=source_status.get(s.get("extractionStatus"), "unavailable"),
                      text=str(s.get("extractedText", "")), access_date=str(s.get("accessDate", "")),
                      notes=str(s.get("notes", "")) + " Imported from the original desktop app.", error=str(s.get("errorMessage", "")))
               for s in decode_records(row.get("sources", "[]")) if isinstance(s.get("sourceNumber"), int)]
    source_numbers = {s.number for s in sources}
    paragraph_numbers = {p.number for p in paragraphs}
    claims = []
    for value in decode_records(row.get("auditResults", "[]")):
        paragraph = value.get("paragraphNumber")
        if paragraph not in paragraph_numbers:
            continue
        source = value.get("bestSupportingSourceNumber")
        source = source if source in source_numbers else None
        status = value.get("citationStatus", "needs_review")
        confidence = value.get("confidenceLevel", "low")
        claims.append(Claim(id=len(claims) + 1, paragraph=paragraph, text=str(value.get("originalClaim", "")),
                            current_citation=str(value.get("currentCitation", "")), status=status if status in STATUSES else "needs_review",
                            source_number=source, source_title=str(value.get("bestSupportingSourceTitle", "")),
                            evidence=str(value.get("evidenceSummary", "")), suggested_citation=str(value.get("suggestedCitation", "")),
                            suggested_rewrite=str(value.get("suggestedRewrite", "")), confidence=confidence if confidence in {"high", "medium", "low"} else "low",
                            notes=str(value.get("notes", "")) + " Legacy verdict; not reverified by the Python pipeline."))
    style = row.get("citationStyle", "APA7")
    return Audit(id=str(row["id"]), created_at=str(row.get("createdAt", "")), title="Imported desktop audit · " + str(row["id"])[:12],
                 citation_style=style if style in STYLE_LABELS else "APA7", mode="Imported desktop audit", paper_text=paper,
                 reference_input=str(row.get("referenceListUrl", "")), sources=sources, paragraphs=paragraphs, claims=claims,
                 corrected_paper=str(row.get("correctedPaper") or paper), reference_list=str(row.get("apa7ReferenceList", "")),
                 warnings=["Imported desktop judgments and proposed edits are retained. Evidence quotes were not reverified by the Python pipeline."])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, default=Path("desktop_audits.json"))
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("The database file does not exist.")
    try:
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT * FROM "AuditSession" WHERE "status" = ? ORDER BY "createdAt" DESC LIMIT 20', ("completed",)).fetchall()
        if not rows:
            parser.error("No completed desktop audits were found in this database.")
        audits = [convert_row(dict(row)) for row in reversed(rows)]
        # Run the same schema validation used by the app before saving the result.
        for audit in audits:
            Audit.from_dict(audit.to_dict())
        args.output.write_bytes(snapshot_bytes(audits))
        print(f"Converted {len(audits)} audits to {args.output}")
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Could not convert desktop history: {exc}\n")


if __name__ == "__main__":
    main()
