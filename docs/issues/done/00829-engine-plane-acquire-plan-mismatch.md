# The router could pick a plan its dispatch arm cannot run

**Fixed in [#829](https://github.com/jbylund/sylvan_librarian/pull/829).** Found 2026-08-04 while
measuring the compose Gather arm; re-measured against `main` @ `b758901` on 2026-08-06.

Written under the `security-` prefix and kept out of the tree while unfixed. It is here now for the
reason [README.md](../README.md#unfixed-security-findings) gives — the write-up is a record rather
than a map once the fix has shipped — and because the impact turned out not to be a security one at
all. See "Severity, measured".

## The defect

`run_query_routed` is "argmin over `ALL.filter(applicable)`, then dispatch on `(plan, &prep)`".
`applicable` is a correctness predicate about the **query**: it says nothing about which artifact the
acquire step materialized. Those are different questions, and only `Prep::Range` answers both the
same way — its arm can run every plan, because the fast paths walk and a materializing winner
materializes lazily.

The other two acquires hold exactly one artifact and can run exactly the plans that read it. A
`Prep::Plane` acquire holds the plane bitmap: it can run that bitmap's own order walk
(`PlanePopcountOrder`) or either candidate-list executor over it, and nothing else. Its dispatch arm
forwarded everything that was not `PlanePopcountOrder` into `exec_from_candidates`, whose match ended
in `unreachable!`. Nothing constrained the argmin to the plans that arm could execute.

## Reachability

What had been keeping this from firing was a coincidence in somebody else's predicate: all three
printing-space fast paths required `plane.is_none()`, so a plane acquire's applicable set landed
inside its scope by accident. That guard is about how a predicate is *represented* — a bare border
under `unique=card` folds into a plane, so compose declined it — not about what a dispatch arm can
execute.

[#836](https://github.com/jbylund/sylvan_librarian/pull/836) lifted it, letting compose cost the
`unsplit` filter alongside a plane. The door came open. On the committed API fixture
(`api/tests/fixtures/engine_cards.json`, 16 cards / 90 printings), release build of `main`:

```
ok    q='f:pauper'          unique=card limit=10   total=4
PANIC q='f:pauper'          unique=card limit=200  PanicException: internal error: entered
                                                   unreachable code: exec_from_candidates
                                                   only runs P3/P4, got PrintingCompose
PANIC q='border:borderless' unique=card limit=200  … got PrintingCompose
```

**Corpus size is not the axis.** Slicing the 97,206-printing benchmark corpus, `border:borderless` at
`unique=card`, `limit=10_000`:

| printings | 90 | 500 | 2,000 | 5,000 | 10,000 | 20,000 | 40,000 | 97,206 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| out-of-scope winner | yes | yes | no | yes | yes | no | no | no |

**The production corpus was clean, by 12%.** Two sweeps over all 97,206 printings, comparing each
plane/candidates-acquired argmin against the set its arm can run:

- 49,248 configs — 239 sampled queries (both `QuerySampler` modes) × 3 uniques × 8 orderbys × 2
  directions × 3 limits × 2 offsets.
- 625 configs — 303 exhaustively enumerated plane-forming predicates (every border, rarity, format
  and frame value, plus 200 sets) × 3 uniques × 5 limits.

Zero out-of-scope winners in either. Closest margin **1.12×**, on `border:white` at `unique=card`
with a large limit: `StreamedSelect` picked, `PrintingCompose` 12% behind it. Then `f:oldschool` at
1.17× and `r:mythic` at 1.30×.

`limit` is the largest single lever and it is unbounded — `_validate_limit` rejects negatives and
non-integers but sets no ceiling, and the SSR search path in the `/` handler calls `_search` with no
limit at all, which becomes `1_000_000` at the engine call. So `GET /?q=border:white` was the shape
sitting closest to the flip.

Per [README.md](../README.md#findings-split-by-blast-radius) this is the kind that ships in the repo,
so it affected anyone deploying it — and the reachable corpus sizes above are exactly what a partial
import or a small self-hosted collection looks like.

## Severity, measured

The first draft of this doc, and #829's original description, both said the panic took the worker
process with it. **It did not.** Measured by raising a `BaseException` subclass out of a WSGI app
under `bjoern.run` in a child process:

```
before panic:       200 b'alive'
panicking request:  HTTPError 500
worker alive after: True exitcode: None
after panic:        200 b'alive'
```

bjoern prints the traceback and keeps serving. That also settles the follow-on worry:
`_all_workers_alive` in [api/entrypoint.py](../../../api/entrypoint.py) tears down *every* worker when
one dies, which would have been the blast radius — and it never fires.

`PanicException` derives from `BaseException`, so neither Falcon's error handling nor `_search`'s
engine-failed-fall-back-to-SQL wrapper caught it. No handler of ours ran. But the cost was **one
wrong 500 on a query the SQL path answers fine** — no capacity loss, so no DoS, and
[#782](https://github.com/jbylund/sylvan_librarian/pull/782) had already made the 500 body opaque, so
no disclosure. An availability bug for one request, not a security finding.

## What shipped

- **`PlanScope`** (`All` / `Candidates` / `Plane`), from `Prep::scope`, narrows the argmin to the
  plans the dispatch arm for that acquire has an executor for. Restricting the argmin rather than
  teaching the arms to run more plans is also the right performance answer: the acquire already paid
  for its artifact, and a plan outside the scope would throw that work away and redo it.
- **`CandidatePlan`** is the P3/P4 pair as a type, so `exec_from_candidates`'s match is exhaustive.
  The one remaining conversion falls back to `GatheredScan` under a `debug_assert` — a
  router/executor disagreement should run a correct plan, not panic.
- Replaces `PhysicalPlan::materializing()`, which grouped `PlanePopcountOrder` with the candidate pair
  and so could hand a candidate-list-only path the one plan that reads a bitmap.
- `explain` marks `picked` as the cheapest **in-scope** plan rather than index 0.
- `_search` catches `BaseException`, re-raising `KeyboardInterrupt`/`SystemExit`.

**It excludes plans; it does not reorder them.** Over 1,545 configs on the 97,206-printing corpus,
comparing isolated builds of the branch and of `main`: 0 differ. Same picked plan, same totals, same
row counts. Note what that implies about the change being a no-op *today* — the value is in the
constraint being stated where it belongs, not in a measurable win.

## Deliberately not included

#829 originally carried a cost-model correction too: `plan_cost`'s per-emitted-row terms read
`f.limit` raw, where `page_span` was already clamped to `matches` for exactly that reason. On the
fixture, `PlanePopcountOrder` at `limit=1_000_000` priced at 2,000,203 ns for a query with 4 results —
1M × the 2.0 ns emit term.

It is independently correct and it is **split out**, because it reorders: 226 of those same 1,545
configs pick a different plan, all `StreamedSelect`/`GatheredScan` → `PlanePopcountOrder` at limits of
100,000 and above. That wants wall-clock p50/p90/p99 behind it and a refit of `cost_terms`' EMIT
regressor against a rebuilt `benchmarks/verify-order/real.store`, which is stale. Branch:
`engine-cost-emit-clamp`.

## Still open

- **The 1.12× margin is undefended.** `plan_scope_admits_only_plans_its_dispatch_arm_can_run` pins the
  structural invariant, not the distance between the cheapest in-scope plan and the cheapest
  out-of-scope one. That margin moves with a corpus refresh as well as with code, and nothing watches
  it. Promoting the sweep to a bench would.
- **A `limit` cap is not a fix for this**, contrary to what an earlier draft suggested. The fixture
  panics at `limit=200`, and at 90 printings at `limit=175` — a front-end page size. Worth having as
  defence in depth on its own merits; it closes nothing here.
- Whether any other `unreachable!` in the dispatch path is reachable by the same argument — an
  invariant an executor assumes but no caller enforces.
