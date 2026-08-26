"""Tests for assembling the server-rendered HTML pages."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import falcon

from api.api_resource import APIResource
from api.tests.support import mock_app_context
from api.utils import page_rendering
from api.utils.page_rendering import serialize_embedded_json


class TestServeStaticFile(unittest.TestCase):
    """serve_static_file reads a file under STATIC_DIR and writes it to the response."""

    def test_reads_file_content(self) -> None:
        mock_response = MagicMock()

        with patch("api.utils.page_rendering.pathlib.Path") as mock_path:
            mock_file = MagicMock()
            mock_file.open.return_value.__enter__.return_value.read.return_value = "file content"
            mock_path.return_value = mock_file

            page_rendering.serve_static_file(filename="test.html", falcon_response=mock_response)

            assert mock_response.text == "file content"

    def test_missing_file_serves_404(self) -> None:
        mock_response = MagicMock()

        page_rendering.serve_static_file(filename="does-not-exist.html", falcon_response=mock_response)

        assert mock_response.status == falcon.HTTP_404
        assert "does-not-exist.html" in mock_response.text


class TestHtmlMinification(unittest.TestCase):
    """_minify_html reduces page weight.

    Must not corrupt the per-request placeholders that _build_base_html's cached output still
    needs substituted afterward (SERVER_SIDE_RESULTS, SERVER_SIDE_EMBEDDED_DATA).
    """

    def setUp(self) -> None:
        self.app_context = mock_app_context()
        self.mock_conn_pool = self.app_context.reader_pool
        self.api_resource = APIResource(app_context=self.app_context)

    def test_minifies_whitespace_by_default(self) -> None:
        # minify_html also drops the redundant closing </p> (valid HTML5 tag-omission), hence
        # "<div><p>x</div>" rather than a literal whitespace-only collapse.
        assert page_rendering._minify_html("<div>   <p>x</p>   </div>") == "<div><p>x</div>"

    def test_disabled_flag_returns_input_unchanged(self) -> None:
        original = page_rendering._MINIFY_HTML_ENABLED
        page_rendering._MINIFY_HTML_ENABLED = False
        try:
            html = "<div>   <p>x</p>   </div>"
            assert page_rendering._minify_html(html) == html
        finally:
            page_rendering._MINIFY_HTML_ENABLED = original

    def test_server_side_placeholders_survive_minification(self) -> None:
        mock_response = MagicMock()
        self.api_resource._root(falcon_response=mock_response)
        assert "<!-- SERVER_SIDE_RESULTS -->" in mock_response.text
        assert "<!-- SERVER_SIDE_EMBEDDED_DATA -->" in mock_response.text

    def test_search_results_still_embed_after_minification(self) -> None:
        mock_response = MagicMock()
        mock_search_results = {
            "cards": [{"name": "Elvish Mystic", "set_code": "m14", "collector_number": "1"}],
            "total_cards": 1,
            "query": "elf",
        }
        with patch.object(self.api_resource, "_search", return_value=mock_search_results):
            self.api_resource._root(falcon_response=mock_response, q="elf")
        assert "window.EMBEDDED_SEARCH_RESULTS = {" in mock_response.text
        assert "Elvish Mystic" in mock_response.text

    def test_hostile_query_in_search_results_escapes_script_breakout(self) -> None:
        mock_response = MagicMock()
        hostile_query = "</script><script>alert('xss')</script>"
        mock_search_results = {
            "cards": [{"name": "<img src=x onerror=alert(1)>", "set_code": "m14", "collector_number": "1"}],
            "total_cards": 1,
            "query": hostile_query,
        }
        with patch.object(self.api_resource, "_search", return_value=mock_search_results):
            self.api_resource._root(falcon_response=mock_response, q=hostile_query)

        # The embedded script assignment must not contain literal closing script tags or < characters
        assert "window.EMBEDDED_SEARCH_RESULTS = " in mock_response.text
        # Extract the script content containing the embedded data
        script_marker = "window.EMBEDDED_SEARCH_RESULTS = "
        script_start = mock_response.text.index(script_marker) + len(script_marker)
        script_end = mock_response.text.index(";", script_start)
        embedded_json = mock_response.text[script_start:script_end]

        assert "<" not in embedded_json
        assert "</script>" not in embedded_json

        # Hydration parity: parsing the escaped JSON yields the exact original payload
        hydrated = json.loads(embedded_json)
        assert hydrated == mock_search_results


class TestSerializeEmbeddedJson(unittest.TestCase):
    """serialize_embedded_json produces HTML-script-safe JSON by escaping literal `<`."""

    def test_escapes_literal_less_than(self) -> None:
        payload = {"tag": "<script>", "arrow": "a < b", "nested": [{"key": "<value>"}]}
        serialized = serialize_embedded_json(payload)

        assert "<" not in serialized
        assert r"\u003c" in serialized
        assert json.loads(serialized) == payload

    def test_hostile_script_closing_payload(self) -> None:
        payload = {
            "query": "</script><script>alert('breakout')</script>",
            "comment": "<!-- comment -->",
        }
        serialized = serialize_embedded_json(payload)

        assert "<" not in serialized
        assert "</script>" not in serialized
        assert "<!--" not in serialized
        assert json.loads(serialized) == payload

    def test_preserves_complex_datatypes_and_unicode(self) -> None:
        payload = {
            "string": "Lim-Dûl's Vault",
            "escapes": 'quote: ", backslash: \\, newline: \n, tab: \t',
            "numbers": [0, 42, -3.14, 1e-5],
            "booleans": [True, False],
            "null": None,
            "html_entities": "&amp; &lt; &gt; &quot;",
            "script_tags": '</script><script src="evil.js"></script>',
        }
        serialized = serialize_embedded_json(payload)

        assert "<" not in serialized
        assert json.loads(serialized) == payload

