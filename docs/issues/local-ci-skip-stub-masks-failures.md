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
- The real job (`rust-test` / `python-test` / `js-test`) gains `needs: changes` and
  `if: needs.changes.outputs.relevant == 'true'`. The Python job was renamed from the bare `test` to
  match its siblings — safe to do here because nothing referenced the old name (see below).

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

## Nothing was actually required, and now it is

Worth recording, because it changes how to read the original bug: the `checks_on_main` ruleset carried
`deletion`, `non_fast_forward`, and `pull_request` (squash-only, 0 approvals) — and **no
`required_status_checks` rule at all**. So the stubs' whole reason for existing ("keep a required check
from hanging Pending") was not load-bearing in practice; nothing gated a merge on any check. The #760
masking was still real — it misled `gh pr checks` and any human reading it — but it could not have let
a red build through a gate, because there was no gate.

Requiring the checks is the point of the pattern, though, so `required_status_checks` is now added for
`rust-test`, `python-test`, and `js-test`, with the desired semantics being **success or not needed**.
That works precisely because of the mechanism above: a job skipped by `if:` produces a check run with
`status=completed, conclusion=skipped`, which rulesets accept — whereas a workflow skipped by `paths:`
produces *no check run at all*, which is why it hung Pending.

`changes` is deliberately **not** required: it is an implementation detail that always runs and always
succeeds, so requiring it would add a name to the rule without adding a guarantee.

### The job rename

The Python job was `test`; it is now `python-test`, matching `js-test`/`rust-test`. This was free to do
at this exact moment and would not have been later: with no `required_status_checks` rule yet, no
config referenced the old name, and nothing else in the repo did either (checked `ci-monitor.yml`,
`label-pr.yml`, `fix-lint.yml`, and the makefile). Renaming a check that a ruleset already requires
silently leaves the rule pointing at a name nothing reports, which is a check that can never pass.

## Verification

The three regexes were checked against 18 representative paths and reproduce the original `paths:`
classification exactly, including the overlaps (`api/static/fixtures/**` → js *and* unit;
`**/*.rs` → rust *and* unit; `changed-paths.sh` itself → all three, since a change to the detector
should run everything).

The script's four branches were exercised locally with a stubbed `gh`: match, no-match, mixed
relevant+irrelevant (the case that produced two runs before, now one), and both fail-open paths.

End-to-end, on real PRs:

| Case | PR | Result |
| --- | --- | --- |
| Files matching every pattern | #761 (workflows + script + doc) | one run per suite, all three real jobs `success` |
| Docs-only | #762 (throwaway probe) | one run per suite, all three real jobs `conclusion=skipped`, workflow `success` |

The docs-only case is the one #761 could not verify on its own — every file in it matches at least one
pattern, so nothing there exercises the skip path. #762 existed only to close that gap and was closed
afterward.

### Proving that `skipped` satisfies a required check

This is the assumption the whole design rests on, and getting it wrong has a specific bad outcome:
every docs-only PR becomes unmergeable, which is exactly the failure the stubs existed to prevent. So
it was tested rather than assumed, and *before* the change landed on `main`.

The obstacle: `checks_on_main` targets `~DEFAULT_BRANCH`, and #762's base was the feature branch, so
main's rule could not gate it — while a docs-only PR *into* main would still have been evaluated by the
old stub-based workflows, testing the wrong mechanism. Resolved with a throwaway second ruleset scoped
to `refs/heads/ci-single-workflow-change-detection` requiring the same three checks, which put #762
under a real requirement whose three checks were all `skipped`:

```
rust-test: skipped   python-test: skipped   js-test: skipped
→ mergeable=MERGEABLE  mergeStateStatus=CLEAN
```

So rulesets do treat `skipped` as satisfying a required status check. The temporary ruleset was deleted
afterward; `checks_on_main` is the only one remaining.

Had it come out the other way, the fallback is a single always-running job with the `if:` moved onto
each individual *step*: the job then always reports `success`, so the required check is always
satisfied while no work happens when it isn't needed. More verbose, but immune to the question.

## Related

- [local-engine-clippy-ci-gate.md](local-engine-clippy-ci-gate.md) — the clippy gate whose red build
  this masked, and where the finding was first recorded. That gate is also what makes the masking
  consequential: before it, `rust-tests.yml` only ran `cargo test`.
