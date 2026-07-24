# Engine: Clean Clippy and Gate It in CI

Status: **implemented 2026-07-24** on `engine-clippy-clean` (stacked on
[#757](00757-engine-query-ctx-arg-bundle.md)). Not filed as a GitHub issue — found while scoping #757,
implemented immediately after it.

## The finding

Clippy was not running in CI at all, and was not clean.

- [rust-tests.yml](../../.github/workflows/rust-tests.yml) ran only `cargo test` for the two crates.
- [lint.yml](../../.github/workflows/lint.yml) is Python-only: its `paths` filter is `**/*.py`,
  `requirements/**`, `pyproject.toml`, and itself. No Rust path can trigger it.
- `cargo clippy --all-targets` emitted **68 warnings** on `card_engine` and 3 on `shared_cache`.

The consequence worth naming: `card_engine` carried 25 hand-written
`#[allow(clippy::too_many_arguments)]` attributes — deliberate, maintained-by-hand suppressions for a
lint **nothing enforced**. Two functions were over the same threshold with no `allow` at all
(`run_query` at 13/7, the PyO3 `query` at 10/7), which is what a silent lint looks like after a while.
This is also why the gate is a prerequisite for #757's payoff rather than an unrelated chore: that
change sheds 15 of those attributes, and without enforcement the count just drifts back up.

## What changed

**Fixed, not suppressed** (the bulk):

| Lint | n | Resolution |
| --- | --- | --- |
| `useless_conversion` | 13 | `u8::from(*v) as f32` → `f32::from(*v)`; the archived 1-byte ints need no endian unwrap, and `f32::from` states the widening without an `as` cast's lossy-looking ambiguity |
| `collapsible_if` | 8 | Collapsed to let-chains (edition 2024), **hand-reindented** — `clippy --fix` leaves the bodies at their old depth and there is no rustfmt in this repo to clean up after it |
| `needless_range_loop` | 4 | Rewrote to slice iteration where the index was only an index |
| `doc_list_item_without_indentation` | 10 | Two doc comments had a paragraph immediately after a 3-space-indented list, so markdown absorbed it as a lazy continuation; dedented the markers and added the blank `///` separator |
| `type_complexity` | 2 | Promoted the tuple's existing explanatory comment into a named type (`FitRow`, `ManaQuery`) with per-field comments |
| `derivable_impls` | 1 | 36-line hand-written `impl Default for CardIndexes` → `#[derive(Default)]` (every field was already its type's default) |
| `ptr_arg` | 1 | `push_card_matches`'s `group_best: &mut Vec<_>` → `&mut [_]`; it indexes and assigns but never resizes, and the slice says so in the type |
| `needless_borrow`, `needless_lifetimes`, `unnecessary_get_then_check`, `byte_char_slices`, … | ~15 | Machine-applied via `clippy --fix`, reviewed by hand |

**Suppressed with a stated reason** (12 `needless_range_loop` + 2 naming/API):

- 10 loops in `lib.rs` keep `#[allow(clippy::needless_range_loop)]` at function level because `pid` is
  the printing's *identity*, not a cursor — it goes into the emitted match tuple as `pid as u32`. A
  slice iterator would lose it. This follows the precedent already in the file at
  `gather_composed_page`, whose allow carried exactly this rationale.
- `solve_normal_eqs` indexes two different rows of `a` in one statement (split_at_mut would obscure
  the elimination step); `printing_range_fixture`'s `i` is both a synthesized card id and a `ranks`
  index.
- `TextSearchField`'s `enum_variant_names`: the shared `Lower` suffix is load-bearing — it names the
  case/accent-folded store columns search actually reads, not the display columns.
- The three PyO3 keyword surfaces (`query` 10/7, `explain` 8/7, and `run_query` behind them) keep
  `too_many_arguments`: `self` counts toward clippy's 7, and the keyword list is the API.

## Two substantive findings

Neither was a live bug, but both were code that read as if it did something it didn't.

**A comparison that was always false.** In `bench_range_compose_kernels`:

```rust
let s = idx.partition_point(|p| u32::from(p.0) < 0); // usd<50 -> [0, 5000) cents
```

Prices are unsigned, so this predicate is never true and `partition_point` returns 0 — which is the
*correct* low bound for a range starting at 0, so the bench was right by accident. It read as a binary
search for something. Replaced with an explicit `let s = 0usize` plus a named `HI_CENTS` for the real
bound (per the repo's named-constants convention) and a comment saying why no search is needed. This
was the one lint reported at `error:` level, not `warning:` — `absurd_extreme_comparisons` is
deny-by-default, so `cargo clippy` had been exiting non-zero the whole time and nothing was watching.

**An open mode that didn't say what it meant.** The reload lock file was opened
`.write(true).create(true)` with no `truncate`, which clippy flags as undefined intent. Nothing is
ever written to that file — it exists only as an `flock` target — so truncation must never happen.
Added an explicit `.truncate(false)` and a comment; behavior is unchanged, the intent is now in the
code.

## The gate

Two `cargo clippy --all-targets -- -D warnings` steps (one per crate) added to the **existing**
`rust-test` job rather than as a new job. Three reasons, in order:

1. A new job means a new required check, which means
   [rust-tests-skip.yml](../../.github/workflows/rust-tests-skip.yml) needs a matching stub job or
   PRs touching no `.rs` file hang forever on a check that never runs. Steps in the existing job need
   no such coordination.
2. `rust-tests.yml`'s `paths` filter (`**/*.rs`, `**/Cargo.*`) is already exactly the right trigger.
3. Placed **after** the test steps deliberately: `-D warnings` aborts the job, so lint-first would
   hide a genuine test failure behind a style nit.

`--all-targets` matters — most of the 68 warnings were in `tests.rs` and the bench modules, which a
bare `cargo clippy` never compiles.

## Follow-up: `shared_cache` has the same arg-bundling smell

The three `shared_cache` warnings are all `too_many_arguments`, and two of them are the same pattern
#757 fixed in the engine: `write_init_headers` takes nine layout parameters both call sites already
hold as separate values, and `do_insert` takes the seven fields of the entry being written. Both want
a struct. `set` (8) is the crate's public cache-write surface and mirrors the HTTP response it stores,
so grouping it would be an API change. All three are allowed with those reasons recorded inline; the
bundling is worth its own change and is not folded in here.

## Related

- [00757-engine-query-ctx-arg-bundle.md](00757-engine-query-ctx-arg-bundle.md) — the arg-bundling work
  this is stacked on; it sheds 15 of the `too_many_arguments` allows, and this gate is what keeps them
  shed.
