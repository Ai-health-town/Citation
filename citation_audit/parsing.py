"""Reference parsing and citation detection without an AI dependency."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit, urlunsplit

from .models import Paragraph, Source

YEAR = r"(?:\d{4}[a-z]?|n\.d\.)"
YEAR_RE = re.compile(r"\((" + YEAR + r")(?:,\s*[^)]*)?\)", re.I)
URL_RE = re.compile(r'https?://[^\s<>"\[\]{}]+', re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s<>\"\[\]{}]+", re.I)
SPACE_RE = re.compile(r"\s+")


def normalized(text: str) -> str:
    return SPACE_RE.sub(" ", text).strip().casefold()


def trim_link(value: str) -> str:
    value = value.strip().rstrip(".,;:!?'\"")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def normalize_url(value: str) -> str:
    value = trim_link(value)
    parsed = urlsplit(value)
    if parsed.hostname and parsed.hostname.lower() in {"doi.org", "dx.doi.org"}:
        return "https://doi.org/" + unquote(parsed.path).lstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def split_reference_entries(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    entries, current = [], []
    for line in text.splitlines():
        raw_line = line
        line = re.sub(r"^\s*(?:\[\d+\]|\d+[.)])\s+", "", line).strip()
        if re.fullmatch(r"(?:references|bibliography|reference list|المراجع)\s*:?", line, re.I):
            continue
        starts = bool(YEAR_RE.search(line) or re.match(r"^\s*(?:\[\d+\]|\d+[.)])\s+", raw_line))
        # A standalone URL after an author/date entry is its continuation.
        if current and (not line or starts or (current[0].startswith("http") and line.startswith("http"))):
            entries.append(" ".join(current))
            current = []
        if line:
            current.append(line)
    if current:
        entries.append(" ".join(current))
    return [SPACE_RE.sub(" ", x).strip() for x in entries if x.strip()]


def parse_references(text: str, max_sources: int = 80) -> list[Source]:
    sources, seen = [], set()
    for raw in split_reference_entries(text):
        urls = [normalize_url(m.group()) for m in URL_RE.finditer(raw)]
        doi_match = DOI_RE.search(raw)
        doi = trim_link(doi_match.group()) if doi_match else ""
        url = next((u for u in urls if "doi.org/" in u), urls[-1] if urls else "")
        if doi and not url:
            url = "https://doi.org/" + doi
        year_match = YEAR_RE.search(raw)
        if year_match:
            author = raw[:year_match.start()].strip().rstrip("., ")
            year = year_match.group(1)
            tail = raw[year_match.end():].strip().lstrip(". ")
        else:
            # Harvard commonly has an unparenthesized year.
            year_match = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", raw)
            author = raw[:year_match.start()].strip().rstrip("., ") if year_match else ""
            year = year_match.group(1) if year_match else ""
            tail = raw[year_match.end():].lstrip("., ") if year_match else raw
        title = re.split(r"\.\s+(?=[A-Z\d])", URL_RE.sub("", tail), maxsplit=1)[0].strip().rstrip(".")
        if not title or title.startswith("http"):
            title = urlsplit(url).hostname or "Untitled reference"
        key = (doi.casefold() if doi else normalize_url(url)) if url else normalized(raw)
        if key in seen:
            continue
        seen.add(key)
        path = urlsplit(url).path.lower()
        source_type = "pdf" if path.endswith(".pdf") else "docx" if path.endswith(".docx") else "webpage"
        sources.append(Source(len(sources) + 1, raw, url, doi, title, author, year, source_type))
    if len(sources) > max_sources:
        raise ValueError(f"Found {len(sources)} references. The current limit is {max_sources}; raise it in settings or split the audit.")
    return sources


def detect_citations(text: str) -> list[str]:
    parenthetical = [m.group() for m in re.finditer(r"\([^()]{1,180}(?:\d{4}[a-z]?|n\.d\.)[^()]{0,40}\)", text, re.I)
                     if re.search(r"[^\W\d_]", m.group(), re.UNICODE)]
    narrative = [m.group() for m in re.finditer(
        r"(?<!\w)(?:[A-ZÀ-ÖØ-Þ][\w’'\-]+(?:\s+[A-ZÀ-ÖØ-Þ][\w’'\-]+){0,4}(?:\s+(?:et\s+al\.|&\s+[A-Z][\w’'\-]+))?)\s*\((?:\d{4}[a-z]?|n\.d\.)\)", text)]
    numeric = [m.group() for m in re.finditer(r"\[\d+(?:\s*[,–\-]\s*\d+)*\]", text)]
    return list(dict.fromkeys(parenthetical + narrative + numeric))


def paragraphs_from_text(text: str) -> list[Paragraph]:
    chunks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    return [Paragraph(i + 1, chunk.strip(), detect_citations(chunk)) for i, chunk in enumerate(chunks) if chunk.strip()]


def citation_source_score(citation: str, source: Source) -> float:
    """Bibliographic resemblance only. This is never an evidence score."""
    if re.fullmatch(r"\[\d+\]", citation.strip()):
        return 1.0 if int(citation.strip("[]")) == source.number else 0.0
    years = re.findall(YEAR, citation, re.I)
    if not years or not source.year:
        return 0.0
    if source.year.casefold() not in {y.casefold() for y in years}:
        return 0.0
    name = re.split(r"[,;&]", source.author)[0].strip()
    name = re.sub(r"\s+[A-Z]\.?(?:\s+[A-Z]\.?)?$", "", name)
    if name and normalized(name) in normalized(citation):
        return 1.0
    # Accommodate institutional abbreviations explicitly written in the citation.
    abbreviation = "".join(w[0] for w in source.author.split() if w and w[0].isalpha())
    if len(abbreviation) >= 3 and re.search(r"\b" + re.escape(abbreviation) + r"\b", citation):
        return 0.85
    return 0.0


def validate_references(sources: list[Source]) -> list[dict]:
    return [{"#": s.number, "Title": s.title, "Author": s.author or "Missing",
             "Year": s.year or "Missing", "Link / DOI": s.url or "Missing",
             "Check": "; ".join(x for x, missing in [
                 ("Author needs checking", not s.author), ("Year needs checking", not s.year),
                 ("Upload source text or add a link", not s.url)] if missing) or "Core fields found"}
            for s in sources]
