"""How well does `cost::plan_cost` agree with measured plan time, across the whole query space?

Not a mis-selection check — this asks whether each plan's cost ARM is right, per acquire branch, per
`unique`, per `orderby`. A plan can be picked correctly for a long time while its arm is badly wrong,
and only diverge once something competes closely with it.

Sampling comes from `query_sampler.QuerySampler` in `uniform` mode, which is deliberately unbiased
in the three ways this measurement needs. It used to come from private tables here that aimed at the
same properties and reached two of the three:

- `unique` is drawn evenly across card / printing / artwork, not 75/20/5. Distinct-on changes which
  plans are even applicable and what `scan_units` means, so under-sampling two thirds of it hides
  exactly the cells most likely to be wrong.
- `orderby` is drawn across every column the engine supports, since the permutation's existence is
  what gates `StreamedSelect` and `PlanePopcountOrder`.
- range thresholds sit at a uniformly-drawn QUANTILE of the real column. The private table sampled
  log-uniformly between hardcoded bounds (`usd` 0.05-400, `cn` 1-500), which spreads the *value* and
  not the *selectivity* — and selectivity is the axis a cost model varies along. The shared sampler
  also reaches `eur`/`tix` and two-sided bounds, neither of which the private table produced.

Reports measured/predicted per (plan, acquire branch), plus the two phases no cost term describes:
`prepare_candidates` — 21-33% of a range-acquired query against 7-10% of a plane-acquired one — and
the per-query scratch setup, which for `StreamedSelect` is an O(`n_cards`) counts zeroing that grows
with the corpus rather than with the answer. Both shares are keyed by acquire branch as well as plan,
because the range-vs-plane contrast is the whole point of the row, and both cover only the two
materializing plans: no other plan has those phases at all, which is not the same as spending 0%
of its run in them.

Then what the fastpaths that DECLINED cost. Those rounds produce no page, so they have no
measured/predicted ratio and appear in no other table -- but the work is real and no cost term
describes it, and one gate (`DeclineSparseExact`) turns back only after composing the printing
bitmap, paying a full compose it then discards. See `report_declines`.

Finally, whether `PrintingCompose`'s predicted paging branch is the one that actually ran, split by
acquire branch. Those two decisions are computed independently and nothing checked they agree until
`paging_taken` existed; `card_engine`'s `compose_paging_prediction_matches_the_branch_taken` asserts
it on a fuzz store, and this observes it over the real corpus, which reaches shapes the fuzz store
does not. The split matters because only a compose acquire predicts anything — see `report_paging`.

    .venv/bin/python scripts/bench_cost_model_agreement.py --seconds 120
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import pathlib
import random
import statistics
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.parsing import parse_scryfall_query  # noqa: E402
from scripts import costbench  # noqa: E402
from scripts.costbench import load_engine  # noqa: E402
from scripts.query_sampler import QuerySampler  # noqa: E402

NUM_WARMUPS = 2
NUM_TRIALS = 7
# Every distinct-on the engine supports, evenly weighted — see the module docstring.
# Every orderby `orderby_to_col` maps. Which ones have a sort permutation decides plan applicability.
LIMITS = (10, 100, 175)
OFFSETS = (0, 0, 0, 100)  # mostly first-page, which is what real traffic asks for
MIN_FOR_QUARTILES = 8
# The paging outcomes `ComposePaging` predicts. `Decline` joined the other three once acquire gained
# the estimated result total and could ask `compose_gather_declines` the same question the fastpath
# asks — before that, a decline was unpredictable by construction and excluded here.
#
# `NotComposable` and `EmptyPage` stay excluded: both are reached before any strategy runs and
# neither is something the prediction claims.
COMPOSE_STRATEGIES = ("Perm", "OrderbyWalk", "Gather", "Decline")
# The executor records WHY it refused; the model only predicts THAT it will. Fold the three reasons
# onto the single predicted label, or every correct decline scores as a disagreement.
DECLINE_REASONS = ("DeclineBroad", "DeclineSparseEstimate", "DeclineSparseExact")
# `GatherWalkDeclined` is a strategy outcome, so it IS counted — but it is not a disagreement. Acquire
# never composes, so it can only test that an orderby walk is AVAILABLE, never that it will succeed;
# the walk declines on the null-value tail or a page past the value structure and falls into the
# gather. `compose_paging` is therefore an upper bound on `OrderbyWalk` and exact everywhere else.
# Tracked separately because the rate is the interesting number: it is how often the engine pays a
# walk attempt AND a full gather while being priced as a walk.
WALK_DECLINED = "GatherWalkDeclined"
# The one decline gate that fires AFTER `printing_compose_fastpath` has composed the printing
# bitmap, off the real total rather than the estimator's bound -- so those rounds pay a full compose
# and discard it. The other three gate before the compose and are cheap. See `report_declines`.
POST_COMPOSE_GATE = "DeclineSparseExact"
# Gates that should be unreachable, so a non-zero count is a defect rather than a measurement.
# `RangeNotBare` contradicts `PrintingRangeScan.applicable`, which already requires bare range
# bounds; `NotComposable` contradicts `PrintingCompose.applicable`, which IS the same structural
# test the compose fastpath re-checks; `RangePermutationStale` means a sort permutation was built
# against a different store. Called out on their own line because in the decline table they would
# otherwise read as three more cheap rows -- their cost is not the point, their existence is.
IMPOSSIBLE_GATES = ("RangeNotBare", "NotComposable", "RangePermutationStale")
# The acquire branch that actually PREDICTS `compose_paging`. Every other branch leaves the
# `mk_plan_feats` default of `Gather` untouched — see `report_paging` for why that is a different
# defect rather than a prediction that missed.
COMPOSE_ACQUIRE = "printing_compose"
# Acquire branches where the routed path really does call `prepare_candidates` at dispatch, so the
# materializing plans genuinely owe its cost; everywhere else acquire built the prep already. Now
# owned by `costbench` alongside the netting rule that reads it — re-exported here because this
# module's own report still slices on it, and because other harnesses imported it from here.
RANGE_ACQUIRES = costbench.RANGE_ACQUIRES
# The only plans that call `prepare_candidates`, and so the only ones with a prepare phase at all.
# Every other plan reports `ns_prepare == 0` because it has no such phase — which is not the same
# as spending 0% of its run there, and pooling the two says the wrong thing.
MATERIALIZING_PLANS = ("StreamedSelect", "GatheredScan")
# The agreement bar this work is aiming at: every (plan, acquire) cell's median inside it.
AGREE_LO, AGREE_HI = 0.8, 1.25

# Predicate templates, one per engine path, so no single family dominates the sample the way a
# weighted-dimension generator does. A query is one or two of these joined.
# Range fields with a real index, and the value ranges to sample thresholds from.


def main() -> None:
    """Sample until the budget runs out, then report agreement per plan and acquire branch."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--corpus", type=pathlib.Path, default=REPO_ROOT / "benchmarks/bitplanes/corpus.jsonl")
    parser.add_argument("--shm-path", type=pathlib.Path, default=None)
    args = parser.parse_args()

    engine = load_engine(args.corpus, args.shm_path or args.corpus.with_suffix(".agreement.store"))
    sampler = QuerySampler(args.corpus, "uniform")
    rng = random.Random(args.seed)

    agr = Agreement()

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        q, unique = sampler.query(rng), sampler.unique(rng)
        kw = {
            "filters": None,
            "unique": unique,
            "orderby": sampler.orderby(rng),
            "direction": rng.choice(("asc", "desc")),
            "limit": rng.choice(LIMITS),
            "offset": rng.choice(OFFSETS),
        }
        try:
            kw["filters"] = parse_scryfall_query(q)
            # `explain_analyze` runs `explain` internally and returns the same `acquire` dict, so a
            # separate `engine.explain(**kw)` call here would be a second full acquire per sample for
            # fields we already have. This loop is budgeted in wall-clock seconds, so dropping it buys
            # samples directly. (`acquire_ns` is the one field that differs -- re-sampled per round
            # rather than once -- and nothing below reads it.)
            res = engine.explain_analyze(prefer="default", num_warmups=NUM_WARMUPS, num_trials=NUM_TRIALS, **kw)
            acq = res["acquire"]
        except Exception as exc:  # noqa: BLE001 - a rejected query is a skipped sample
            # Counted BY TYPE, not just totalled. This harness's whole argument is that sampling bias
            # hides the cells most likely to be wrong -- and a bare skip counter is that same bias: if
            # explain_analyze started raising for every artwork query, the table below would look
            # healthy over two thirds of the intended space with nothing to say so.
            agr.skipped += 1
            agr.skip_reasons[type(exc).__name__] += 1
            continue
        agr.sampled += 1
        for p in res["plans"]:
            # A plan that entered a fastpath and turned back produces no page, so it has no
            # measured/predicted ratio and falls out of every table below. It is not free, though,
            # and until `declined_ns` existed it was invisible from here entirely: `explain_analyze`
            # dropped the stats of any plan whose round returned nothing, so the four decline gates
            # were recorded in Rust and discarded before this loop could count them. That made the
            # compose table below a census of successful composes described as a census of composes.
            if p["declined_ns"]:
                agr.declines[p["plan"], p["paging_taken"]].append(min(p["declined_ns"]))
                continue
            predicted = costbench.predicted_ns(p)
            if not p["trials_ns"] or predicted is None:
                continue
            measured = min(p["trials_ns"])
            agr.ratios[p["plan"], acq["count_source"]].append(measured / predicted)
            agr.by_unique[p["plan"], unique].append(measured / predicted)
            # Keyed by acquire branch as well as plan: the whole point of this row is that the
            # prepare share differs by HOW the query was acquired, so collapsing to the plan alone
            # averages a range-acquired query together with a plane-acquired one and reports a
            # number that describes neither. Restricted to the plans that HAVE a prepare phase --
            # `ns_round_total` alone admits every plan that ran, and the uninstrumented ones would
            # then contribute a run of 0.0 that reads as "spends no time preparing".
            #
            # The setup phase rides the same guard: also instrumented for both materializing plans,
            # and for `StreamedSelect` it is an O(n_cards) counts zeroing that no cost term carries.
            if p["ns_round_total"] and p["plan"] in MATERIALIZING_PLANS:
                agr.prep_frac[p["plan"], acq["count_source"]].append(p["ns_prepare"] / p["ns_round_total"])
                agr.setup_frac[p["plan"], acq["count_source"]].append(p["ns_setup"] / p["ns_round_total"])
            # Did the compose fastpath take the branch the cost model predicted? The two decisions
            # are computed independently, and nothing checked they agree until now -- the same shape
            # as the Python cost mirror that drifted from cost.rs for two revisions.
            # `card_engine`'s `compose_paging_prediction_matches_the_branch_taken` asserts this on a
            # fuzz store; here it is observed over the real corpus, which reaches far more shapes.
            #
            # Keyed by acquire branch, because `compose_paging` is only a PREDICTION under a compose
            # acquire. Under any other branch it is the untouched `mk_plan_feats` default, so an
            # off-diagonal cell there means a competing compose was mispriced, not that a prediction
            # drifted -- different defect, different fix, and pooling them reports neither.
            if p["plan"] == "PrintingCompose":
                taken = "Decline" if p["paging_taken"] in DECLINE_REASONS else p["paging_taken"]
                if taken in (*COMPOSE_STRATEGIES, WALK_DECLINED):
                    agr.paging[acq["count_source"], acq["compose_paging"], taken] += 1

    report(agr, args.seconds)


def report_suppressed(groups: dict[tuple[str, str], list[float]]) -> None:
    """The cells too thin to summarise, named and sized.

    This harness's whole argument is that under-sampling hides the cells most likely to be wrong, so
    dropping them silently is that same bias applied to the report itself -- the reason `skip_reasons`
    counts exceptions by type rather than totalling them. Both numbers are needed: how many samples a
    suppressed cell already has says whether more budget would surface it (n=6 of 8) or whether the
    shape is genuinely rare in the sample space (n=1). Named, because a cell that is thin every run is
    itself the finding.
    """
    thin = {f"{plan}/{key}": len(rs) for (plan, key), rs in groups.items() if len(rs) < MIN_FOR_QUARTILES}
    if not thin:
        return
    listed = ", ".join(f"{name} n={n}" for name, n in sorted(thin.items(), key=lambda kv: (-kv[1], kv[0])))
    print(f"  {len(thin)} cells suppressed (n < {MIN_FOR_QUARTILES}), {sum(thin.values())} samples: {listed}")


def summarise(label: str, groups: dict[tuple[str, str], list[float]], col: str) -> None:
    """One table: median measured/predicted per cell, and how many cells clear the agreement bar."""
    cells: list[tuple[str, float]] = []
    print(f"\n{'plan':<20}{col:<22}{'n':>5}{'median':>9}{'p10':>8}{'p90':>8}{'within 25%':>12}")
    for (plan, key), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        if len(rs) < MIN_FOR_QUARTILES:
            continue
        ds = statistics.quantiles(rs, n=10)
        near = sum(1 for r in rs if AGREE_LO <= r <= AGREE_HI) / len(rs)
        med = statistics.median(rs)
        verdict = "" if AGREE_LO <= med <= AGREE_HI else "  FAIL"
        print(f"{plan:<20}{key:<22}{len(rs):>5}{med:>9.2f}{ds[0]:>8.2f}{ds[8]:>8.2f}{near:>11.0%}{verdict}")
        cells.append((f"{plan}/{key}", med))
    passing = sum(1 for _, m in cells if AGREE_LO <= m <= AGREE_HI)
    print(f"  {label}")
    # "of the cells reported", not "of the cells that exist" -- the suppressed line below carries the
    # rest, and without it this ratio reads as coverage when it is nothing of the kind.
    print(f"  {passing}/{len(cells)} reported cells inside [{AGREE_LO}, {AGREE_HI}]")
    report_suppressed(groups)


@dataclasses.dataclass
class Agreement:
    """Everything the sweep accumulates, keyed for the three tables the report prints."""

    ratios: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    by_unique: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    prep_frac: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    setup_frac: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    # (acquire branch, predicted ComposePaging, paging_taken) -> count. The acquire branch is what
    # says whether an off-diagonal cell is drift in a real prediction or a default that was never
    # one; see `report_paging`.
    paging: dict[tuple[str, str, str], int] = dataclasses.field(default_factory=lambda: collections.defaultdict(int))
    # (plan, gate) -> the ns each declining round cost. Declines produce no page and so appear in no
    # other table; see `report_declines` for why the cost is worth its own row.
    declines: dict[tuple[str, str], list[float]] = dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    sampled: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = dataclasses.field(default_factory=lambda: collections.defaultdict(int))


def report_paging(agr: Agreement) -> None:
    """Predicted compose paging branch against the branch that ran, split by acquire branch.

    Split because the two halves are different defects with different fixes.

    Under a `printing_compose` acquire the prediction is genuinely computed: `acquire_plan_features`
    re-derives the permutation lookup and the orderby-walk test that `printing_compose_fastpath` then
    decides again at run time. An off-diagonal cell is drift between two independent implementations
    of one decision, and the fix is to share it.

    With one cell excepted, and it is excepted on structure rather than tolerance. The two
    *availability* tests are identical by construction and cannot disagree; what acquire cannot see is
    whether the walk it predicts will SUCCEED, since knowing that means composing, which acquire
    deliberately never does. A walk that declines falls into the gather and reports
    `GatherWalkDeclined`, so `compose_paging` is an upper bound on `OrderbyWalk` and exact elsewhere.
    That cell gets its own line rather than being pooled either way — it is not drift, but it is not
    free either: those runs pay the walk attempt and the gather while priced as a walk.

    Under any other acquire nothing predicted anything — `compose_paging` is the `mk_plan_feats`
    default of `Gather`, which `cost.rs` nonetheless prices a COMPETING `PrintingCompose` with. An
    off-diagonal cell there means the competitor was costed with an arm it never runs, and the fix is
    to give that branch a result total. Pooling the two averages a cell that is wrong by
    construction into one that is mostly right, and describes neither.
    """
    if not agr.paging:
        print("\nno PrintingCompose run reached a paging strategy; nothing to check")
        return
    for acquire in sorted({branch for branch, _, _ in agr.paging}):
        cells = {(pred, took): n for (branch, pred, took), n in agr.paging.items() if branch == acquire}
        total = sum(cells.values())
        is_prediction = acquire == COMPOSE_ACQUIRE
        subject = "predicted vs taken" if is_prediction else "default Gather vs taken (NO prediction made here)"
        print(f"\ncompose paging under {acquire} acquire: {subject}, {total:,} runs")
        for strategy in COMPOSE_STRATEGIES:
            print(f"  {strategy:<14}{cells.get((strategy, strategy), 0):>7,} agreed")
        # Structural, not drift: a predicted walk that declined into the gather. Reported on its own
        # line with a rate, because that rate is the size of a real (small) under-costing — those runs
        # pay the walk attempt AND the gather while priced as a walk — and folding it into either the
        # agreed count or the disagreement count would hide it.
        declined = cells.pop(("OrderbyWalk", WALK_DECLINED), 0)
        if declined:
            print(f"  {'walk declined':<14}{declined:>7,} ({declined / total:.1%}) predicted OrderbyWalk, fell back to gather")
        wrong = {key: n for key, n in cells.items() if key[0] != key[1]}
        if not wrong:
            print("  0 disagreements.")
            continue
        verdict = (
            "two implementations of one decision have drifted"
            if is_prediction
            else "a competing compose was priced with an arm it never runs"
        )
        print(f"  {sum(wrong.values()):,} ({sum(wrong.values()) / total:.0%}) DISAGREEMENTS -- {verdict}:")
        for (pred, took), n in sorted(wrong.items(), key=lambda kv: -kv[1]):
            print(f"    predicted {pred:<12} took {took:<12}{n:>7,}")


def report_declines(agr: Agreement) -> None:
    """What the fastpaths that turned back cost, per gate.

    A decline produces no page, so it has no measured/predicted ratio and appears in no other table.
    That is exactly why it needs one: the work is real, it is charged to the query, and no cost term
    describes it. The router picked a plan, the plan bailed, and the general path then answered the
    query from scratch -- so a declining round is pure overhead on top of whatever ran next.

    Both printing-space fastpaths name their gate. The gates do not cost the same thing, which is
    the number this table exists to separate:

    `PrintingCompose` --
    - `DeclineBroad` and `DeclineSparseEstimate` gate BEFORE the compose. Cheap; the fastpath looked
      at an estimate and turned back.
    - `DeclineSparseExact` gates AFTER, off the real total, so it has already composed the printing
      bitmap and throws it away. That is a full compose paid for nothing.
    - `NotComposable` should never appear at all: `PrintingCompose.applicable` IS the structural
      test the fastpath re-checks, so reaching it means the two have drifted. Its existence is the
      finding, not the timing next to it -- see `IMPOSSIBLE_GATES`.

    `PrintingRangeScan` -- all four of its real gates are cheap (two binary searches and a lookup),
    so the interesting number here is the COUNT, not the median:
    - `RangeSelective` is the expected one: the range is narrow enough that the ordinary narrowing
      wins. A large count is fine.
    - `RangeNoPermutation` and `RangeUnalignedPrice` are orderby/predicate mismatches. A large count
      means the router keeps ranking a plan that structurally cannot serve those queries, which is a
      costing question rather than a fastpath one.
    - `RangeSparse` needs 1000 < k <= 1024 and so is near-unreachable outside a tiny index.
    - `RangeNotBare` and `RangePermutationStale` should never appear at all -- the first contradicts
      `PrintingRangeScan.applicable`, the second means an index built against a different store. A
      non-zero count in either row is the finding, not the timing next to it. Same class as compose's
      `NotComposable` above; all three are in `IMPOSSIBLE_GATES`.

    A high `DeclineSparseExact` median against the same query's `PrintingCompose` predicted cost is
    the actionable case: it means the pre-compose sparse estimate is not catching what the exact
    total then rejects, and tightening the estimator would recover the whole of it.
    """
    if not agr.declines:
        print("\nno plan declined; every fastpath that was tried produced a page")
        return
    total = sum(len(v) for v in agr.declines.values())
    print(f"\nfastpath declines: {total:,} rounds that entered, turned back, and produced nothing")
    print(f"{'plan':<20}{'gate':<24}{'n':>6}{'median µs':>12}{'p90 µs':>10}")
    for (plan, gate), costs in sorted(agr.declines.items(), key=lambda kv: -len(kv[1])):
        p90 = statistics.quantiles(costs, n=10)[8] if len(costs) >= MIN_FOR_QUARTILES else max(costs)
        print(f"{plan:<20}{gate:<24}{len(costs):>6}{statistics.median(costs) / 1000:>12.1f}{p90 / 1000:>10.1f}")
    post_compose = sum(len(v) for (_, gate), v in agr.declines.items() if gate == POST_COMPOSE_GATE)
    if post_compose:
        print(f"  {post_compose:,} of these ({post_compose / total:.0%}) are {POST_COMPOSE_GATE} -- composed, then discarded.")
    impossible = {gate: len(v) for (_, gate), v in agr.declines.items() if gate in IMPOSSIBLE_GATES}
    if impossible:
        named = ", ".join(f"{gate} x{n:,}" for gate, n in sorted(impossible.items()))
        print(f"  DEFECT: {named} -- see IMPOSSIBLE_GATES; these gates are supposed to be unreachable.")
    print("  Declines appear in no other table: no page, so no measured/predicted ratio.")


def report_phase_share(groups: dict[tuple[str, str], list[float]], caption: str) -> None:
    """One phase's share of the plan's whole run, per (plan, acquire branch)."""
    print(f"\n{'plan':<20}{'acquire':<22}{'n':>6}{'median share':>20}{'p90':>8}")
    for (plan, acquire), fracs in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        if len(fracs) < MIN_FOR_QUARTILES:
            continue
        p90 = statistics.quantiles(fracs, n=10)[8]
        print(f"{plan:<20}{acquire:<22}{len(fracs):>6}{statistics.median(fracs):>19.0%}{p90:>8.0%}")
    print(f"  {caption}")
    report_suppressed(groups)


def report(agr: Agreement, seconds: float) -> None:
    """Agreement per acquire branch and per distinct-on, then the two unpriced phase shares."""
    print(f"\n{agr.sampled:,} queries sampled ({agr.skipped:,} skipped) in {seconds:.0f}s")
    if agr.skip_reasons:
        breakdown = ", ".join(f"{name} x{n:,}" for name, n in sorted(agr.skip_reasons.items(), key=lambda kv: -kv[1]))
        print(f"  skipped by reason: {breakdown}")
    summarise("measured/predicted by acquire branch. 1.00 is agreement; >1 under-costed.", agr.ratios, "acquire")
    summarise("the same, split by distinct-on rather than acquire.", agr.by_unique, "unique")

    report_phase_share(agr.prep_frac, "prepare_candidates as a share of the plan's run — the term no cost arm carries.")
    report_phase_share(agr.setup_frac, "per-query scratch setup — StreamedSelect's is an O(n_cards) counts zeroing, so it")
    print("  scales with the corpus rather than with the answer, and no cost arm carries it either.")
    print("  Both tables cover only the two materializing plans; no other plan has these phases.")
    report_paging(agr)
    report_declines(agr)


if __name__ == "__main__":
    main()
