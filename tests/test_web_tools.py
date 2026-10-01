"""
Unit tests for WebSearchTool and FetchWebContentTool.
"""

from unittest.mock import MagicMock, patch
import pytest

from tools.builtins.web import (
    FetchWebContentTool,
    WebSearchTool,
    _extract_readable_text,
    _is_safe_url,
)
from tools.base import ToolRisk, SideEffect


def test_is_safe_url_ssrf_rejections():
    # Loopback & Localhost
    safe, reason = _is_safe_url("http://127.0.0.1:8000/api")
    assert not safe
    assert "localhost" in reason.lower() or "forbidden" in reason.lower()

    safe, reason = _is_safe_url("http://localhost:3000/dashboard")
    assert not safe

    # Schemes
    safe, reason = _is_safe_url("file:///etc/passwd")
    assert not safe
    assert "scheme" in reason.lower()

    safe, reason = _is_safe_url("ftp://ftp.example.com/file")
    assert not safe

    safe, reason = _is_safe_url("")
    assert not safe

    # Private IP range
    safe, reason = _is_safe_url("http://192.168.1.100/admin")
    assert not safe
    assert "private" in reason.lower() or "forbidden" in reason.lower()


def test_is_safe_url_valid():
    with patch("socket.gethostbyname", return_value="93.184.216.34"):
        safe, reason = _is_safe_url("https://example.com/page")
        assert safe
        assert reason == ""


def test_extract_readable_text_formatting():
    raw_html = """
    <html>
        <head>
            <title>Test Page</title>
            <style>body { color: red; }</style>
            <script>alert('evil');</script>
        </head>
        <body>
            <nav><a href="/">Home</a></nav>
            <h1>Main Title</h1>
            <p>This is a paragraph with <b>bold text</b> &amp; entities.</p>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
            </ul>
            <footer>Copyright 2026</footer>
        </body>
    </html>
    """
    cleaned = _extract_readable_text(raw_html, max_length=1000)
    assert "alert" not in cleaned
    assert "body { color" not in cleaned
    assert "Main Title" in cleaned
    assert "bold text" in cleaned
    assert "&amp;" not in cleaned
    assert "&" in cleaned
    assert "Item 1" in cleaned
    assert "Item 2" in cleaned


def test_extract_readable_text_truncation():
    long_text = "<p>" + ("A" * 500) + "</p>"
    cleaned = _extract_readable_text(long_text, max_length=100)
    assert len(cleaned) > 100
    assert "[... Truncated for length ...]" in cleaned


def test_web_search_tool_attributes():
    tool = WebSearchTool()
    assert tool.name == "search_web"
    assert tool.capability == "web.search"
    assert tool.risk == ToolRisk.SAFE
    assert tool.side_effect == SideEffect.READ_ONLY


def test_web_search_tool_empty_query():
    tool = WebSearchTool()
    result = tool.execute(query="   ")
    assert not result.ok
    assert "empty" in result.error.lower()


def test_web_search_tool_ddg_mock():
    mock_html = """
    <html>
    <body>
        <a class="result-link" href="https://example.com/one"><b>Result</b> Number 1</a>
        <table>
            <tr><td class="result-snippet">This is snippet 1 about Python.</td></tr>
        </table>
        <a class="result-link" href="https://example.com/two">Result Number 2</a>
        <table>
            <tr><td class="result-snippet">Snippet 2 describing algorithms.</td></tr>
        </table>
    </body>
    </html>
    """
    tool = WebSearchTool(tavily_api_key=None)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_client

        res = tool.execute(query="python algorithms", limit=2)
        assert res.ok
        assert "Result Number 1" in res.output
        assert "https://example.com/one" in res.output
        assert "snippet 1" in res.output
        assert len(res.data["results"]) == 2


def test_web_search_tool_tavily_mock():
    tool = WebSearchTool(tavily_api_key="tvly-mock-key")
    tavily_payload = {
        "results": [
            {
                "title": "Tavily Title",
                "url": "https://tavily.com/doc",
                "content": "Detailed Tavily search snippet content.",
            }
        ]
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = tavily_payload
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_client

        res = tool.execute(query="ai agents", limit=1)
        assert res.ok
        assert "Tavily Title" in res.output
        assert "https://tavily.com/doc" in res.output
        assert res.data["results"][0]["title"] == "Tavily Title"


def test_fetch_web_content_ssrf_block():
    tool = FetchWebContentTool()
    res = tool.execute(url="http://127.0.0.1:8000/secret")
    assert not res.ok
    assert "refused" in res.error.lower()


def test_fetch_web_content_success():
    tool = FetchWebContentTool()
    html_sample = "<html><body><h1>Doc Heading</h1><p>Doc Content paragraph.</p></body></html>"

    with patch("socket.gethostbyname", return_value="93.184.216.34"):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"content-type": "text/html"}
            mock_resp.text = html_sample
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__enter__.return_value = mock_client

            res = tool.execute(url="https://example.com/doc", max_length=2000)
            assert res.ok
            assert "Doc Heading" in res.output
            assert "Doc Content paragraph" in res.output
            assert res.data["status_code"] == 200
