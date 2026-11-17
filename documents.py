"""Extract text from in-memory documents; never execute uploaded content."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

MAX_BYTES = 15 * 1024 * 1024
MAX_CHARS = 1_000_000
MAX_PAGES = 250


def _check_docx(data: bytes) -> None:
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 60 * 1024 * 1024:
                raise ValueError("This DOCX expands beyond the 60 MB extraction limit.")
            if "word/document.xml" not in archive.namelist():
                raise ValueError("The file is not a valid Word document.")
    except BadZipFile as exc:
        raise ValueError("The Word document is damaged or has the wrong extension.") from exc


def extract_document(data: bytes, filename: str) -> str:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Please upload a nonempty file smaller than 15 MB.")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf" or data.startswith(b"%PDF-"):
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("The PDF is encrypted. Upload an unlocked copy.")
            if len(reader.pages) > MAX_PAGES:
                raise ValueError(f"PDFs are limited to {MAX_PAGES} pages; upload the relevant section.")
            parts, length = [], 0
            for page in reader.pages:
                part = page.extract_text() or ""
                length += len(part)
                if length > MAX_CHARS:
                    raise ValueError("Document text exceeds the extraction limit; upload a shorter section.")
                parts.append(part)
            text = "\n\n".join(parts)
        elif suffix == ".docx":
            _check_docx(data)
            from docx import Document
            document = Document(BytesIO(data))
            text = "\n\n".join(p.text for p in document.paragraphs)
            for table in document.tables:
                text += "\n\n" + "\n".join(" | ".join(c.text for c in row.cells) for row in table.rows)
        elif suffix in {".txt", ".md", ".csv"}:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = data.decode("utf-16")
            else:
                text = data.decode("utf-8-sig")
        else:
            raise ValueError("Use a PDF, DOCX, TXT, or Markdown document.")
    except ValueError:
        raise
    except UnicodeDecodeError as exc:
        raise ValueError("Save the text file as UTF-8 and upload it again.") from exc
    except Exception as exc:
        raise ValueError("The document could not be read. Try an unlocked PDF, DOCX, or UTF-8 text file.") from exc
    if len(text) > MAX_CHARS:
        raise ValueError("Document text exceeds the extraction limit; upload a shorter section.")
    if len(text.strip()) < 20:
        raise ValueError("No readable text found. Scanned PDFs need OCR before uploading.")
    return text.replace("\x00", "").strip()
