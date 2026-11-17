import json
import socket

import httpx
import pytest

from citation_audit.ai import AIClient, AIConfig, AIError, parse_json_response
from citation_audit.models import Source
from citation_audit import web


def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/a", "https://user:secret@example.org", "https://example.org:8000/a"])
def test_nonpublic_url_forms_are_rejected(url, monkeypatch):
    public_dns(monkeypatch)
    with pytest.raises(web.RetrievalError):
        web.validate_public_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
def test_private_and_metadata_addresses_are_rejected(address, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(web.RetrievalError):
        web.validate_public_url("https://example.org")


def test_public_url_is_accepted(monkeypatch):
    public_dns(monkeypatch)
    assert web.validate_public_url("https://example.org/page#section") == "https://example.org/page"


def test_source_access_failure_is_explicit(monkeypatch):
    def fail(url):
        raise web.RetrievalError("Source returned HTTP 403.")
    monkeypatch.setattr(web, "public_get", fail)
    result = web.retrieve_source(Source(1, url="https://example.org/blocked"))
    assert result.status == "failed"
    assert not result.text
    assert "403" in result.error


def test_html_metadata_and_readable_article(monkeypatch):
    html = b'<html><head><title>Study title</title><meta name="author" content="Example Author"></head><body><nav>Do not read me</nav><article><p>' + b'The relevant study text and its context. ' * 10 + b'</p></article></body></html>'
    monkeypatch.setattr(web, "public_get", lambda url: (html, "text/html", url))
    result = web.retrieve_source(Source(1, url="https://example.org/study"))
    assert result.status == "success"
    assert result.title == "Study title"
    assert "Do not read me" not in result.text


def test_reference_page_extracts_actual_links(monkeypatch):
    html = b'<html><body><nav><a href="/login">Log in</a></nav><main id="references"><p>Smith, J. (2024). Example. <a href="https://doi.org/10.1234/example">DOI</a></p></main></body></html>'
    monkeypatch.setattr(web, "public_get", lambda url: (html, "text/html", url))
    sources = web.references_from_url("https://example.org/references", 10)
    assert len(sources) == 1
    assert sources[0].doi == "10.1234/example"


def test_ai_compatible_payload_and_json(monkeypatch):
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}]})
    real_client = httpx.Client
    monkeypatch.setattr("citation_audit.ai.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    ai = AIClient(AIConfig(api_key="test-key"))
    assert ai.complete("Return an object.", {"paper": "Example"}) == {"ok": True}
    payload = json.loads(captured[0].content)
    assert payload["thinking"] == {"type": "disabled"}
    assert captured[0].url.path == "/api/paas/v4/chat/completions"
    assert "test-key" not in repr(ai.config)


def test_ai_key_errors_do_not_echo_credentials_or_response_body(monkeypatch):
    def handler(request):
        return httpx.Response(401, text="do not echo test-secret-key")
    real_client = httpx.Client
    monkeypatch.setattr("citation_audit.ai.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AIError) as error:
        AIClient(AIConfig(api_key="test-secret-key")).complete("Return JSON.", {})
    assert "rejected" in str(error.value)
    assert "test-secret-key" not in str(error.value)


def test_rate_limit_is_retried(monkeypatch):
    calls = []
    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})
    real_client = httpx.Client
    monkeypatch.setattr("citation_audit.ai.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    assert AIClient(AIConfig(api_key="test")).complete("Return JSON.", {})["ok"]
    assert len(calls) == 2


def test_markdown_fences_are_accepted_but_partial_json_is_rejected():
    assert parse_json_response('```json\n{"ok":true}\n```') == {"ok": True}
    with pytest.raises(AIError):
        parse_json_response('{"ok":')
