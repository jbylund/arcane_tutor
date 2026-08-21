# Should `QueryError` Split Into "Worth Retrying on SQL" vs. "Just Garbage"?

[#984](https://github.com/jbylund/sylvan_librarian/issues/984).

Proposed during the #939 follow-up (dropping `_EngineDeclinedQueryError`, see
[done/00939](done/00939-engine-declined-error-not-http-error.md)): today every `card_engine.QueryError`
gets the same treatment — `_search` logs it at info and falls through to `_search_sql`. The idea is a
single `EngineError` base with two subclasses: one meaning "the engine is missing some theoretically
possible functionality, SQL is worth trying," the other meaning "this is garbage that ideally would
have been caught before it ever reached the engine" — catch only the first, and re-raise the second
as whatever 400 it should have been.

## The six actual failure sites

`card_engine::QueryError` wraps `build_filter`'s `Result<FilterExpr, String>`
(`card_engine/src/filter.rs`). Six distinct `Err(...)` sites feed it today:

| Failure | Likely category | Confidence |
|---|---|---|
| `regex not supported on {attr}` | Worth retrying — SQL supports `~*` on any text column, engine just hasn't wired it up for this attribute | High |
| `text substring not supported on {attr}` | Worth retrying — same shape | High |
| `unknown text field: {attr}` | Worth retrying, *if* the field already passed the parser's alias validation (`ALIAS_TO_FIELD_INFOS`) to get this far — then it's a real field the engine's JSON→filter mapping hasn't implemented, not user garbage | Medium — not verified that every field reaching here passed parser validation |
| `bad date: {val_str}` | Unclear — garbage if Rust's date parser only rejects dates Postgres would also reject; worth-retrying if Rust is stricter on *format* than Postgres | Unverified |
| `unexpected top-level node type` | Unclear — could be a genuine internal AST/schema mismatch (a bug) or an engine gap on a node type the parser can legitimately produce | Unverified |
| `invalid regex '{pattern}': {e}` (Rust `regex` crate syntax rejection) | **Neither, cleanly** — see below | N/A |

## The case that breaks a clean two-way split

The PR that introduced this whole area already documents that invalid-regex-syntax splits into two
outcomes that look identical at the point Rust catches `regex::Error`: some patterns are invalid
everywhere (`o:/^[/`), others Rust's `regex` crate rejects but Postgres's regex engine accepts
(backreferences, lookaround). The Rust engine cannot tell these apart when it catches the error — it
would have to ask Postgres first, which is exactly what "retry on SQL" already does.

So even with a clean 2-subclass taxonomy, this one failure mode still needs the "try SQL and see"
path — the split would speed up (skip a wasted SQL round-trip for) the other five cases, not eliminate
the fallback path entirely. Worth deciding up front whether that's still worth the Rust-side change.

## Why this is a bigger change than it looks

Unlike the Python-only follow-ups already stacked on #909 (#941, #944, #950, #939), this touches the
Rust engine itself: the `create_exception!` hierarchy in `card_engine/src/lib.rs`, every `Err(...)`
site in `build_filter` (`card_engine/src/filter.rs`), and requires a `cargo build`/`maturin develop`
rebuild before any of it is even testable from Python. Scope it as its own PR, not folded into a
Python cleanup batch.

## Before implementing

1. Verify the `bad date` and `unexpected top-level node type` cases against real Postgres behavior —
   construct queries that hit each, check whether SQL actually succeeds where the engine declined.
   Both are currently unverified guesses above.
2. Decide whether "unknown text field" can actually be reached with a field that passed parser
   validation, or whether that path is dead/defensive-only.
3. Decide whether the regex-syntax case's inherent unsplittability changes the cost/benefit — the
   Rust-side change buys a faster 400 for up to 5 of 6 cases, but the 6th (arguably the most common
   one in practice, since regex patterns are the documented motivation for this whole retry path)
   still needs the full round-trip either way.
