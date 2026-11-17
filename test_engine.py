import copy

import pytest

from citation_audit.ai import AIError
from citation_audit.engine import apply_verdict, corrected_draft, run_audit, structure_claims
from citation_audit.models import Claim, Paragraph, Source

SOURCE_TEXT = "The study found an association with fewer missed visits. It did not prove causation."


class FakeAI:
    def complete(self, instructions, data):
        if "Identify factual claims" in instructions:
            return {"paragraphs": [{"number": p["number"], "claims": [{"text": p["text"], "current_citation": "(Smith, 2024)"}]} for p in data["paragraphs"]]}
        if "Audit each factual claim" in instructions:
            return {"claims": [{"id": c["id"], "status": "correct", "source_number": 1, "evidence": "Association stated in source.",
                                 "evidence_quote": "The study found an association with fewer missed visits.", "confidence": "high"} for c in data["claims"]]}
        return {"references": [{"number": s["number"], "text": s["supplied_reference"]} for s in data["sources"]]}


def test_complete_offline_ai_pipeline():
    values = []
    paper = "The study found an association with fewer missed visits (Smith, 2024)."
    audit = run_audit(paper, "Smith, J. (2024). Appointment reminders.", ai=FakeAI(), retrieve=False,
                      attachments={1: ("source.txt", SOURCE_TEXT.encode())}, progress=lambda v, m: values.append(v))
    assert len(audit.claims) == 1
    assert audit.claims[0].status == "correct"
    assert audit.claims[0].evidence_quote in SOURCE_TEXT
    assert audit.corrected_paper == paper
    assert values == sorted(values)
    assert values[-1] == 1


def test_structure_checks_never_mark_evidence_verified():
    audit = run_audit("The evidence supports the claim about clinical reminders (Smith, 2024).",
                      "Smith, J. (2024). Appointment reminders.", retrieve=False)
    assert {c.status for c in audit.claims} == {"needs_review"}
    assert audit.sources[0].status == "unavailable"
    assert audit.corrected_paper == audit.paper_text


def test_failed_sources_do_not_become_unsupported_claims():
    audit = run_audit("The study found an association with fewer missed visits (Smith, 2024).",
                      "Smith, J. (2024). Appointment reminders.", ai=FakeAI(), retrieve=False)
    assert audit.claims[0].status == "needs_review"
    assert "No accessible source text" in audit.claims[0].notes


@pytest.mark.parametrize("number,quote", [(99, "Real evidence in the source text."), (1, "An invented statement about evidence."), (1, "Real")])
def test_invented_source_or_quote_cannot_produce_positive_verdict(number, quote):
    claim = Claim(1, 1, "A claim requiring evidence.", "(Smith, 2024)")
    row = {"status": "correct", "source_number": number, "evidence_quote": quote, "confidence": "high", "suggested_rewrite": "Rewrite."}
    result = apply_verdict(claim, row, [{"number": 1, "excerpt": "Real evidence in the source text."}], [Source(1, author="Smith", year="2024")])
    assert result.status == "needs_review"
    assert not result.evidence_quote
    assert not result.suggested_rewrite


def test_bibliographic_mismatch_is_not_correct():
    row = {"status": "correct", "source_number": 1, "evidence_quote": SOURCE_TEXT, "confidence": "high"}
    result = apply_verdict(Claim(1, 1, "The statement.", "(Jones, 2022)"), row,
                           [{"number": 1, "excerpt": SOURCE_TEXT}], [Source(1, author="Smith, J.", year="2024")])
    assert result.status == "needs_review"


def test_missing_citation_is_identified_when_supported():
    row = {"status": "correct", "source_number": 1, "evidence_quote": SOURCE_TEXT, "confidence": "high"}
    result = apply_verdict(Claim(1, 1, "The statement."), row, [{"number": 1, "excerpt": SOURCE_TEXT}], [Source(1)])
    assert result.status == "missing_citation"


def test_invalid_claim_extraction_falls_back_without_losing_paper():
    class BadAI(FakeAI):
        def complete(self, instructions, data):
            if "Identify factual claims" in instructions:
                return {"paragraphs": [{"number": 1, "claims": [{"text": "Invented wording not in the paper."}]}]}
            return super().complete(instructions, data)
    paper = "The study found an association with fewer missed visits (Smith, 2024)."
    audit = run_audit(paper, "Smith, J. (2024). A study.", ai=BadAI(), retrieve=False)
    assert audit.claims
    assert audit.corrected_paper == paper
    assert any("conservative structure" in w for w in audit.warnings)


def test_unavailable_ai_produces_reviewable_partial_result():
    class DownAI:
        def complete(self, *args, **kwargs):
            raise AIError("Provider unavailable.")
    paper = "The study found an association with fewer missed visits (Smith, 2024)."
    audit = run_audit(paper, "Smith, J. (2024). A study.", ai=DownAI(), retrieve=False,
                      attachments={1: ("source.txt", SOURCE_TEXT.encode())})
    assert audit.claims[0].status == "needs_review"
    assert audit.corrected_paper == paper
    assert any("Provider unavailable" in w for w in audit.warnings)


def test_rewrites_preserve_order_and_skip_overlaps_or_ambiguous_spans():
    paragraphs = [Paragraph(1, "First statement. Second statement."), Paragraph(2, "Repeat. Repeat.")]
    claims = [Claim(1, 1, "First statement.", status="overclaim", evidence_quote=SOURCE_TEXT, suggested_rewrite="Revised first."),
              Claim(2, 1, "First statement. Second statement.", status="overclaim", evidence_quote=SOURCE_TEXT, suggested_rewrite="Unsafe overlap."),
              Claim(3, 2, "Repeat.", status="overclaim", evidence_quote=SOURCE_TEXT, suggested_rewrite="Ambiguous."),
              Claim(4, 1, "Second statement.", status="needs_review", evidence_quote=SOURCE_TEXT, suggested_rewrite="No.")]
    assert corrected_draft(paragraphs, claims) == "Revised first. Second statement.\n\nRepeat. Repeat."


def test_attachment_requires_a_real_source_number():
    with pytest.raises(ValueError, match="does not exist"):
        run_audit("This is a sufficiently long paper for the validation check.", "Smith, J. (2024). A study.", retrieve=False,
                  attachments={2: ("source.txt", SOURCE_TEXT.encode())})


def test_too_short_paper_is_rejected():
    with pytest.raises(ValueError, match="between 50"):
        run_audit("too short", "Smith (2024). A study.")
