"""Tests for assembling the server-rendered HTML pages."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import falcon

from api.api_resource import APIResource
from api.tests.support import mock_app_context
from api.utils import page_rendering


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
