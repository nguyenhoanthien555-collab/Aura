"""
Web search and content extraction tools for Aura.

Provides real-time internet knowledge grounding via:
1. WebSearchTool (`search_web`): DuckDuckGo Lite keyless search with optional Tavily API fallback.
2. FetchWebContentTool (`fetch_web_content`): URL fetcher with SSRF defense and clean text extraction.
"""

from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from core.logger import logger
from tools.base import Parameter, SideEffect, Tool, ToolResult, ToolRisk, fail, ok

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Aura/1.0"
)


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate URL scheme and host against SSRF (Server-Side Request Forgery)."""
    try:
        trimmed = (url or "").strip()
        if not trimmed:
            return False, "Empty URL"
        parsed = urlparse(trimmed)
        if parsed.scheme.lower() not in ("http", "https"):
            return False, f"Unsupported scheme '{parsed.scheme}'. Only http and https are allowed."
        host = parsed.hostname
        if not host:
            return False, "Missing host in URL."

        if host.lower() in ("localhost", "127.0.0.1", "::1"):
            return False, "Access to localhost/loopback is forbidden."

        # Resolve host IP to prevent private network pivoting
        try:
            ip_str = socket.gethostbyname(host)
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_reserved
                or ip.is_link_local
                or ip.is_unspecified
                or ip.is_multicast
            ):
                return False, f"Access to private/local/reserved network address ({ip_str}) is forbidden."
        except socket.gaierror:
            return False, f"Unable to resolve hostname '{host}'."

        return True, ""
    except Exception as error:
        return False, f"Invalid URL '{url}': {error}"


def _extract_readable_text(html_text: str, max_length: int = 4000) -> str:
    """Convert HTML into clean, human/LLM-readable plain text / markdown."""
    # 1. Remove non-content blocks (scripts, styles, noscript, svg, navigation, footers)
    text = re.sub(
        r"<(script|style|svg|noscript|nav|header|footer)[^>]*>.*?</\1>",
        " ",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 2. Markdown equivalents for headings and lists
    text = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", r"\n\n# \2\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n* \1", text, flags=re.DOTALL | re.IGNORECASE)
    # 3. Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # 4. Unescape HTML entities (&amp;, &lt;, etc.)
    text = html.unescape(text)
    # 5. Clean up redundant whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text).strip()

    if len(text) > max_length:
        text = text[:max_length].rstrip() + "\n\n[... Truncated for length ...]"
    return text


class WebSearchTool(Tool):
    """
    Search the web for real-time information, documentation, news, or answers.
    Uses DuckDuckGo Lite by default (zero-config, keyless), with seamless Tavily API upgrade if configured.
    """

    name = "search_web"
    description = (
        "Search the web for up-to-date information, documentation, news, or technical answers. "
        "Returns top titles, snippets, and source URLs."
    )
    capability = "web.search"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = [
        Parameter("query", "Search keywords or question to search the web for", required=True, type="string"),
        Parameter("limit", "Maximum number of search results to return (1 to 10, default 5)", required=False, type="integer"),
    ]

    def __init__(self, tavily_api_key: Optional[str] = None):
        self._tavily_key = tavily_api_key or os.environ.get("TAVILY_API_KEY", "").strip()

    def execute(self, query: str = "", limit: int = 5, **kwargs: Any) -> ToolResult:
        query_str = (query or "").strip()
        if not query_str:
            return fail("Empty search query provided.", tool=self.name)

        try:
            n_limit = max(1, min(10, int(limit)))
        except (ValueError, TypeError):
            n_limit = 5

        # 1. Tavily API path if key is provided
        if self._tavily_key:
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(
                        "https://api.tavily.com/search",
                        json={
                            "api_key": self._tavily_key,
                            "query": query_str,
                            "max_results": n_limit,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        results = data.get("results", [])
                        if results:
                            lines = [f"Found {len(results)} web search results for '{query_str}':\n"]
                            structured = []
                            for idx, item in enumerate(results[:n_limit], 1):
                                title = item.get("title", "No Title").strip()
                                url = item.get("url", "").strip()
                                content = item.get("content", "").strip()
                                lines.append(f"{idx}. {title}\n   URL: {url}\n   Snippet: {content}\n")
                                structured.append({"title": title, "url": url, "snippet": content})
                            return ok("\n".join(lines).strip(), tool=self.name, data={"results": structured})
            except Exception as tavily_err:
                logger.warning("Tavily search failed (%s), falling back to DuckDuckGo", tavily_err)

        # 2. Keyless DuckDuckGo path (prefer html.duckduckgo.com to avoid TLS handshake hangs on cloud)
        endpoints = [
            ("https://html.duckduckgo.com/html/", "html"),
            ("https://lite.duckduckgo.com/lite/", "lite"),
        ]
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        last_error = None

        for endpoint_url, engine_type in endpoints:
            try:
                with httpx.Client(timeout=8.0, headers=headers, follow_redirects=True) as client:
                    resp = client.post(endpoint_url, data={"q": query_str})
                    if resp.status_code != 200:
                        last_error = f"Search provider ({engine_type}) returned status {resp.status_code}"
                        continue

                    html_content = resp.text
                    results = []

                    # Parse html.duckduckgo.com format
                    web_blocks = re.findall(
                        r'<div[^>]+class=[\'"][^\'"]*(?:web-result|result__body)[^\'"]*[\'"][^>]*>(.*?)</div>\s*</div>',
                        html_content,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if not web_blocks:
                        # Fallback block matching
                        web_blocks = re.findall(
                            r'<div[^>]+class=[\'"][^\'"]*result__body[^\'"]*[\'"][^>]*>(.*?)(?:</div>|$)',
                            html_content,
                            re.DOTALL | re.IGNORECASE,
                        )

                    for block in web_blocks:
                        title_match = re.search(
                            r'<h2[^>]+class=[\'"][^\'"]*result__title[^\'"]*[\'"][^>]*>\s*<a[^>]+href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>',
                            block,
                            re.DOTALL | re.IGNORECASE,
                        )
                        if not title_match:
                            title_match = re.search(
                                r'<a[^>]+class=[\'"][^\'"]*result__a[^\'"]*[\'"][^>]*href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>',
                                block,
                                re.DOTALL | re.IGNORECASE,
                            )
                        if title_match:
                            raw_url, raw_title = title_match.group(1), title_match.group(2)
                            # Skip ad links
                            if "ad_domain=" in raw_url or "y.js" in raw_url:
                                continue

                            clean_title = html.unescape(re.sub(r"<[^>]+>", "", raw_title).strip())
                            snippet_match = re.search(
                                r'<a[^>]+class=[\'"][^\'"]*result__snippet[^\'"]*[\'"][^>]*>(.*?)</a>',
                                block,
                                re.DOTALL | re.IGNORECASE,
                            )
                            clean_snippet = (
                                html.unescape(re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip())
                                if snippet_match
                                else "(No preview snippet available)"
                            )

                            clean_url = raw_url.strip()
                            if "/l/?" in clean_url or "uddg=" in clean_url:
                                try:
                                    parsed_q = parse_qs(urlparse(clean_url).query)
                                    if "uddg" in parsed_q:
                                        clean_url = parsed_q["uddg"][0]
                                except Exception:
                                    pass

                            results.append({"title": clean_title, "url": clean_url, "snippet": clean_snippet})
                            if len(results) >= n_limit:
                                break

                    # If html format found results, return them
                    if results:
                        lines = [f"Found web search results for '{query_str}':\n"]
                        for idx, item in enumerate(results, 1):
                            lines.append(f"{idx}. {item['title']}\n   URL: {item['url']}\n   Snippet: {item['snippet']}\n")
                        return ok("\n".join(lines).strip(), tool=self.name, data={"results": results})

                    # If not found via html blocks, try lite.duckduckgo.com parser format
                    links = []
                    for m in re.finditer(r"<a\s+([^>]+)>(.*?)</a>", html_content, re.DOTALL | re.IGNORECASE):
                        attrs_str, link_text = m.group(1), m.group(2)
                        if "result-link" in attrs_str:
                            href_match = re.search(r"href=['\"]([^'\"]+)['\"]", attrs_str)
                            if href_match:
                                links.append((href_match.group(1), link_text))
                    snippets = re.findall(
                        r"<td[^>]+class=['\"][^'\"]*result-snippet[^'\"]*['\"][^>]*>(.*?)</td>",
                        html_content,
                        re.DOTALL | re.IGNORECASE,
                    )

                    if links:
                        lines = [f"Found web search results for '{query_str}':\n"]
                        for idx, (raw_url, raw_title) in enumerate(links[:n_limit], 1):
                            clean_title = html.unescape(re.sub(r"<[^>]+>", "", raw_title).strip())
                            s = snippets[idx - 1] if idx - 1 < len(snippets) else ""
                            clean_snippet = html.unescape(re.sub(r"<[^>]+>", "", s).strip()) or "(No preview snippet available)"

                            clean_url = raw_url.strip()
                            if "/l/?" in clean_url or "uddg=" in clean_url:
                                try:
                                    parsed_q = parse_qs(urlparse(clean_url).query)
                                    if "uddg" in parsed_q:
                                        clean_url = parsed_q["uddg"][0]
                                except Exception:
                                    pass

                            results.append({"title": clean_title, "url": clean_url, "snippet": clean_snippet})
                            lines.append(f"{idx}. {clean_title}\n   URL: {clean_url}\n   Snippet: {clean_snippet}\n")
                        return ok("\n".join(lines).strip(), tool=self.name, data={"results": results})

            except Exception as endpoint_err:
                logger.debug("Search endpoint %s failed: %s", endpoint_url, endpoint_err)
                last_error = str(endpoint_err)
                continue

        if last_error:
            logger.error("WebSearchTool execution failed across endpoints: %s", last_error)
            return fail(f"Search request failed: {last_error}", tool=self.name)

        return ok(f"No web results found for query: '{query_str}'", tool=self.name, data={"results": []})


class FetchWebContentTool(Tool):
    """
    Fetch and extract readable plain text or markdown from a web URL.
    Enforces strict SSRF protection (refuses local, private, and loopback IPs).
    """

    name = "fetch_web_content"
    description = (
        "Fetch and extract readable plain text or markdown from a web URL. "
        "Use this to read online documentation, articles, GitHub files, or pages found via search_web."
    )
    capability = "web.fetch"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = [
        Parameter("url", "The full HTTP or HTTPS URL of the webpage to fetch", required=True, type="string"),
        Parameter("max_length", "Maximum characters of text to extract (default 4000)", required=False, type="integer"),
    ]

    def execute(self, url: str = "", max_length: int = 4000, **kwargs: Any) -> ToolResult:
        trimmed_url = (url or "").strip()
        is_safe, reason = _is_safe_url(trimmed_url)
        if not is_safe:
            return fail(f"Refused to fetch URL '{trimmed_url}': {reason}", tool=self.name)

        try:
            n_max = max(500, min(20000, int(max_length)))
        except (ValueError, TypeError):
            n_max = 4000

        current_url = trimmed_url
        max_redirects = 4
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        MAX_RESPONSE_BYTES = 2_000_000  # 2MB max download limit

        try:
            with httpx.Client(timeout=12.0, headers=headers, follow_redirects=False) as client:
                for redirect_hop in range(max_redirects + 1):
                    # Validate each hop before requesting
                    hop_safe, hop_reason = _is_safe_url(current_url)
                    if not hop_safe:
                        return fail(
                            f"Refused to follow redirect to unsafe URL '{current_url}': {hop_reason}",
                            tool=self.name,
                        )

                    with client.stream("GET", current_url) as resp:
                        # Handle HTTP redirects safely
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location")
                            if not location:
                                return fail(f"Redirect from '{current_url}' had no Location header.", tool=self.name)
                            current_url = urljoin(current_url, location)
                            continue

                        if resp.status_code >= 400:
                            return fail(
                                f"HTTP request to '{current_url}' failed with status code {resp.status_code}",
                                tool=self.name,
                            )

                        # Check Content-Length upfront if available
                        cl = resp.headers.get("content-length")
                        if cl and cl.isdigit() and int(cl) > 10_000_000:
                            return fail(f"Refused to fetch content: size ({cl} bytes) exceeds 10MB limit.", tool=self.name)

                        # Bounded stream reading
                        chunks = []
                        total_bytes = 0
                        for chunk in resp.iter_bytes():
                            chunks.append(chunk)
                            total_bytes += len(chunk)
                            if total_bytes >= MAX_RESPONSE_BYTES:
                                break

                        raw_bytes = b"".join(chunks)
                        encoding = resp.encoding or "utf-8"
                        try:
                            body_text = raw_bytes.decode(encoding, errors="replace")
                        except Exception:
                            body_text = raw_bytes.decode("utf-8", errors="replace")

                        content_type = resp.headers.get("content-type", "").lower()
                        if "application/json" in content_type:
                            extracted = body_text[:n_max]
                        else:
                            extracted = _extract_readable_text(body_text, max_length=n_max)

                        summary = (
                            f"Successfully fetched content from {current_url} ({len(extracted)} characters):\n\n"
                            f"{extracted}"
                        )
                        return ok(
                            summary,
                            tool=self.name,
                            data={"url": current_url, "content_length": len(extracted), "status_code": resp.status_code},
                        )

                return fail(f"Exceeded maximum redirects ({max_redirects}) for '{trimmed_url}'.", tool=self.name)

        except Exception as error:
            logger.error("FetchWebContentTool failed on '%s': %s", trimmed_url, error)
            return fail(f"Failed to fetch web content from '{trimmed_url}': {error}", tool=self.name)


__all__ = [
    "FetchWebContentTool",
    "WebSearchTool",
]
