"""
Tests for OpenUrlTool (desktop.open_url).
"""

import pytest

from core.capabilities import registry as cap_registry
from tools.base import ToolRisk, ToolStatus
from tools.builtins.desktop import OpenUrlTool
from tools.factory import build_registry


class TestOpenUrlTool:

    def test_protocol_and_metadata(self):
        tool = OpenUrlTool()
        assert tool.name == "open_url"
        assert tool.risk == ToolRisk.SAFE
        assert tool.capability == "desktop.open_url"
        assert len(tool.parameters) == 1
        assert tool.parameters[0].name == "url"
        assert tool.parameters[0].required is True

    def test_execute_valid_https_url(self):
        opened_urls = []

        def mock_opener(url):
            opened_urls.append(url)
            return True

        tool = OpenUrlTool(opener=mock_opener)
        result = tool.execute("https://youtube.com/watch?v=12345")

        assert result.ok is True
        assert result.status == ToolStatus.SUCCESS.value
        assert result.output == "Opened 'https://youtube.com/watch?v=12345' in default browser"
        assert opened_urls == ["https://youtube.com/watch?v=12345"]

    def test_execute_valid_http_url_with_whitespace(self):
        opened_urls = []

        def mock_opener(url):
            opened_urls.append(url)
            return True

        tool = OpenUrlTool(opener=mock_opener)
        result = tool.execute("   http://example.com/test   ")

        assert result.ok is True
        assert result.status == ToolStatus.SUCCESS.value
        assert "http://example.com/test" in result.output
        assert opened_urls == ["http://example.com/test"]

    def test_rejects_empty_or_non_string(self):
        tool = OpenUrlTool()
        assert tool.execute("").ok is False
        assert tool.execute("   ").ok is False
        assert tool.execute(None).ok is False
        assert tool.execute(123).ok is False

    def test_rejects_dangerous_schemes(self):
        opened_urls = []

        def mock_opener(url):
            opened_urls.append(url)
            return True

        tool = OpenUrlTool(opener=mock_opener)

        # File scheme
        res_file = tool.execute("file:///C:/Windows/System32/cmd.exe")
        assert res_file.ok is False
        assert res_file.status == ToolStatus.FAILED.value
        assert "only http and https URLs are permitted" in res_file.error

        # Javascript scheme
        res_js = tool.execute("javascript:alert(1)")
        assert res_js.ok is False
        assert res_js.status == ToolStatus.FAILED.value
        assert "only http and https URLs are permitted" in res_js.error

        # Data scheme
        res_data = tool.execute("data:text/html,<html></html>")
        assert res_data.ok is False

        # FTP scheme
        res_ftp = tool.execute("ftp://files.example.com")
        assert res_ftp.ok is False

        assert opened_urls == []

    def test_rejects_missing_host(self):
        tool = OpenUrlTool()
        res = tool.execute("https://")
        assert res.ok is False
        assert res.status == ToolStatus.FAILED.value
        assert "missing host/domain" in res.error

    def test_opener_returning_false(self):
        tool = OpenUrlTool(opener=lambda url: False)
        result = tool.execute("https://example.com")
        assert result.ok is False
        assert result.status == ToolStatus.FAILED.value
        assert "Failed to launch browser" in result.error

    def test_opener_raising_exception(self):
        def broken_opener(url):
            raise RuntimeError("Browser not found")

        tool = OpenUrlTool(opener=broken_opener)
        result = tool.execute("https://example.com")
        assert result.ok is False
        assert result.status == ToolStatus.FAILED.value
        assert "Browser not found" in result.error

    def test_registered_in_factory_and_capability_matches(self):
        from core.capabilities.factory import register_core_capabilities
        register_core_capabilities()

        # Capability exists
        cap = cap_registry.get("desktop.open_url")
        assert cap is not None
        assert cap.discovery_metadata.get("tool") == "open_url"

        # Registry contains tool
        reg = build_registry({"allowed": ["open_url"]})
        assert "open_url" in reg.names()
        tool = reg.get("open_url")
        assert tool.name == "open_url"
        assert tool.capability == "desktop.open_url"
