"""Bounded public-URL retrieval, document extraction, and Crossref metadata."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .documents import MAX_BYTES, extract_document
from .models import Source, utc_now
from .parsing import normalize_url, normalized, parse_references

USER_AGENT = "CitationAuditPro/1.0 (academic reference verification)"
MAX_SOURCE_CHARS = 120_000


class RetrievalError(ValueError):
    pass


def validate_public_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise RetrievalError("Use a complete public http:// or https:// URL.")
        if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise RetrievalError("URLs with credentials or nonstandard ports are not supported.")
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise RetrievalError("Only public internet addresses can be retrieved.")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except (socket.gaierror, ValueError, OSError) as exc:
        if isinstance(exc, RetrievalError):
            raise
        raise RetrievalError("The URL is invalid or its host could not be resolved.") from exc


def public_get(url: str) -> tuple[bytes, str, str]:
    """Revalidate every redirect; bound both response bytes and network waits."""
    current = url
    with httpx.Client(timeout=httpx.Timeout(18, connect=8), follow_redirects=False, trust_env=False,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/json,*/*"}) as client:
        for _ in range(6):
            current = validate_public_url(current)
            try:
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise RetrievalError("The server sent an empty redirect.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        suffix = " Upload a copy of the source if you have access." if response.status_code in {401, 403} else ""
                        raise RetrievalError(f"Source returned HTTP {response.status_code}.{suffix}")
                    parts, size = [], 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if size > MAX_BYTES:
                            raise RetrievalError("Source exceeds the 15 MB download limit.")
                        parts.append(part)
                    return b"".join(parts), response.headers.get("content-type", ""), current
            except httpx.TimeoutException as exc:
                raise RetrievalError("Source timed out. Try uploading the source document.") from exc
            except httpx.HTTPError as exc:
                raise RetrievalError("Source could not be reached over HTTPS.") from exc
    raise RetrievalError("Source redirected too many times.")


def readable_html(data: bytes) -> tuple[str, dict[str, str]]:
    soup = BeautifulSoup(data, "html.parser")
    def meta(*names):
        for name in names:
            node = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
            if node and node.get("content"):
                return str(node["content"]).strip()
        return ""
    metadata = {
        "title": meta("citation_title", "og:title", "dc.title") or (soup.title.get_text(" ", strip=True) if soup.title else ""),
        "author": meta("citation_author", "author", "dc.creator"),
        "year": meta("citation_publication_date", "article:published_time", "date", "dc.date"),
    }
    for node in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        node.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, metadata


def references_from_url(url: str, max_sources: int) -> list[Source]:
    data, kind, final_url = public_get(url)
    path = urlsplit(final_url).path.lower()
    if "pdf" in kind or data.startswith(b"%PDF-") or path.endswith(".docx"):
        suffix = ".docx" if path.endswith(".docx") else ".pdf"
        return parse_references(extract_document(data, "references" + suffix), max_sources)
    if "text/plain" in kind:
        return parse_references(data.decode("utf-8-sig", errors="replace"), max_sources)
    soup = BeautifulSoup(data, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        node.decompose()
    root = soup.find(id=re.compile(r"^(references|bibliography|ref-list|refs)$", re.I))
    root = root or soup.find("main") or soup.find("article") or soup.body or soup
    for anchor in root.find_all("a", href=True):
        href = urljoin(final_url, anchor["href"])
        if urlsplit(href).scheme in {"https", "http"}:
            anchor.replace_with(anchor.get_text(" ", strip=True) + " " + href + " ")
    blocks = root.find_all(["li", "p"])
    entries = []
    for block in blocks:
        text = block.get_text(" ", strip=True)
        if re.search(r"https?://", text) and (re.search(r"\b(?:19|20)\d{2}\b", text) or "doi.org/" in text):
            entries.append(text)
    if not entries:
        # URL-only reference lists, but never invent a URL from an article title.
        entries = re.findall(r"https?://[^\s<>]+", root.get_text(" ", strip=True))
    if not entries:
        raise RetrievalError("No reference links found. Paste the reference list or upload it as a document.")
    return parse_references("\n\n".join(entries), max_sources)


def _crossref_record(doi: str) -> dict:
    data, _, _ = public_get("https://api.crossref.org/works/" + quote(doi, safe=""))
    payload = json.loads(data)
    record = payload.get("message", {})
    if not isinstance(record, dict):
        raise RetrievalError("Crossref did not return a metadata record.")
    return record


def _apply_crossref(source: Source, record: dict) -> str:
    titles = record.get("title", [])
    title = str(titles[0]) if titles else ""
    authors = record.get("author", [])
    author = "; ".join(
        str(a.get("family", "")) + (", " + " ".join(x[0] + "." for x in str(a.get("given", "")).split() if x) if a.get("given") else "")
        for a in authors if a.get("family"))
    if not author:
        author = "; ".join(str(a.get("name", "")) for a in authors if a.get("name"))
    dates = record.get("published", record.get("issued", {})).get("date-parts", [[]])
    year = str(dates[0][0]) if dates and dates[0] else ""
    if title and source.title and SequenceMatcher(None, normalized(title), normalized(source.title)).ratio() < 0.45:
        source.notes += " Supplied title differs from the DOI registry title; check the reference."
    if title:
        source.title = title
    if author:
        source.author = author
    if year:
        # Preserve year suffixes (e.g. 2024a) assigned by the supplied bibliography.
        source.year = source.year if source.year.startswith(year) else year
    source.metadata_verified = True
    abstract = record.get("abstract", "")
    return BeautifulSoup(abstract, "html.parser").get_text(" ", strip=True) if abstract else ""


def find_doi(source: Source) -> str:
    """Resolve title-only scholarly references with a strict metadata match."""
    if len(source.title.split()) < 4:
        return ""
    data, _, _ = public_get("https://api.crossref.org/works?rows=3&query.bibliographic=" + quote(source.raw_reference or source.title))
    records = json.loads(data).get("message", {}).get("items", [])
    for record in records:
        title = (record.get("title") or [""])[0]
        similarity = SequenceMatcher(None, normalized(source.title), normalized(title)).ratio()
        dates = record.get("published", record.get("issued", {})).get("date-parts", [[]])
        year = str(dates[0][0]) if dates and dates[0] else ""
        author_matches = not source.author or any(normalized(str(a.get("family", ""))) in normalized(source.author)
            for a in record.get("author", []) if a.get("family"))
        if similarity >= 0.88 and author_matches and (not source.year or source.year[:4] == year):
            return str(record.get("DOI", ""))
    return ""


def retrieve_source(source: Source, search_missing: bool = False) -> Source:
    source = replace(source, access_date=utc_now()[:10])
    abstract = ""
    if not source.doi and not source.url and search_missing:
        try:
            source.doi = find_doi(source)
            if source.doi:
                source.url = "https://doi.org/" + source.doi
                source.notes += " DOI resolved by a close Crossref author/title/year match."
        except (RetrievalError, ValueError, TypeError, KeyError):
            source.notes += " Scholarly reference lookup was unavailable."
    if source.doi:
        try:
            abstract = _apply_crossref(source, _crossref_record(source.doi))
        except (RetrievalError, ValueError, TypeError, KeyError):
            source.notes += " DOI metadata could not be retrieved."
    if not source.url:
        source.status = "unavailable"
        source.error = "No link or DOI. Attach the source text or enable scholarly reference lookup."
        return source
    try:
        data, kind, final_url = public_get(source.url)
        path = urlsplit(final_url).path.lower()
        if "pdf" in kind or data.startswith(b"%PDF-"):
            text = extract_document(data, "source.pdf")
            source.source_type = "pdf"
        elif path.endswith(".docx") or "wordprocessingml" in kind:
            text = extract_document(data, "source.docx")
            source.source_type = "docx"
        elif "text/plain" in kind:
            text = data.decode("utf-8-sig", errors="replace")
        else:
            text, metadata = readable_html(data)
            if not source.metadata_verified:
                source.title = metadata["title"] or source.title
                source.author = source.author or metadata["author"]
                source.year = source.year or metadata["year"][:4]
        blocked = re.search(r"(verify you are human|checking your browser|just a moment|enable javascript and cookies|access denied|captcha)", text[:1000], re.I)
        if len(text.strip()) < 100 or blocked:
            raise RetrievalError("The page has no accessible source text or requires browser sign-in.")
        if len(text) > MAX_SOURCE_CHARS:
            source.notes += f" Source text limited to its first {MAX_SOURCE_CHARS:,} characters."
        source.text = text[:MAX_SOURCE_CHARS]
        source.status = "success"
    except (RetrievalError, ValueError) as exc:
        source.error = str(exc)
        source.status = "metadata_only" if source.metadata_verified else "failed"
        if len(abstract) >= 100:
            source.text = abstract[:MAX_SOURCE_CHARS]
            source.status = "success"
            source.notes += " Only the registered abstract was accessible; the full text was not read."
    return source
