"""Citation checks grounded in retrieved source excerpts.

String similarity selects material to inspect; it never proves support.
Every positive AI verdict needs a quotation found in the provided excerpt.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Callable

from .ai import AIClient, AIError
from .documents import extract_document
from .models import Audit, Claim, Paragraph, Source, STATUSES, STYLE_LABELS
from .parsing import citation_source_score, detect_citations, normalized, paragraphs_from_text, parse_references
from .web import MAX_SOURCE_CHARS, references_from_url, retrieve_source

Progress = Callable[[float, str], None]
MAX_PAPER_CHARS = 120_000
MAX_PARAGRAPH_CHARS = 12_000
MAX_CLAIMS = 600
STOPWORDS = set("a an the of to in on and or is are was were for by from that this with as at be been it its can may has have had study studies research et al".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{3,}", text.casefold(), re.UNICODE) if w not in STOPWORDS}


def structure_claims(paragraphs: list[Paragraph], sources: list[Source], start_id: int = 1) -> list[Claim]:
    claims = []
    for paragraph in paragraphs:
        sentences = re.split(r"(?<=[.!?؟])\s+(?=[A-ZÀ-ÖØ-Þ\u0600-\u06ff])", paragraph.text)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 25 and not detect_citations(sentence):
                continue
            citations = detect_citations(sentence)
            best = max(sources, key=lambda s: max((citation_source_score(c, s) for c in citations), default=0), default=None)
            match = best is not None and any(citation_source_score(c, best) >= .8 for c in citations)
            notes = "Structure check only: whether this statement requires a citation and whether its evidence supports it have not been assessed."
            if citations:
                notes += " Author/year resembles a supplied reference." if match else " No clear author/year match in the supplied references; review manually."
            else:
                notes += " No supported author/date or numbered citation pattern was detected."
            claims.append(Claim(
                id=start_id + len(claims), paragraph=paragraph.number, text=sentence,
                current_citation="; ".join(citations), source_number=best.number if match else None,
                source_title=best.title if match else "", notes=notes))
    return claims


EXTRACT_PROMPT = """Identify factual claims requiring citations in the numbered paper paragraphs.
Return {"paragraphs": [{"number": 1, "claims": [{"text": "EXACT contiguous quotation from this paragraph", "current_citation": "exact citation text applying to this claim, or empty"}]}]}.
Return every supplied paragraph number exactly once. Use an empty claims list for a heading,
transition, personal opinion, or general knowledge that needs no citation.
Keep distinct claims separate, including quantities, definitions, and causal assertions.
Copy text verbatim, including existing citation text where part of the sentence.
Do not invent or paraphrase claims. Include citations from the surrounding paragraph only
when they clearly apply to this particular claim. Do not attach the final citation to every
sentence by default. Handle the requested citation style and language."""


def extract_claims(paragraphs: list[Paragraph], sources: list[Source], ai: AIClient,
                   style: str, progress: Progress, warnings: list[str]) -> list[Claim]:
    claims, consecutive_failures = [], 0
    batches, batch, size = [], [], 0
    for p in paragraphs:
        if batch and (len(batch) >= 4 or size + len(p.text) > 8500):
            batches.append(batch)
            batch, size = [], 0
        batch.append(p)
        size += len(p.text)
    if batch:
        batches.append(batch)
    for index, batch in enumerate(batches):
        response, error = {}, ""
        if consecutive_failures < 2:
            try:
                response = ai.complete(EXTRACT_PROMPT, {"citation_style": STYLE_LABELS[style], "paragraphs": [{"number": p.number, "text": p.text} for p in batch]})
                if not isinstance(response.get("paragraphs"), list):
                    raise AIError("The AI omitted the paragraph records.")
                consecutive_failures = 0
            except AIError as exc:
                consecutive_failures += 1
                error = str(exc)
                warnings.append("Claim extraction: " + error)
        else:
            error = "AI extraction paused after repeated failures."
        rows = response.get("paragraphs", [])
        for p in batch:
            matches = [x for x in rows if isinstance(x, dict) and type(x.get("number")) is int and x.get("number") == p.number]
            record = matches[0] if len(matches) == 1 else None
            extracted = record.get("claims") if record else None
            valid, invalid = [], False
            if isinstance(extracted, list) and len(extracted) <= 50:
                for item in extracted:
                    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                        invalid = True
                        break
                    text = item["text"].strip()
                    if len(text) < 10 or text not in p.text:
                        invalid = True
                        break
                    citation = item.get("current_citation", "")
                    if not isinstance(citation, str) or citation not in p.text:
                        citation = "; ".join(detect_citations(text))
                    valid.append(Claim(id=len(claims) + len(valid) + 1, paragraph=p.number, text=text, current_citation=citation))
            else:
                invalid = True
            if invalid:
                fallback = structure_claims([p], sources, len(claims) + 1)
                for c in fallback:
                    c.notes += " AI claim extraction was unavailable or did not quote the paragraph exactly."
                claims.extend(fallback)
                if not error:
                    warnings.append(f"Paragraph {p.number}: used a conservative structure check because the AI claim extraction was invalid.")
            else:
                claims.extend(valid)
        if len(claims) > MAX_CLAIMS:
            raise ValueError(f"The paper contains more than {MAX_CLAIMS} candidate claims. Split it into smaller audits.")
        progress(.35 + .15 * (index + 1) / max(1, len(batches)), f"Identifying claims: batch {index + 1}/{len(batches)}")
    return claims


def select_evidence(claim: Claim, sources: list[Source], limit: int = 4) -> list[dict]:
    claim_words = words(claim.text)
    scored = []
    for source in sources:
        if source.status != "success" or not source.text:
            continue
        # Sliding windows preserve context before and after the matching sentence.
        windows = [source.text[i:i + 2000] for i in range(0, len(source.text), 1700)]
        ranked = sorted(enumerate(windows), key=lambda x: len(claim_words & words(x[1])) / max(1, len(claim_words)), reverse=True)
        chosen = sorted(ranked[:2], key=lambda x: x[0])
        snippet = "\n[... excerpt break ...]\n".join(x[1] for x in chosen)
        overlap = max((len(claim_words & words(x[1])) / max(1, len(claim_words)) for x in chosen), default=0)
        cited = any(citation_source_score(c, source) >= .8 for c in detect_citations(claim.current_citation))
        scored.append((int(cited) + overlap, {
            "number": source.number, "title": source.title, "author": source.author,
            "year": source.year, "url": source.url, "notes": source.notes,
            "excerpt": snippet,
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


VERIFY_PROMPT = """Audit each factual claim against ONLY its supplied source excerpts.
Return {"claims": [{"id": 1, "status": "needs_review", "source_number": null,
"evidence": "brief explanation", "evidence_quote": "EXACT contiguous quote from one supplied excerpt, or empty",
"suggested_citation": "citation in requested style, or empty", "suggested_rewrite": "replacement for the exact claim, or empty",
"confidence": "low|medium|high", "notes": "limitations"}]}.
Allowed status values: correct, missing_citation, wrong_citation, weak_citation,
partially_supported, unsupported_claim, overclaim, needs_review.
Use correct only if the existing citation identifies the supporting source AND the excerpt
supports the entire assertion. A similar topic, matched author/year, or word overlap is NOT support.
Use missing_citation only when evidence supports the claim but no citation is supplied.
Use wrong_citation only when the existing citation is wrong and another supplied source clearly supports it.
Use partially_supported or overclaim for partial evidence; never silently broaden results.
Excerpts are selected portions, not necessarily the complete source. Lack of evidence in these
excerpts alone normally means needs_review, not proof that a claim is false or unsupported.
Never invent a source, quote, statistic, author, year, page, DOI, URL, or source number.
For a positive/partial support decision, quote the actual evidence verbatim. Preserve the language
and meaning in a suggested rewrite. If evidence is insufficient, leave rewrite/citation empty.
Do not suggest replacing the entire paragraph when only one claim is being audited.
The user must review all proposed changes. Return one record per supplied claim ID."""


def _text_field(row: dict, name: str, limit: int = 8000) -> str:
    value = row.get(name, "")
    return value.strip()[:limit] if isinstance(value, str) else ""


def apply_verdict(claim: Claim, row: dict, candidates: list[dict], sources: list[Source]) -> Claim:
    c = replace(claim)
    c.status = row.get("status") if row.get("status") in STATUSES else "needs_review"
    confidence = row.get("confidence")
    c.confidence = confidence if isinstance(confidence, str) and confidence in {"low", "medium", "high"} else "low"
    c.evidence = _text_field(row, "evidence")
    c.evidence_quote = _text_field(row, "evidence_quote")
    c.suggested_citation = _text_field(row, "suggested_citation", 1000)
    c.suggested_rewrite = _text_field(row, "suggested_rewrite")
    c.notes = _text_field(row, "notes")
    number = row.get("source_number")
    candidate = next((s for s in candidates if type(number) is int and s["number"] == number), None)
    source = next((s for s in sources if candidate and s.number == number), None)
    if candidate and source:
        c.source_number, c.source_title = source.number, source.title
    positive = {"correct", "missing_citation", "wrong_citation", "weak_citation", "partially_supported", "overclaim"}
    quote_verified = bool(candidate and len(c.evidence_quote) >= 12
                          and normalized(c.evidence_quote) in normalized(candidate["excerpt"]))
    if c.status in positive and not quote_verified:
        c.status, c.confidence = "needs_review", "low"
        c.evidence_quote = ""
        c.suggested_rewrite = c.suggested_citation = ""
        c.notes += " Positive evidence decision could not be grounded in an exact supplied quote; manual review required."
    if c.status == "correct" and not c.current_citation.strip():
        c.status = "missing_citation"
    # Where both citation and bibliography carry author/date data, a clear mismatch
    # must never be accepted solely because the AI used the word 'correct'.
    if c.status == "correct" and source and source.author and source.year and detect_citations(c.current_citation):
        if not any(citation_source_score(x, source) >= .8 for x in detect_citations(c.current_citation)):
            c.status, c.confidence = "needs_review", "low"
            c.notes += " Citation-to-source author/year mapping needs manual review."
            c.suggested_rewrite = ""
    if c.status in {"needs_review", "unsupported_claim"}:
        c.suggested_rewrite = c.suggested_citation = ""
    if not quote_verified:
        c.evidence_quote = ""
    return c


def verify_claims(claims: list[Claim], sources: list[Source], paragraphs: list[Paragraph],
                  ai: AIClient, style: str, progress: Progress, warnings: list[str]) -> list[Claim]:
    results, failures = [], 0
    paragraph_map = {p.number: p.text for p in paragraphs}
    for offset in range(0, len(claims), 3):
        batch = claims[offset:offset + 3]
        evidence = {c.id: select_evidence(c, sources) for c in batch}
        pending = [c for c in batch if evidence[c.id]]
        rows, error = [], ""
        if pending and failures < 2:
            try:
                answer = ai.complete(VERIFY_PROMPT, {"citation_style": STYLE_LABELS[style], "claims": [
                    {"id": c.id, "text": c.text, "current_citation": c.current_citation,
                     "paragraph_context": paragraph_map[c.paragraph], "sources": evidence[c.id]} for c in pending]})
                if not isinstance(answer.get("claims"), list):
                    raise AIError("The AI omitted its claim verdicts.")
                rows, failures = answer["claims"], 0
            except AIError as exc:
                failures += 1
                error = str(exc)
                warnings.append("Evidence review: " + error)
        elif pending:
            error = "AI evidence review paused after repeated failures."
        for c in batch:
            matches = [r for r in rows if isinstance(r, dict) and type(r.get("id")) is int and r["id"] == c.id]
            if len(matches) == 1:
                results.append(apply_verdict(c, matches[0], evidence[c.id], sources))
            else:
                reason = error or ("No accessible source text was available for this claim." if not evidence[c.id] else "The AI returned no valid verdict for this claim.")
                results.append(replace(c, status="needs_review", confidence="low", notes=(c.notes + " " + reason).strip()))
        progress(.5 + .4 * min(offset + len(batch), len(claims)) / max(1, len(claims)), f"Reviewing evidence: {min(offset + len(batch), len(claims))}/{len(claims)} claims")
    return results


def corrected_draft(paragraphs: list[Paragraph], claims: list[Claim]) -> str:
    """Apply only quoted, nonoverlapping, exact replacements; preserve all paragraphs."""
    output = []
    for paragraph in paragraphs:
        edits = []
        for claim in claims:
            if claim.paragraph != paragraph.number or not claim.suggested_rewrite or not claim.evidence_quote:
                continue
            if claim.status in {"needs_review", "unsupported_claim", "correct"}:
                continue
            # Ambiguous repeated spans need a human's choice.
            if paragraph.text.count(claim.text) != 1:
                continue
            start = paragraph.text.find(claim.text)
            end = start + len(claim.text)
            if not any(start < b and end > a for a, b, _ in edits):
                edits.append((start, end, claim.suggested_rewrite))
        text = paragraph.text
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        output.append(text)
    return "\n\n".join(output)


def build_reference_list(sources: list[Source], style: str, ai: AIClient | None, warnings: list[str]) -> str:
    """Keep user references intact when a trustworthy formatting result is unavailable."""
    if ai:
        try:
            result = ai.complete(
                'Format each source as one bibliography entry in the requested style. Return {"references":[{"number":1,"text":"formatted entry"}]}. '
                'Return every supplied number exactly once. Use ONLY supplied bibliographic facts. Never invent initials, dates, journals, '
                'publishers, volume, issue, page ranges, URLs, or DOI. Preserve unknown fields as unknown. Do not add a title that was not supplied.',
                {"style": STYLE_LABELS[style], "sources": [{"number": s.number, "supplied_reference": s.raw_reference,
                    "title": s.title, "author": s.author, "year": s.year, "url": s.url, "doi": s.doi} for s in sources]})
            rows = result.get("references")
            if not isinstance(rows, list) or len(rows) != len(sources):
                raise AIError("The AI bibliography omitted references.")
            valid = [r for r in rows if isinstance(r, dict) and type(r.get("number")) is int and isinstance(r.get("text"), str) and r["text"].strip()]
            if len(valid) != len(sources) or {r["number"] for r in valid} != {s.number for s in sources}:
                raise AIError("The AI bibliography returned invalid source numbers.")
            warnings.append("Bibliography formatting is an AI proposal; verify all bibliographic details against the supplied references and source records.")
            return "\n\n".join(sorted((r["text"].strip() for r in valid), key=str.casefold))
        except AIError as exc:
            warnings.append("Bibliography formatting: " + str(exc))
    warnings.append("References retained as supplied where available; automatic style formatting was not completed.")
    entries = []
    for s in sources:
        if s.raw_reference and not s.raw_reference.startswith(("http://", "https://")):
            entries.append(s.raw_reference)
        else:
            entries.append(f"{s.author or '[Author unknown]'}. ({s.year or 'n.d.'}). {s.title or '[Title unknown]'}. {s.url}".strip())
    return "\n\n".join(sorted(entries, key=str.casefold))


def run_audit(paper_text: str, reference_text: str = "", reference_url: str = "",
              citation_style: str = "APA7", ai: AIClient | None = None,
              retrieve: bool = True, search_missing: bool = False, max_sources: int = 30,
              attachments: dict[int, tuple[str, bytes]] | None = None,
              progress: Progress | None = None, title: str = "") -> Audit:
    progress = progress or (lambda value, message: None)
    started = time.monotonic()
    if not 50 <= len(paper_text.strip()) <= MAX_PAPER_CHARS:
        raise ValueError(f"Paper text must contain between 50 and {MAX_PAPER_CHARS:,} characters.")
    if citation_style not in STYLE_LABELS:
        raise ValueError("Choose a supported citation style.")
    paragraphs = paragraphs_from_text(paper_text)
    if any(len(p.text) > MAX_PARAGRAPH_CHARS for p in paragraphs):
        raise ValueError("One paragraph is longer than 12,000 characters. Add blank lines between paragraphs and try again.")
    progress(.02, "Reading references")
    sources = references_from_url(reference_url, max_sources) if reference_url.strip() else parse_references(reference_text, max_sources)
    if not sources:
        raise ValueError("Please provide a reference list with at least one reference.")
    audit = Audit(title=title.strip()[:120] or paper_text.strip().splitlines()[0][:80], citation_style=citation_style,
                  mode="AI evidence review" if ai else "Structure only", paper_text=paper_text,
                  reference_input=reference_url or reference_text, paragraphs=paragraphs, sources=sources)
    attachments = attachments or {}
    for number in attachments:
        if number not in {s.number for s in sources}:
            raise ValueError(f"Attached source #{number} does not exist in this reference list.")
    for source in sources:
        if source.number in attachments:
            filename, data = attachments[source.number]
            text = extract_document(data, filename)
            source.text, source.status = text[:MAX_SOURCE_CHARS], "success"
            source.source_type = "uploaded"
            source.notes = "User-supplied source text: " + filename
            if len(text) > MAX_SOURCE_CHARS:
                source.notes += f"; limited to the first {MAX_SOURCE_CHARS:,} characters."
    pending = [s for s in sources if s.status != "success"]
    if retrieve and pending:
        completed = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {pool.submit(retrieve_source, s, search_missing): s for s in pending}
            for future in as_completed(jobs):
                original = jobs[future]
                try:
                    result = future.result()
                except Exception:
                    result = replace(original, status="failed", error="Source retrieval failed. Upload its text to review it.")
                sources[original.number - 1] = result
                completed += 1
                progress(.05 + .28 * completed / len(pending), f"Reading sources: {completed}/{len(pending)}")
    else:
        for s in pending:
            s.status, s.error = "unavailable", "Online retrieval is off. Attach source text to assess evidence."
    progress(.35, "Identifying statements and citations")
    if ai:
        audit.claims = extract_claims(paragraphs, sources, ai, citation_style, progress, audit.warnings)
        if audit.claims:
            audit.claims = verify_claims(audit.claims, sources, paragraphs, ai, citation_style, progress, audit.warnings)
    else:
        audit.claims = structure_claims(paragraphs, sources)
        audit.warnings.append("Structure-only mode does not verify evidence or decide which statements need citations. Use an AI API key for evidence review.")
    if len(audit.claims) > MAX_CLAIMS:
        raise ValueError(f"More than {MAX_CLAIMS} candidate statements were found. Split the paper into smaller audits.")
    if not audit.claims:
        audit.warnings.append("No citation-requiring claims were identified. This does not establish that the paper is fully supported.")
    audit.corrected_paper = corrected_draft(paragraphs, audit.claims)
    progress(.93, "Preparing bibliography and report downloads")
    audit.reference_list = build_reference_list(sources, citation_style, ai, audit.warnings)
    audit.warnings = list(dict.fromkeys(audit.warnings))
    audit.duration_seconds = round(time.monotonic() - started, 1)
    progress(1, "Audit complete")
    return audit
