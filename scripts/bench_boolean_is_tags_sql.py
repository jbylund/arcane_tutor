#!/usr/bin/env python3
"""Compare old (subquery) vs new (jsonb_build_object) BOOLEAN_IS_TAGS sync SQL on blue."""

from __future__ import annotations

import re
import statistics
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from api.admin_resource import BOOLEAN_IS_TAGS, _build_boolean_is_tags_sql  # noqa: E402


def _build_legacy_boolean_is_tags_sql(
    tags: dict[str, str],
    *,
    chunk_index: int | None = None,
    num_chunks: int | None = None,
) -> str:
    """Pre-refactor shape: correlated subquery over a VALUES list."""
    chunk_filter = ""
    if chunk_index is not None and num_chunks is not None:
        chunk_filter = (
            f"\n    WHERE (abs(hashtext(cards.scryfall_id::text)) % {num_chunks}) = {chunk_index}"
        )

    managed = ", ".join(f"'{tag}'" for tag in tags)
    values = ",\n                        ".join(f"('{tag}', ({expr}))" for tag, expr in tags.items())
    return f"""
WITH proposed AS (
    SELECT
        cards.scryfall_id,
        (cards.card_is_tags - ARRAY[{managed}]::text[])
            || COALESCE(
                   (
                       SELECT jsonb_object_agg(t.tag, true)
                       FROM (VALUES
                        {values}
                       ) AS t(tag, is_true)
                       WHERE t.is_true
                   ),
                   '{{}}'::jsonb
               ) AS proposed_is_tags
    FROM magic.cards cards{chunk_filter}
)
SELECT COUNT(*) AS rows_scanned,
       COUNT(*) FILTER (WHERE cards.card_is_tags IS DISTINCT FROM proposed.proposed_is_tags) AS rows_changed
FROM proposed
JOIN magic.cards cards USING (scryfall_id)
"""


def _build_new_boolean_is_tags_sql(
    tags: dict[str, str],
    *,
    chunk_index: int | None = None,
    num_chunks: int | None = None,
) -> str:
    update_sql = _build_boolean_is_tags_sql(tags, chunk_index=chunk_index, num_chunks=num_chunks)
    return update_sql.replace(
        "UPDATE magic.cards\nSET card_is_tags = proposed.proposed_is_tags\nFROM proposed\nWHERE\n"
        "    cards.scryfall_id = proposed.scryfall_id AND\n"
        "    cards.card_is_tags IS DISTINCT FROM proposed.proposed_is_tags\n",
        "SELECT COUNT(*) AS rows_scanned,\n"
        "       COUNT(*) FILTER (WHERE cards.card_is_tags IS DISTINCT FROM proposed.proposed_is_tags) AS rows_changed\n"
        "FROM proposed\n"
        "JOIN magic.cards cards USING (scryfall_id)\n",
    )


def _run_psql(sql: str) -> str:
    cmd = [
        "docker",
        "compose",
        "--project-name",
        "sylvan_blue",
        "--env-file",
        str(REPO / "envs/blue"),
        "--file",
        str(REPO / "docker-compose.yml"),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "foouser",
        "-d",
        "magic",
        "--host=localhost",
        "-v",
        "ON_ERROR_STOP=1",
        "-P",
        "pager=off",
    ]
    wrapped = textwrap.dedent(
        f"""
        SET statement_timeout = 0;
        {sql}
        """
    )
    proc = subprocess.run(cmd, input=wrapped, capture_output=True, text=True, check=False, cwd=REPO)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        proc.check_returncode()
    return proc.stdout


def _parse_explain_ms(out: str) -> float | None:
    matches = re.findall(r"Execution Time: ([0-9.]+) ms", out)
    if not matches:
        return None
    return float(matches[-1])


def _explain(label: str, sql: str) -> float | None:
    print(f"\n=== {label}: EXPLAIN (ANALYZE, BUFFERS) ===")
    out = _run_psql(f"EXPLAIN (ANALYZE, BUFFERS, TIMING)\n{sql}")
    print(out)
    return _parse_explain_ms(out)


def _time_runs(label: str, sql: str, runs: int = 3) -> list[float]:
    timings: list[float] = []
    for _ in range(runs):
        out = _run_psql("\\timing on\n" + sql)
        match = re.search(r"Time:\s+([0-9.]+)\s+ms", out)
        if not match:
            print(out)
            msg = f"could not parse \\timing output for {label}"
            raise RuntimeError(msg)
        timings.append(float(match.group(1)))
    print(f"\n=== {label}: \\timing over {runs} runs (ms) ===")
    print(f"  runs:   {timings}")
    print(f"  median: {statistics.median(timings):.0f} ms")
    print(f"  mean:   {statistics.mean(timings):.0f} ms")
    return timings


def main() -> None:
    card_count = _run_psql("SELECT COUNT(*) AS n FROM magic.cards;")
    print("=== corpus ===")
    print(card_count.strip())

    full_legacy = _build_legacy_boolean_is_tags_sql(BOOLEAN_IS_TAGS)
    full_new = _build_new_boolean_is_tags_sql(BOOLEAN_IS_TAGS)

    print("\n=== correctness check (full scan) ===")
    print(_run_psql(full_legacy).strip())
    print(_run_psql(full_new).strip())

    legacy_explain = _explain("legacy subquery (full)", full_legacy)
    new_explain = _explain("jsonb_build_object (full)", full_new)
    if legacy_explain is not None and new_explain is not None:
        delta = new_explain - legacy_explain
        pct = (delta / legacy_explain) * 100 if legacy_explain else 0
        print(f"\n=== full-scan execution time delta (new - legacy) ===")
        print(f"  legacy: {legacy_explain:.1f} ms")
        print(f"  new:    {new_explain:.1f} ms")
        print(f"  delta:  {delta:+.1f} ms ({pct:+.1f}%)")

    legacy_times = _time_runs("legacy subquery (full)", full_legacy)
    new_times = _time_runs("jsonb_build_object (full)", full_new)
    print(
        f"\n=== full-scan \\timing delta (new - legacy median) ==="
        f" {statistics.median(new_times) - statistics.median(legacy_times):+.0f} ms"
    )

    chunk_legacy = _build_legacy_boolean_is_tags_sql(BOOLEAN_IS_TAGS, chunk_index=0, num_chunks=4)
    chunk_new = _build_new_boolean_is_tags_sql(BOOLEAN_IS_TAGS, chunk_index=0, num_chunks=4)
    legacy_chunk_explain = _explain("legacy subquery (chunk 0/4)", chunk_legacy)
    new_chunk_explain = _explain("jsonb_build_object (chunk 0/4)", chunk_new)
    if legacy_chunk_explain is not None and new_chunk_explain is not None:
        delta = new_chunk_explain - legacy_chunk_explain
        pct = (delta / legacy_chunk_explain) * 100 if legacy_chunk_explain else 0
        print(f"\n=== chunk execution time delta (new - legacy) ===")
        print(f"  legacy: {legacy_chunk_explain:.1f} ms")
        print(f"  new:    {new_chunk_explain:.1f} ms")
        print(f"  delta:  {delta:+.1f} ms ({pct:+.1f}%)")

    legacy_chunk_times = _time_runs("legacy subquery (chunk 0/4)", chunk_legacy)
    new_chunk_times = _time_runs("jsonb_build_object (chunk 0/4)", chunk_new)
    print(
        f"\n=== chunk \\timing delta (new - legacy median) ==="
        f" {statistics.median(new_chunk_times) - statistics.median(legacy_chunk_times):+.0f} ms"
    )


if __name__ == "__main__":
    main()
