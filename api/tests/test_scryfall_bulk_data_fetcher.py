"""Tests for ScryfallBulkDataFetcher streaming methods."""

from __future__ import annotations

import gzip
import logging
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import orjson
import pytest
import requests
import zstandard as zstd

from api.scryfall_bulk_data_fetcher import (
    _DOWNLOAD_URI_FIELD,
    _MAX_UNPARSEABLE_LINE_LOGS,
    BulkDataFormatError,
    BulkDataKey,
    BulkDataParseError,
    ScryfallBulkDataFetcher,
)

if TYPE_CHECKING:
    import pathlib

_FAKE_URI = "https://data.scryfall.io/default-cards/default-cards-test.jsonl.gz"
_FAKE_BULK_DATA = {BulkDataKey.DEFAULT_CARDS: {"jsonl_download_uri": _FAKE_URI}}
# What _cache_file_path_for_key derives from _FAKE_URI: .gz stripped, .zstd appended.
_FAKE_CACHE_NAME = "default-cards-test.jsonl.zstd"


def _make_fetcher(cache_directory: pathlib.Path) -> ScryfallBulkDataFetcher:
    fetcher = ScryfallBulkDataFetcher.__new__(ScryfallBulkDataFetcher)
    fetcher.cache_directory = cache_directory
    fetcher.session = MagicMock()
    return fetcher


def _write_cache_file(cache_dir: pathlib.Path, raw_json: bytes) -> pathlib.Path:
    cache_file = cache_dir / "default_cards" / _FAKE_CACHE_NAME
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(zstd.compress(raw_json))
    return cache_file


def _scryfall_jsonl(cards: list[dict]) -> bytes:
    """Produce a Scryfall-style JSONL dump: one compact JSON object per line."""
    return b"".join(orjson.dumps(card) + b"\n" for card in cards)


def _mock_streaming_response(chunks: list[bytes] | object) -> MagicMock:
    """Mock requests response usable as a context manager, yielding chunks from iter_content."""
    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.iter_content.return_value = chunks
    return mock_response


class TestStreamDataForKeyHappyPath:
    @pytest.fixture
    def fetcher(self, tmp_path: pathlib.Path) -> ScryfallBulkDataFetcher:
        return _make_fetcher(tmp_path)

    def test_streams_all_cards_from_cache(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        cards = [{"id": "c1", "name": "Lightning Bolt"}, {"id": "c2", "name": "Counterspell"}]
        _write_cache_file(tmp_path, _scryfall_jsonl(cards))

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert len(result) == 2
        assert result[0] == {"id": "c1", "name": "Lightning Bolt"}
        assert result[1] == {"id": "c2", "name": "Counterspell"}

    def test_single_card_file(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        _write_cache_file(tmp_path, _scryfall_jsonl([{"id": "only", "name": "Dark Ritual"}]))

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert len(result) == 1
        assert result[0]["name"] == "Dark Ritual"

    def test_empty_file_yields_nothing(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        _write_cache_file(tmp_path, _scryfall_jsonl([]))

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == []


class TestStreamDataForKeyLineParsing:
    @pytest.fixture
    def fetcher(self, tmp_path: pathlib.Path) -> ScryfallBulkDataFetcher:
        return _make_fetcher(tmp_path)

    def test_skips_malformed_lines_and_logs_warning(
        self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed JSON lines are skipped; a WARNING is emitted and parsing continues."""
        raw = b'{"id":"good1","name":"Good"}\n{bad json here}\n{"id":"good2","name":"Also Good"}\n'
        _write_cache_file(tmp_path, raw)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), caplog.at_level(logging.WARNING):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert [r["name"] for r in result] == ["Good", "Also Good"]
        assert any("Skipping unparseable" in r.message for r in caplog.records)

    def test_skips_valid_json_that_is_not_an_object(
        self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Lines that parse but aren't objects must not be yielded, or callers get a non-dict card."""
        raw = b'{"id":"c1","name":"A"}\n[{"id":"nested"}]\n"a bare string"\n42\n'
        _write_cache_file(tmp_path, raw)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), caplog.at_level(logging.WARNING):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == [{"id": "c1", "name": "A"}]
        assert all(isinstance(card, dict) for card in result)
        assert sum("Skipping unparseable" in r.message for r in caplog.records) == 3

    def test_ignores_blank_lines(
        self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Blank lines are structural whitespace, not unparseable content, so they log nothing."""
        raw = b'\n{"id":"c1","name":"A"}\n\n'
        _write_cache_file(tmp_path, raw)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), caplog.at_level(logging.WARNING):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == [{"id": "c1", "name": "A"}]
        assert not [r for r in caplog.records if "Skipping unparseable" in r.message]


class TestStreamDataForKeyCacheMiss:
    @pytest.fixture
    def fetcher(self, tmp_path: pathlib.Path) -> ScryfallBulkDataFetcher:
        return _make_fetcher(tmp_path)

    def test_downloads_and_caches_on_miss(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """When no cache file exists, the file is downloaded and written as zstd-compressed cache."""
        raw_json = _scryfall_jsonl([{"id": "dl-1", "name": "Downloaded Card"}])
        fetcher.session.get.return_value = _mock_streaming_response([raw_json])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        cache_file = tmp_path / "default_cards" / _FAKE_CACHE_NAME
        assert cache_file.exists(), "cache file should have been written"
        # Use ZstdDecompressor so we can bound output size via max_output_size.
        dctx = zstd.ZstdDecompressor()
        decompressed = dctx.decompress(cache_file.read_bytes(), max_output_size=10 * 1024 * 1024)
        assert b"Downloaded Card" in decompressed
        assert result == [{"id": "dl-1", "name": "Downloaded Card"}]

    def test_second_call_does_not_re_download(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """Once the cache file exists, subsequent calls must not make HTTP requests."""
        _write_cache_file(tmp_path, _scryfall_jsonl([{"id": "c1", "name": "Cached"}]))

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        fetcher.session.get.assert_not_called()

    def test_prunes_stale_cache_files_before_download(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """Stale cache files in the directory are deleted before writing a new one."""
        stale = tmp_path / "default_cards" / "old-file.json.zstd"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"stale data")

        raw_json = _scryfall_jsonl([{"id": "new", "name": "New Card"}])
        uri = "https://data.scryfall.io/default-cards/default-cards-new.json"
        fetcher.session.get.return_value = _mock_streaming_response([raw_json])

        bulk_data = {BulkDataKey.DEFAULT_CARDS: {"jsonl_download_uri": uri}}
        with patch.object(fetcher, "list_bulk_data", return_value=bulk_data):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert not stale.exists(), "stale file should have been deleted"

    def test_prune_spares_fresh_tmp_files(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """A recent .tmp file may be another worker's in-flight download and must survive pruning."""
        fresh_tmp = tmp_path / "default_cards" / "default-cards-other.json.zstd.12345.abcd.tmp"
        fresh_tmp.parent.mkdir(parents=True, exist_ok=True)
        fresh_tmp.write_bytes(b"in-flight download")

        raw_json = _scryfall_jsonl([{"id": "new", "name": "New Card"}])
        fetcher.session.get.return_value = _mock_streaming_response([raw_json])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert fresh_tmp.exists(), "fresh tmp file should have been spared"

    def test_prune_deletes_abandoned_tmp_files(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """An old .tmp file is an abandoned download (crashed worker) and gets pruned."""
        old_tmp = tmp_path / "default_cards" / "default-cards-other.json.zstd.12345.abcd.tmp"
        old_tmp.parent.mkdir(parents=True, exist_ok=True)
        old_tmp.write_bytes(b"abandoned download")
        two_hours_ago = time.time() - 2 * 60 * 60
        os.utime(old_tmp, (two_hours_ago, two_hours_ago))

        raw_json = _scryfall_jsonl([{"id": "new", "name": "New Card"}])
        fetcher.session.get.return_value = _mock_streaming_response([raw_json])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert not old_tmp.exists(), "abandoned tmp file should have been pruned"

    def test_tmp_file_name_is_unique_per_process(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """The in-flight tmp file name embeds the pid and a random token, so concurrent workers never share it."""
        cache_dir = tmp_path / "default_cards"
        seen_names: list[str] = []

        def _chunks() -> object:
            seen_names.extend(p.name for p in cache_dir.iterdir())
            yield _scryfall_jsonl([{"id": "c1", "name": "A"}])

        fetcher.session.get.return_value = _mock_streaming_response(_chunks())

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        tmp_names = [n for n in seen_names if n.endswith(".tmp")]
        assert tmp_names, "a .tmp file should exist while the download is in flight"
        assert all(f".{os.getpid()}." in n for n in tmp_names)


class TestScryfallApiSession:
    def test_session_does_not_use_default_user_agent(self) -> None:
        """Scryfall rejects default HTTP-library User-Agents with 400 generic_user_agent."""
        fetcher = ScryfallBulkDataFetcher()
        user_agent = fetcher.session.headers["User-Agent"]
        assert not user_agent.startswith("python-requests")
        assert fetcher.session.headers["Accept"] == "application/json"

    def test_list_bulk_data_raises_on_http_error(self, tmp_path: pathlib.Path) -> None:
        """A non-2xx bulk-data response raises HTTPError instead of KeyError('data') on the error body."""
        fetcher = _make_fetcher(tmp_path)
        error_response = MagicMock()
        error_response.raise_for_status.side_effect = requests.HTTPError("400 Client Error: Bad Request")
        fetcher.session.get.return_value = error_response

        with pytest.raises(requests.HTTPError):
            fetcher.list_bulk_data()

    def test_list_bulk_data_ignores_unrecognized_types(self, tmp_path: pathlib.Path) -> None:
        """Bulk data types Scryfall adds in the future are skipped, not a ValueError crash."""
        fetcher = _make_fetcher(tmp_path)
        response = MagicMock()
        response.json.return_value = {
            "data": [
                {"type": "default_cards", "jsonl_download_uri": _FAKE_URI},
                {"type": "future_type", "jsonl_download_uri": "https://data.scryfall.io/future/whatever.json"},
            ],
        }
        fetcher.session.get.return_value = response

        result = fetcher.list_bulk_data()

        assert result == {BulkDataKey.DEFAULT_CARDS: {"type": "default_cards", "jsonl_download_uri": _FAKE_URI}}


class TestStreamDataForKeyCorruptCache:
    @pytest.fixture
    def fetcher(self, tmp_path: pathlib.Path) -> ScryfallBulkDataFetcher:
        return _make_fetcher(tmp_path)

    def test_corrupt_cache_file_is_deleted_and_raises(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """A cache file that fails zstd decompression is deleted so the next call can re-download."""
        cache_file = tmp_path / "default_cards" / _FAKE_CACHE_NAME
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(b"this is not a zstd stream")

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), pytest.raises(zstd.ZstdError):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert not cache_file.exists(), "corrupt cache file should have been deleted"

    def test_invalid_utf8_cache_file_is_deleted_and_raises(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """A valid zstd file whose content is not valid UTF-8 is also treated as corrupt and deleted."""
        invalid_utf8 = b'[\n{"id":"c1","name":"truncated \xe2\x82'  # multibyte char cut short
        cache_file = _write_cache_file(tmp_path, invalid_utf8)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), pytest.raises(UnicodeDecodeError):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert not cache_file.exists(), "invalid-utf8 cache file should have been deleted"

    def test_call_after_corruption_re_downloads(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """After a corrupt cache file is deleted, the next call downloads a fresh copy and succeeds."""
        cache_file = tmp_path / "default_cards" / _FAKE_CACHE_NAME
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(b"this is not a zstd stream")

        raw_json = _scryfall_jsonl([{"id": "c1", "name": "Recovered"}])
        fetcher.session.get.return_value = _mock_streaming_response([raw_json])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            with pytest.raises(zstd.ZstdError):
                list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        fetcher.session.get.assert_called_once()
        assert result == [{"id": "c1", "name": "Recovered"}]


def _padded_card(idx: int, pad_chars: int = 1000) -> dict:
    """Card dict padded to roughly pad_chars so tests can cheaply build files past the coverage-check minimum size."""
    return {"id": f"card-{idx}", "name": f"Card {idx}", "pad": "x" * pad_chars}


class TestStreamDataForKeyParseCoverage:
    """Large files must parse at least the coverage threshold of their content into cards."""

    @pytest.fixture
    def fetcher(self, tmp_path: pathlib.Path) -> ScryfallBulkDataFetcher:
        return _make_fetcher(tmp_path)

    def test_whole_array_on_one_line_raises(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """A dump that reverts to a single-line JSON array parses as a list, yields no cards, and must raise."""
        cards = [_padded_card(i) for i in range(1200)]  # ~1.2MB as a single line
        _write_cache_file(tmp_path, orjson.dumps(cards) + b"\n")

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), pytest.raises(BulkDataParseError):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

    def test_mostly_unparseable_large_file_raises(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """A large file whose content is mostly garbage lines raises even though some cards parsed."""
        raw = _scryfall_jsonl([_padded_card(i) for i in range(400)])
        raw += b"".join(b"{not valid json " + b"y" * 1000 + b"\n" for _ in range(800))
        _write_cache_file(tmp_path, raw)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), pytest.raises(BulkDataParseError):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

    def test_small_fraction_of_garbage_in_large_file_is_tolerated(
        self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path
    ) -> None:
        """Garbage below the threshold is skipped with warnings, as for small files."""
        raw = _scryfall_jsonl([_padded_card(i) for i in range(1000)])
        raw += b"".join(b"{not valid json " + b"y" * 1000 + b"\n" for _ in range(100))
        _write_cache_file(tmp_path, raw)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert len(result) == 1000

    def test_unparseable_line_warnings_are_capped(
        self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Per-line warnings stop after the cap; a single summary line reports the total."""
        raw = _scryfall_jsonl([_padded_card(i) for i in range(1000)])
        raw += b"".join(b"{not valid json " + b"y" * 1000 + b"\n" for _ in range(100))
        _write_cache_file(tmp_path, raw)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), caplog.at_level(logging.WARNING):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        per_line_warnings = [r for r in caplog.records if "Skipping unparseable" in r.message]
        summaries = [r for r in caplog.records if "unparseable lines total" in r.message]
        assert len(per_line_warnings) == _MAX_UNPARSEABLE_LINE_LOGS
        assert len(summaries) == 1
        assert "100" in summaries[0].message

    def test_small_files_are_exempt_from_coverage_check(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """Files below the minimum size never trigger the coverage check, even at 0% coverage."""
        _write_cache_file(tmp_path, b"{bad json}\n")

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == []


class TestGzippedJsonlBulkData:
    """Scryfall serves gzipped JSONL dumps via `jsonl_download_uri`, with no Content-Encoding header."""

    @pytest.fixture
    def fetcher(self, tmp_path: pathlib.Path) -> ScryfallBulkDataFetcher:
        return _make_fetcher(tmp_path)

    def test_cache_path_strips_gz_and_appends_zstd(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """The upstream .gz suffix is dropped, since we store zstd-recompressed plaintext."""
        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            path = fetcher._cache_file_path_for_key(BulkDataKey.DEFAULT_CARDS)

        assert path == tmp_path / "default_cards" / "default-cards-test.jsonl.zstd"

    def test_gzipped_jsonl_download_is_decompressed_and_streamed(
        self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path
    ) -> None:
        """A gzip payload must be gunzipped before zstd-caching, not stored as opaque bytes."""
        cards = [{"id": "c1", "name": "Lightning Bolt"}, {"id": "c2", "name": "Counterspell"}]
        fetcher.session.get.return_value = _mock_streaming_response([gzip.compress(_scryfall_jsonl(cards))])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == cards
        dctx = zstd.ZstdDecompressor()
        cached = dctx.decompress((tmp_path / "default_cards" / _FAKE_CACHE_NAME).read_bytes(), max_output_size=1 << 20)
        assert cached.startswith(b'{"id":"c1"'), "cache must hold plaintext JSONL, not gzip bytes"

    def test_gzip_split_across_chunks(self, fetcher: ScryfallBulkDataFetcher) -> None:
        """Decompression must work when gzip members are split arbitrarily across iter_content chunks."""
        cards = [{"id": f"c{i}", "name": f"Card {i}"} for i in range(50)]
        blob = gzip.compress(_scryfall_jsonl(cards))
        chunk_size = 7  # deliberately tiny, so members split mid-header and mid-trailer
        chunks = [blob[i : i + chunk_size] for i in range(0, len(blob), chunk_size)]
        fetcher.session.get.return_value = _mock_streaming_response(chunks)

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == cards

    def test_concatenated_gzip_members_are_all_read(self, fetcher: ScryfallBulkDataFetcher) -> None:
        """Multi-member gzip is one logical stream; every member must be decompressed."""
        first = [{"id": "a", "name": "A"}]
        second = [{"id": "b", "name": "B"}]
        blob = gzip.compress(_scryfall_jsonl(first)) + gzip.compress(_scryfall_jsonl(second))
        fetcher.session.get.return_value = _mock_streaming_response([blob])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == first + second

    def test_truncated_gzip_raises_and_leaves_no_cache_file(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """A download cut off mid-member must fail loudly rather than cache a partial dump."""
        blob = gzip.compress(_scryfall_jsonl([{"id": f"c{i}", "name": f"Card {i}"} for i in range(50)]))
        fetcher.session.get.return_value = _mock_streaming_response([blob[: len(blob) // 2]])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA), pytest.raises(BulkDataFormatError):
            list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert list((tmp_path / "default_cards").iterdir()) == [], "no cache or tmp file should survive"

    def test_uncompressed_payload_still_passes_through(self, fetcher: ScryfallBulkDataFetcher) -> None:
        """If the body is already plaintext (e.g. requests decoded a Content-Encoding), pass it through."""
        cards = [{"id": "c1", "name": "Plain"}]
        fetcher.session.get.return_value = _mock_streaming_response([_scryfall_jsonl(cards)])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == cards

    def test_empty_leading_chunks_are_tolerated(self, fetcher: ScryfallBulkDataFetcher) -> None:
        """iter_content can emit empty keep-alive chunks before the real body."""
        cards = [{"id": "c1", "name": "Keepalive"}]
        fetcher.session.get.return_value = _mock_streaming_response([b"", b"", gzip.compress(_scryfall_jsonl(cards))])

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert result == cards

    def test_large_jsonl_file_passes_coverage_check(self, fetcher: ScryfallBulkDataFetcher, tmp_path: pathlib.Path) -> None:
        """JSONL yields ~100% parse coverage, so a big real-shaped dump must not trip BulkDataParseError."""
        cards = [_padded_card(i) for i in range(1200)]  # ~1.2MB, past _PARSE_COVERAGE_MIN_CHARS
        _write_cache_file(tmp_path, _scryfall_jsonl(cards))

        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            result = list(fetcher.stream_data_for_key(BulkDataKey.DEFAULT_CARDS))

        assert len(result) == 1200


class TestBulkDataSchemaDrift:
    """A renamed or removed download-URI field must name the problem, not surface as a bare KeyError."""

    def test_missing_download_uri_field_raises_with_diagnostics(self, tmp_path: pathlib.Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        # The exact shape that broke production: the record exists, the URI field does not.
        stale_schema = {BulkDataKey.DEFAULT_CARDS: {"type": "default_cards", "download_uri": _FAKE_URI}}

        with patch.object(fetcher, "list_bulk_data", return_value=stale_schema), pytest.raises(BulkDataFormatError) as exc:
            fetcher.get_download_uri_for_key(BulkDataKey.DEFAULT_CARDS)

        message = str(exc.value)
        assert _DOWNLOAD_URI_FIELD in message
        assert "download_uri" in message, "the message should list the fields that were present"

    def test_download_uri_read_from_jsonl_field(self, tmp_path: pathlib.Path) -> None:
        fetcher = _make_fetcher(tmp_path)
        with patch.object(fetcher, "list_bulk_data", return_value=_FAKE_BULK_DATA):
            assert fetcher.get_download_uri_for_key(BulkDataKey.DEFAULT_CARDS) == _FAKE_URI
