# Scryfall's gzipped-JSONL bulk data

Card imports were failing outright with `KeyError: 'download_uri'` from
`get_download_uri_for_key`. Scryfall changed `/bulk-data`: each record's `download_uri` is now
`jsonl_download_uri` (and `size` is now `compressed_size`), and the dumps moved from a
pretty-printed JSON array to gzipped JSONL.

The rename was the visible half. The second break was behind it: the new URLs are `.jsonl.gz`
served as `Content-Type: application/gzip` with **no** `Content-Encoding` header, so requests does
not transparently decompress and `iter_content()` yields raw gzip bytes. Fixing only the field name
would have re-wrapped gzip in zstd, then hit `UnicodeDecodeError` on read — which deletes the cache
and re-raises, so every import would re-download 77 MB and fail again.

Changes to `api/scryfall_bulk_data_fetcher.py`:

- Read the URI from `jsonl_download_uri`, hoisted into `_DOWNLOAD_URI_FIELD`. A missing field now
  raises `BulkDataFormatError` naming the field and listing the fields that *were* present, so the
  next upstream rename reads as a schema change rather than a bare `KeyError`.
- New `_gunzip_if_needed()` decompresses the download stream before it is zstd-cached. It sniffs the
  gzip magic bytes instead of trusting the URL suffix or headers, so it keeps working if Scryfall
  later serves gzip as a `Content-Encoding` that requests decodes for us. It decodes concatenated
  gzip members (otherwise a multi-member dump would silently truncate to its first member) and
  raises on a stream ending mid-member rather than caching a partial dump.
- Cache paths are now `<name>.jsonl.zstd`, not the `<name>.jsonl.json.zstd` that chaining
  `with_suffix` onto a `.jsonl.gz` name would have produced.

The legacy JSON-array tolerance is gone, since the dumps are JSONL only: no more `[`/`]` line
skipping or trailing-comma stripping. An `isinstance(card, dict)` guard replaces the old
`startswith("{")` check — stricter as well as simpler, because it stops a minified single-line array
from being yielded as a `list` where callers expect a `dict`, and it feeds the existing parse
coverage check so a future format change fails loudly instead of under-yielding.

Verified against live Scryfall — all seven dumps stream to exhaustion, every record a dict:

| dump | records |
|---|---:|
| `all_cards` | 538,597 |
| `default_cards` | 116,490 |
| `unique_artwork` | 53,950 |
| `oracle_cards` | 38,485 |
| `rulings` | 77,998 |
| `art_tags` | 11,431 |
| `oracle_tags` | 4,509 |

`rulings` matches `wc -l` on the raw gunzipped file exactly. The `default_cards` import path runs
clean through `preprocess_card` (116,490 cards → 99,407 rows) and the cache file holds plaintext
JSONL. New tests cover the gzip transport (split chunks, concatenated members, truncation, empty
keep-alive chunks, plaintext passthrough), the cache-path suffix, non-object JSONL lines, and the
schema-drift diagnostic. Unit suite 2,129 passed.
