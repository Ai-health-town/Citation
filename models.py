"""Portable, explicit audit records. No credentials are stored in a report."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

STATUSES = (
    "correct", "missing_citation", "wrong_citation", "weak_citation",
    "partially_supported", "unsupported_claim", "overclaim", "needs_review",
)
STYLE_LABELS = {
    "APA7": "APA 7", "APA6": "APA 6", "MLA9": "MLA 9",
    "Chicago17": "Chicago 17 (author–date)", "Harvard": "Harvard",
}
LABELS = {s: s.replace("_", " ").capitalize() for s in STATUSES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Source:
    number: int
    raw_reference: str = ""
    url: str = ""
    doi: str = ""
    title: str = ""
    author: str = ""
    year: str = ""
    source_type: str = "webpage"
    status: str = "pending"
    text: str = ""
    access_date: str = ""
    notes: str = ""
    error: str = ""
    metadata_verified: bool = False


@dataclass
class Paragraph:
    number: int
    text: str
    citations: list[str] = field(default_factory=list)


@dataclass
class Claim:
    id: int
    paragraph: int
    text: str
    current_citation: str = ""
    status: str = "needs_review"
    source_number: int | None = None
    source_title: str = ""
    evidence: str = ""
    evidence_quote: str = ""
    suggested_citation: str = ""
    suggested_rewrite: str = ""
    confidence: str = "low"
    notes: str = ""


@dataclass
class Audit:
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    title: str = "Untitled audit"
    citation_style: str = "APA7"
    mode: str = "Structure only"
    paper_text: str = ""
    reference_input: str = ""
    sources: list[Source] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    corrected_paper: str = ""
    reference_list: str = ""
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0
    is_demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def summary(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self.claims) for s in STATUSES}

    @property
    def needs_attention(self) -> int:
        return sum(c.status != "correct" for c in self.claims)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Audit":
        """Validate an untrusted JSON snapshot before it reaches the UI."""
        if not isinstance(data, dict):
            raise ValueError("Each audit must be a JSON object.")
        def read_record(kind, value):
            if not isinstance(value, dict):
                raise ValueError("Invalid audit record.")
            allowed = {f.name for f in fields(kind)}
            value = {k: v for k, v in value.items() if k in allowed}
            for k, v in value.items():
                if k not in {"number", "id", "paragraph", "source_number", "metadata_verified", "citations"}:
                    if not isinstance(v, str):
                        raise ValueError(f"Invalid text field: {k}.")
            try:
                return kind(**value)
            except TypeError as exc:
                raise ValueError("The snapshot is missing required fields.") from exc
        nested = {"sources": Source, "paragraphs": Paragraph, "claims": Claim}
        values = {k: v for k, v in data.items() if k in {f.name for f in fields(cls)}}
        for name, kind in nested.items():
            records = values.get(name, [])
            if not isinstance(records, list) or len(records) > 3000:
                raise ValueError("The snapshot has too many records.")
            values[name] = [read_record(kind, r) for r in records]
        for key in ["id", "created_at", "title", "citation_style", "mode", "paper_text", "reference_input", "corrected_paper", "reference_list"]:
            if key in values and not isinstance(values[key], str):
                raise ValueError(f"Invalid audit field: {key}.")
        if not isinstance(values.get("warnings", []), list) or any(not isinstance(x, str) for x in values.get("warnings", [])):
            raise ValueError("Invalid warnings list.")
        if not isinstance(values.get("duration_seconds", 0), (int, float)) or values.get("duration_seconds", 0) < 0:
            raise ValueError("Invalid audit duration.")
        if not isinstance(values.get("is_demo", False), bool):
            raise ValueError("Invalid demo flag.")
        audit = cls(**values)
        if audit.citation_style not in STYLE_LABELS:
            raise ValueError("Unsupported citation style in snapshot.")
        for collection in (audit.sources, audit.paragraphs):
            numbers = [r.number for r in collection]
            if any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
                raise ValueError("Invalid or duplicate record numbers.")
        for s in audit.sources:
            if s.status not in {"pending", "success", "failed", "metadata_only", "unavailable"} or type(s.metadata_verified) is not bool:
                raise ValueError("Invalid source status.")
        for p in audit.paragraphs:
            if not isinstance(p.citations, list) or any(not isinstance(c, str) for c in p.citations):
                raise ValueError("Invalid paragraph citations.")
        source_numbers = {s.number for s in audit.sources}
        paragraph_numbers = {p.number for p in audit.paragraphs}
        claim_ids = set()
        for c in audit.claims:
            if type(c.id) is not int or c.id < 1 or c.id in claim_ids:
                raise ValueError("Invalid or duplicate claim ID.")
            claim_ids.add(c.id)
            if type(c.paragraph) is not int or c.paragraph not in paragraph_numbers:
                raise ValueError("Claim refers to an unknown paragraph.")
            if c.status not in STATUSES or c.confidence not in {"low", "medium", "high"}:
                raise ValueError("Invalid claim status or confidence.")
            if c.source_number is not None and (type(c.source_number) is not int or c.source_number not in source_numbers):
                raise ValueError("Claim refers to an unknown source.")
        return audit
