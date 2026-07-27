# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sylvan Librarian is an open-source Scryfall-compatible Magic: The Gathering card search engine. It parses a Scryfall-like query DSL, converts queries to optimized PostgreSQL, and serves results via a Falcon REST API with a vanilla JS frontend. It extends Scryfall syntax with arithmetic expressions (e.g., `cmc+1<power`).

## Commands

```bash
# Run all tests (779 total)
make test
# or: python -m pytest -vvv --capture=no --durations=10

# Run a single test file
python -m pytest api/parsing/tests/test_parsing.py -vvv

# Run a single test by name
python -m pytest -vvv -k "test_my_test_name"

# Unit tests only (no Docker required)
make test-unit

# Integration tests only (requires Docker)
make test-integration

# Coverage report
make coverage

# Lint (ruff + prettier)
make lint

# Auto-fix lint issues
python -m ruff check --fix --unsafe-fixes .
python -m ruff format .

# Start services (dev mode)
make dev-up

# Connect to local database (execs into the postgres container — no external port exposed)
make dbconn-blue  # blue environment
make dbconn-green # green environment
# also: dbconn-dev
```

## Architecture

### Request Flow

```
Browser → GET /search?q=<query>
  → api/api_resource.py (Falcon sink handler)
    → api/parsing/parsing_f.py (pyparsing DSL → AST)
    → api/api_resource.py (AST → parameterized SQL)
    → PostgreSQL (magic schema)
  → JSON response (cached by CachingMiddleware)
```

### Key Directories

- **`api/parsing/`** — Core query parser (~2,500 lines). `parsing_f.py` drives the pyparsing grammar; `nodes.py` defines AST node types; `card_query_nodes.py` has card-specific nodes; `db_info.py` maps query fields to DB columns.
- **`api/api_resource.py`** — All HTTP routing (Falcon sink), search logic, SQL generation from AST, and bulk import endpoints.
- **`api/entrypoint.py`** + **`api/api_worker.py`** — Multi-process Bjoern WSGI server startup.
- **`api/db/`** — PostgreSQL schema SQL (`2025-09-29-great-reset.sql`). The `magic.cards` table has 22 specialized indices (trigram GIN for text, GIN for JSONB arrays, B-tree for numerics).
- **`api/tests/`** — Integration tests using `testcontainers` (spins up a real PostgreSQL instance).
- **`api/parsing/tests/`** — 544 parser unit tests.
- **`api/static/`** — `app.js` (vanilla JS), `app.min.js` (minified for production).
- **`client/query_runner.py`** — Load testing / query diversity tool.
- **`scripts/`** — Font subsetting, minification, DB helpers.

### Middleware Stack (applied in order)

`TimingMiddleware` → `CachingMiddleware` → `CompressionMiddleware` (gzip/brotli/zstd) → `SecurityHeadersMiddleware` → `CORSMiddleware`

### Parser → SQL Pipeline

1. `parsing_f.py` converts a query string into a tree of AST nodes (defined in `nodes.py` and `card_query_nodes.py`).
2. Each node implements a method that emits a SQL fragment + bound parameters.
3. `api_resource.py` wraps the fragment in a `SELECT` against `magic.cards` with `ORDER BY` scoring logic and a `LIMIT` clause.
4. All user input reaches the database only via parameterized queries.

### Database

- PostgreSQL 17+, schema: `magic`
- Primary table: `magic.cards` — `scryfall_id` (UUID PK), numeric columns (`cmc`, `creature_power`, `creature_toughness`, `planeswalker_loyalty`), JSONB columns (`card_colors`, `card_color_identity`, `card_keywords`, `card_legalities`, `mana_cost_jsonb`, etc.), text columns (`card_name`, `oracle_text`, `flavor_text`).
- Tag system: `magic.tags` + `magic.tag_relationships` (with circular-reference trigger).
- Custom DB functions: `rarity_text_to_int()`, `rarity_int_to_text()`, `extract_collector_number_int()`, `get_tag_ancestors()`, `get_tag_descendants()`.

## Linting / Style

- **Python:** `ruff` (line length 132, Google docstring convention, target Python 3.13). Config in `pyproject.toml`.
- **HTML/JS:** `prettier` (config in `.prettierrc`).
- Tests relax many ruff rules (see `per-file-ignores` in `pyproject.toml`).

## Blog Posts

Blog posts live in `docs/blog/posts/<slug>/index.md`. Writing guidance:

- **Rubric:** `docs/blog/post-grading-rubric.md` — 100-point rubric covering technical accuracy,
  concrete evidence, clarity, narrative cohesion, honest tradeoffs, and writing quality. Read before
  writing or reviewing a post.
- **HN guidance:** `docs/blog/hn-content-guidance.md` and `docs/blog/hn-title-guidance.md` — what
  makes a post land on Hacker News vs. get ignored.
- **Post plan:** `docs/blog/blog-post-plan.md` — planned and in-progress posts.

Blog posts are not subject to the 100-line length convention in the global markdown rules. Length
should match the story: long enough to explain the mechanism and show evidence, no longer.

## Issue Tracking

`docs/issues/` holds the deep design/implementation notes for both engine and product work — this
is the primary source of truth, tracked in git (all of it, no exceptions; `docs/issues/done/` for
finished work). GitHub issues are secondary: a triage-and-status layer that must stand on its own —
a reader should understand the problem and the gist of the approach from the GitHub issue alone,
without needing to open the doc — but the doc is where the real depth (measurements, rejected
alternatives, iteration history) lives, so the GitHub issue doesn't need to duplicate all of that,
just link to it.

**Naming:** `#####-slug.md`, 5-digit zero-padded GitHub issue (or PR, if no issue exists)
number, e.g. `00623-engine-flavor-absent-gram-bitmap.md` for #623. Prefer the issue number over its
merging PR's number when both exist. Docs with no GitHub issue of their own use a prefix that signals
intent, so a reader can tell backlog from background at a glance:

- `local-slug.md` — **proposed / not-yet-filed work**: a design or fix meant to happen but not yet
  filed as an issue (e.g. `local-engine-range-veto-redundancy.md`). When a `local-` doc later gets a
  GitHub issue, rename it to the assigned number.
- `reference-slug.md` — **reference material that is deliberately not scheduled**: a general pattern,
  design study, or rejected-but-preserved approach that is *not* todo work and shouldn't be read as a
  backlog item (e.g. `reference-engine-printing-varying-plane-repair-pattern.md`). Promote to `local-`
  or a number only if it actually becomes planned work.
- `security-slug.md` — **unfixed security findings**: the one exception to "all of it, tracked". This
  repo is public, so committing a description of a live, unfixed vulnerability publishes a working
  recipe against the running deployment — permanently, since removing the file later does not remove
  it from history. `docs/issues/security-*` is gitignored to make that mechanical rather than a thing
  to remember. **When the finding is fixed, rename it off the prefix** (to `local-`, a number, or
  `done/`) and commit it — at that point the write-up is a useful record instead of a map, and it
  should not stay invisible.

  Two caveats that come with the gitignore. It protects against `git add -A` and `git clean -fd`, but
  **not** `git clean -fdx`, which deletes ignored files. And these docs exist on one machine only, so
  a finding worth keeping needs a home outside the working tree — see below.

  **The design doc for the fix usually needs the prefix too, and the bar is not "did I omit the
  specifics."** It is whether a reader could derive the attack from what remains. A doc explaining
  *why* a boundary is missing generally shows *where* it is missing, so in practice these fail the
  test even when written carefully — default to `security-` and rename once the fix lands. Only a
  genuinely general architecture note, one that would read the same had no vulnerability prompted it,
  belongs in-tree before the fix.

  While fixing, keep commit messages boring and factual (`Refactor: Extract Router from APIResource`,
  not `Fix: unauthenticated admin endpoints`). Commits on a public repo are visible in real time, and
  a message that advertises the defect narrows the window between disclosure and deploy.

  Findings split by blast radius, and it changes where they belong. A defect in code or compose that
  **ships in the repo** affects everyone who deploys it, so it gates tagged releases (see
  [#778](https://github.com/jbylund/sylvan_librarian/issues/778)) — fix before releasing and there is
  never a vulnerable version to disclose. A misconfiguration of *our* host (nginx, a stray credential)
  affects only us and needs no disclosure path at all.

**Length and scope:** issue docs are not subject to the ~100-line length ideal in the global markdown
rules — they are the deep source of truth, so length should match the material (measurements, rejected
alternatives, iteration history). What governs instead is **one shippable idea per doc**: the split
signal is not line count but whether the doc holds multiple *independent* ideas that would land as
separate PRs. When it does, extract each into its own doc and cross-link. In practice docs cluster
around 100–300 lines; treat ~500 as the prompt to check for an independent idea hiding inside. The
other global conventions still apply in full — cross-link rather than duplicate, and prefer a
well-linked narrative over bare index docs.
