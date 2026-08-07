# ILIKE planning overhead on trigram-indexed text columns

## Problem

Text search queries (`name:`, `oracle:`, `artist:`, `flavor:`) are slow to plan. Planning time
scales roughly linearly with the number of ILIKE conditions:

| ILIKE conditions | Planning time | Execution time |
|-----------------|---------------|----------------|
| 0               | ~0.3 ms       | ~25 ms         |
| 1               | ~40 ms        | ~8 ms          |
| 3               | ~110 ms       | ~3 ms          |

A query like `oracle:counter oracle:flying oracle:sacrifice tou>=5` spends ~110ms planning and ~3ms
executing — the query itself is essentially free, but the planner is the bottleneck.

## Root cause

PostgreSQL uses different selectivity estimators for `LIKE` and `ILIKE`. The `ILIKE` estimator must
account for case-folding across the trigram set, which involves significantly more work than the
`LIKE` estimator. This cost is paid once per `ILIKE` condition at planning time, regardless of
whether the query result is cached afterward.

The current query generator in `card_query_nodes.py` always emits `ILIKE`:

```python
return f"({lhs_sql} ILIKE %({_param_name})s)"
```

The existing GIN trigram indexes (`idx_cards_oracle_text_trgm`, `idx_cards_cardname_trgm`, etc.)
are effective at *execution* time — the trigram bitmap scans are fast. The cost is entirely in
planning.

## Fix

Replace `ILIKE` with `LIKE` by:

1. Creating functional indexes on `lower(column)` for each trigram-indexed text field.
2. Lowercasing the search pattern at query-build time in `card_query_nodes.py`.
3. Emitting `lower(column) LIKE '%pattern%'` instead of `column ILIKE '%pattern%'`.

### Migration

```sql
CREATE INDEX idx_cards_oracle_text_lower_trgm
    ON magic.cards USING gin (lower(oracle_text) gin_trgm_ops);

CREATE INDEX idx_cards_cardname_lower_trgm
    ON magic.cards USING gin (lower(card_name) gin_trgm_ops);

CREATE INDEX idx_cards_artist_lower_trgm
    ON magic.cards USING gin (lower(card_artist) gin_trgm_ops)
    WHERE card_artist IS NOT NULL;

CREATE INDEX idx_cards_flavor_text_lower_trgm
    ON magic.cards USING gin (lower(flavor_text) gin_trgm_ops)
    WHERE flavor_text IS NOT NULL;
```

The existing `ILIKE` indexes can be dropped once the new ones are in place, since any remaining
`ILIKE` uses would fall back to a sequential scan anyway and the `lower()` indexes won't serve them.

### `card_query_nodes.py`

In `_handle_text_field_pattern_matching`, lowercase the pattern and wrap the column:

```python
words = ["", *txt_val.lower().split(), ""]
pattern = "%".join(words)
_param_name = param_name(pattern)
context[_param_name] = pattern
return f"(lower({lhs_sql}) LIKE %({_param_name})s)"
```

No changes needed to the regex path (`~*`) — PostgreSQL's regex selectivity estimator does not have
the same planning overhead as `ILIKE`.

## Trade-offs

- Functional indexes are maintained automatically by PostgreSQL and require no changes to the data
  model.
- `lower()` substring search is semantically equivalent to `ILIKE` for all ASCII input. Non-ASCII
  edge cases (locale-dependent case folding) are unlikely to matter for card names and oracle text.
- The old `ILIKE` indexes become dead weight after this change and should be dropped.

---

## Status

**Resolved — merged to main as [#470](https://github.com/jbylund/sylvan_librarian/pull/470) (2026-05-20).**

See [PR description](../prs/optimize_like.md) for full details of what shipped.
