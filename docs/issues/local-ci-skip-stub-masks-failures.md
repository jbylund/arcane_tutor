# CI: The Skip-Stub Workflow Pair Can Report a Passing Check While the Real Run Fails

Status: **implemented 2026-07-24** on `ci-single-workflow-change-detection`. Not filed as a GitHub
issue — found while landing [local-engine-clippy-ci-gate.md](local-engine-clippy-ci-gate.md) (#760),
where it actively hid a red build.

## The finding

Each test suite was two workflows: the real one gated on `paths:`, and a no-op "skip" stub gated on
the mirror-image `paths-ignore:`, **both declaring the same job name** so a required status check
would be satisfied either way. The stub exists because a workflow skipped by `paths:` leaves its
check *Pending* forever rather than passing — so without it, a docs-only PR would never be mergeable.

The two filters are not complements:

| Filter | Fires when |
| --- | --- |
| `paths:` | **any** changed file matches |
| `paths-ignore:` | **any** changed file does *not* match |

So a PR touching relevant *and* irrelevant files satisfies both, and **both workflows run**. On #760
(Rust changes plus a doc):

| Workflow | Steps | Result |
| --- | --- | --- |
| `rust-tests-skip.yml` | 3 | success |
| `rust-tests.yml` | 11 | **failure** |

Because they report the same check name, `gh pr checks 760` said **"4 passed, 0 failed"** while the
build was red. The failure was visible only by listing workflow runs directly and noticing that a
"passing" `rust-test` had finished in 3 seconds. If `rust-test` is a required status check, its state
with two same-named results is ambiguous — a 3-second no-op can stand in for the real suite.

This affected all three pairs identically (`rust-tests`, `unit-tests`, `js-tests`), and
`check-ci-sync.yml` could not catch it: that workflow only verified the two path lists *mirror* each
other, which they correctly did. Mirroring is precisely the broken assumption — it presumes the
filters are complements.

## The fix

One workflow per suite:

- **No `paths:` filter at all**, so the workflow always runs and its check is reported exactly once.
- A `changes` job that calls [`.github/scripts/changed-paths.sh`](../../.github/scripts/changed-paths.sh)
  and outputs a boolean.
- The real job (`rust-test` / `test` / `js-test`, names unchanged so existing required checks keep
  matching) gains `needs: changes` and `if: needs.changes.outputs.relevant == 'true'`.

The load-bearing detail: **a job skipped by `if:` reports a `skipped` conclusion, which branch
protection counts as satisfied** — unlike a *workflow* skipped by `paths:`, which stays Pending. That
is the behavior the stubs were faking, available directly, so the stubs are deleted.

`check-ci-sync.yml` is deleted too: there are no longer two path lists to keep in sync.

Net: **7 workflow files → 3**, plus one shared script.

### Why the API and not `git diff`

The script reads the PR's file list from `repos/:owner/:repo/pulls/:n/files` rather than diffing
locally. No `fetch-depth` games, no base/head range subtleties on force-pushed or merge-ref
checkouts, and it is the same list GitHub's own `paths:` filter consults — so the classification
matches what the old filters would have decided. Costs one `pull-requests: read` permission per
workflow.

### Failing open

Two cases deliberately run the full suite rather than skipping:

- **Not a `pull_request` event** (notably `push` to `main`) — there is no meaningful "changed files
  vs. a base" for gating.
- **An empty file list**, whether a genuinely empty PR or an API hiccup. For a *test gate*, the safe
  direction is to run too much, not too little.

## Verification

The three regexes were checked against 18 representative paths and reproduce the original `paths:`
classification exactly, including the overlaps (`api/static/fixtures/**` → js *and* unit;
`**/*.rs` → rust *and* unit; `changed-paths.sh` itself → all three, since a change to the detector
should run everything).

The script's four branches were exercised locally with a stubbed `gh`: match, no-match, mixed
relevant+irrelevant (the case that produced two runs before, now one), and both fail-open paths.

## Related

- [local-engine-clippy-ci-gate.md](local-engine-clippy-ci-gate.md) — the clippy gate whose red build
  this masked, and where the finding was first recorded. That gate is also what makes the masking
  consequential: before it, `rust-tests.yml` only ran `cargo test`.
