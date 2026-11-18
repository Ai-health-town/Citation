import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from citation_audit.reports import load_snapshot


def test_desktop_database_migration_is_read_only_and_importable(tmp_path):
    database = tmp_path / "custom.db"
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE AuditSession (id TEXT, status TEXT, createdAt TEXT, paperText TEXT, sources TEXT, paragraphs TEXT, auditResults TEXT, correctedPaper TEXT, apa7ReferenceList TEXT, citationStyle TEXT, referenceListUrl TEXT)')
        paper = "The study reported an association between reminders and attendance (Smith, 2024)."
        sources = [{"sourceNumber": 1, "title": "Reminder study", "author": "Smith", "publicationDate": "2024", "extractionStatus": "success", "extractedText": "The study reported an association."}]
        paragraphs = [{"paragraphNumber": 1, "originalText": paper, "existingCitations": ["(Smith, 2024)"]}]
        claims = [{"paragraphNumber": 1, "originalClaim": paper, "citationStatus": "correct", "bestSupportingSourceNumber": 1, "confidenceLevel": "high"}]
        connection.execute('INSERT INTO AuditSession VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           ("legacy-1", "completed", "2026-11-18T01:00:00Z", paper, json.dumps(sources), json.dumps(paragraphs), json.dumps(claims), paper, "Smith (2024). Reminder study.", "APA7", "pasted-text://references"))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    output = tmp_path / "legacy.json"
    script = Path(__file__).resolve().parents[1] / "tools" / "import_legacy_history.py"
    result = subprocess.run([sys.executable, str(script), str(database), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    audit = load_snapshot(output.read_bytes())[0]
    assert audit.id == "legacy-1"
    assert audit.claims[0].status == "correct"
    assert "not reverified" in audit.claims[0].notes
