import pytest

from citation_audit.models import Source
from citation_audit.parsing import (
    citation_source_score, detect_citations, normalize_url, paragraphs_from_text,
    parse_references, split_reference_entries,
)


def test_multiline_numbered_references_and_duplicates():
    text = """References
1. Smith, J. (2024). A clinical study.
   Journal of Examples. https://doi.org/10.1234/test.abc
2. World Health Organization. (2023). A report. https://example.org/report
3. Smith, J. (2024). Duplicate. https://doi.org/10.1234/test.abc
"""
    sources = parse_references(text)
    assert len(sources) == 2
    assert sources[0].author == "Smith, J"
    assert sources[0].doi == "10.1234/test.abc"
    assert sources[1].number == 2
    assert sources[1].author == "World Health Organization"


def test_doi_with_balanced_parentheses_is_preserved():
    source = parse_references("Smith, J. (2024). A study. https://doi.org/10.1234/example(12)34.")[0]
    assert source.doi == "10.1234/example(12)34"
    assert source.url == "https://doi.org/10.1234/example(12)34"


def test_nodate_and_organizations():
    source = parse_references("World Health Organization. (n.d.). An undated report. https://example.org/report")[0]
    assert source.year == "n.d."
    assert citation_source_score("(World Health Organization, n.d.)", source) == 1


def test_titles_without_urls_are_retained():
    source = parse_references("Example Author. (2023). A book without an online link. Example Press.")[0]
    assert source.url == ""
    assert source.title == "A book without an online link"


def test_url_only_list():
    assert len(parse_references("https://example.org/a\nhttps://example.org/b")) == 2


def test_reference_limit_fails_without_silent_truncation():
    with pytest.raises(ValueError, match="Found 3"):
        parse_references("\n\n".join(f"Author{i}. (2024). Title{i}." for i in range(3)), max_sources=2)


def test_citation_detection_and_mapping():
    text = "World Health Organization (2023) reports this. Others agree (Smith et al., 2024; Jones, 2021)."
    citations = detect_citations(text)
    assert "World Health Organization (2023)" in citations
    assert "(Smith et al., 2024; Jones, 2021)" in citations
    assert citation_source_score(citations[-1], Source(1, author="World Health Organization", year="2023")) == 1
    assert citation_source_score("(Smith, 2023)", Source(2, author="Smith, J.", year="2024")) == 0


def test_year_suffixes_are_not_conflated():
    source = Source(1, author="Smith, J.", year="2024a")
    assert citation_source_score("(Smith, 2024b)", source) == 0
    assert citation_source_score("(Smith, 2024a)", source) == 1


def test_numeric_citation_mapping():
    assert detect_citations("This is reported in [2].") == ["[2]"]
    assert citation_source_score("[2]", Source(2)) == 1
    assert citation_source_score("[2]", Source(1)) == 0


def test_paragraph_order_and_unicode_are_preserved():
    text = "First paragraph.\n\nSecond paragraph.\n\nالمراجع مهمة."
    ps = paragraphs_from_text(text)
    assert [p.number for p in ps] == [1, 2, 3]
    assert "\n\n".join(p.text for p in ps) == text
