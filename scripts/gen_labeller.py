#!/usr/bin/env python3
"""Generate a self-contained artwork-preference labelling page.

Emits a single HTML file with the queue embedded, so it needs no server and no CORS
workaround — open it directly. Verdicts accumulate in localStorage as you go and are
exported as JSONL with one button.

Background: docs/issues/local-prefer-score-label-harness.md.

Shows every distinct artwork for one card at once; you click the best, or "no
preference". A pick-best-of-N is the argmax in a single click AND decomposes into
N-1 pairwise observations for fitting (chosen beats each other shown), so it yields
strictly more data per click than asking one pair at a time.

Three design rules:

  * **Show only the artwork**, via Scryfall's own `art_crop`. The judgment is about
    art, so frame, title and text box are noise. Cropping a full card scan ourselves
    needs per-frame geometry and got it wrong; the publisher's crop is exact.
  * **Never display a scored feature** (reprint count, first-appearance date,
    prefer_score). Those are the model's inputs; showing them would make the label
    partly a function of them, and any later fit would learn a rule inferred from the
    same numbers it is fitting. Emitted labels carry scryfall_ids, so analysis
    rejoins against magic.cards for whatever it needs.
  * **Randomise tile order** per card, so position bias cannot look like preference.

Usage:
    python scripts/gen_labeller.py --limit 150 -o /tmp/labeller.html
    python scripts/gen_labeller.py --limit 150 --seed 2 \
        --exclude ~/Downloads/prefer-score-labels.jsonl -o /tmp/batch2.html
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import subprocess
import sys
from pathlib import Path

DB_CONTAINER, DB_USER, DB_NAME = "sylvan_blue-postgres-1", "foouser", "magic"

# Artwork images come from Scryfall's own `art_crop` (stored per printing in
# raw_card_blob.image_uris), NOT from this project's card CDN. Cropping a full card
# scan ourselves needs the art window's geometry, which differs by frame version --
# a single fractional box cut roughly the bottom third off 2015-frame art while
# fitting older frames. art_crop is the publisher's crop, correct for every frame,
# and populated for 100% of English printings (94,718/94,718). Typical size 626x457.
#
# This is the one place the tooling hits cards.scryfall.io rather than the project
# CDN. Acceptable for a dev labelling tool loading a few hundred images; do not point
# production at it.
ART_CROP_JSON = "raw_card_blob -> 'image_uris' ->> 'art_crop'"

# Basic lands have hundreds of artworks and no meaningful "best" one.
EXCLUDED_CARDS = ("Mountain", "Island", "Plains", "Forest", "Swamp", "Wastes")

# Sets whose printings are a *treatment* of an existing artwork rather than new art.
# Scryfall assigns them their own illustration_id, so they surface as a duplicate
# "artwork". `dbl` (Innistrad: Double Feature) reprints 530 cards in black and white.
# prefer_score already singles this set out -- its `artwork_set` component scores dbl 0
# against everything else's 20 -- so excluding it here is consistent with that.
EXCLUDED_SETS = ("dbl",)

# Perceptual backstop for treatment variants that are not covered by a set or promo
# flag. Distances measured with the fingerprint below: black-and-white vs colour of one
# artwork 6.9, a promo recolour 19.3, genuinely different artworks 34.1. A threshold of
# 15 removes grayscale and near-identical treatments while leaving real pairs alone --
# of 183 previously labelled pairs only 3 fell below 16.
DEDUP_THRESHOLD = 15.0
# Decode size for the comparison. Small on purpose: the blur IS the comparison, and it
# keeps a 375-artwork batch to a few seconds rather than minutes.
DEDUP_DECODE = 64
DEDUP_GRID = 16

# Candidate "off style" art tags, from the community tagger vocabulary reachable via
# `art:`. These are curated descriptions OF THE PICTURE, which is why they are preferred
# over an era/year term: a term on year permanently penalises all future art, whereas a
# style tag generalises -- a new anime-styled card gets tagged and scores low, a new
# card in the core style does not.
#
# Measured on 155 labelled artwork pairs: where exactly one side carried one of these,
# the UNTAGGED artwork was chosen 19 out of 19 times (p ~ 4e-6). Strong, but 19
# observations cannot pin the magnitude, support per tag is 2-6, and `anime` never
# appeared at all -- hence this stratified sampler.
#
# This list is a hypothesis, not a conclusion. Analysis should scan all 10,807 art tags
# for predictive ones rather than trusting the selection here.
STYLE_TAGS = (
    "anime",
    "comic-style",
    "line-art",
    "rulebook-style",
    "word-art-title",
    "marvel-universe",
    "fallout-universe",
    "warhammer-universe",
)

# Cards with more distinct artworks than this are skipped: past roughly a dozen
# tiles the choice stops being a judgment and starts being a search.
MAX_ARTWORKS = 12
# Cards with fewer than this have no choice to make.
MIN_ARTWORKS = 2

STYLE_ELIGIBLE_SQL = """
WITH rep AS (
    SELECT DISTINCT ON (card_name, illustration_id) card_name, illustration_id,
           (SELECT string_agg(t, ',') FROM jsonb_object_keys(COALESCE(card_art_tags, '{{}}'::jsonb)) t
            WHERE t IN {tags}) AS style_tags
    FROM magic.cards
    WHERE raw_card_blob ->> 'lang' = 'en'
      AND illustration_id IS NOT NULL
      AND card_name NOT IN {excluded}
      AND card_set_code NOT IN {excluded_sets}
      AND NOT COALESCE((raw_card_blob ->> 'promo')::bool, false)
      AND raw_card_blob -> 'image_uris' ->> 'art_crop' IS NOT NULL
      {exclude_cards}
    ORDER BY card_name, illustration_id, prefer_score DESC NULLS LAST
)
SELECT card_name, string_agg(DISTINCT style_tags, ',') AS tags
FROM rep GROUP BY card_name
-- must contain BOTH a tagged and an untagged artwork, or the pair teaches nothing
HAVING bool_or(style_tags IS NOT NULL) AND bool_or(style_tags IS NULL)
   AND count(*) BETWEEN {min_art} AND {max_art}
"""

QUERY = """
WITH src AS (
    SELECT card_name, scryfall_id::text AS sid, illustration_id::text AS art,
           raw_card_blob -> 'image_uris' ->> 'art_crop' AS art_url, prefer_score
    FROM magic.cards
    WHERE raw_card_blob ->> 'lang' = 'en'
      AND card_name NOT IN {excluded}
      AND illustration_id IS NOT NULL
      AND raw_card_blob -> 'image_uris' ->> 'art_crop' IS NOT NULL
      AND card_set_code NOT IN {excluded_sets}
      -- Promos are excluded because Scryfall gives a promo whose art is the base art
      -- with a recolour/effect treatment its own illustration_id, so it would appear
      -- as a separate "artwork" that reads as a duplicate. Observed with Arashin War
      -- Beast: frf/123 vs ugin/123. Costs ~12% of the pool.
      AND NOT COALESCE((raw_card_blob ->> 'promo')::bool, false)
      {exclude_cards}
),
rep AS (   -- one representative printing per distinct artwork
    SELECT DISTINCT ON (card_name, art) *
    FROM src
    ORDER BY card_name, art, prefer_score DESC NULLS LAST, sid
),
counted AS (
    SELECT card_name, count(*) AS n_art FROM rep GROUP BY card_name
    HAVING count(*) BETWEEN {min_art} AND {max_art}
),
picked AS (   -- seeded card selection; the seed must reach the SQL or every batch
              -- returns the same alphabetically-first cards
    SELECT card_name FROM counted
    {restrict}
    ORDER BY md5(card_name || '{seed}')
    LIMIT {limit}
)
SELECT r.card_name, r.sid, r.art, r.art_url
FROM rep r JOIN picked p USING (card_name)
ORDER BY r.card_name, r.art
"""


def run_query(sql: str) -> list[dict[str, str]]:
    """Run SQL in the postgres container; parse CSV (card names contain commas).

    ON_ERROR_STOP is essential: psql exits 0 on SQL errors by default, which makes
    the returncode meaningless and turns a broken query into silent empty results.
    """
    proc = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "--csv", "-f", "-"],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed:\n{proc.stderr}")
    rows = list(csv.DictReader(io.StringIO(proc.stdout)))
    if not rows:
        sys.exit("query returned no rows")
    return rows


def sql_list(values: tuple[str, ...]) -> str:
    """SQL IN-list. Python's repr of a 1-tuple is ('x',), which is a syntax error."""
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def already_labelled(paths: list[Path]) -> set[str]:
    seen: set[str] = set()
    for p in paths:
        for line in p.read_text().splitlines():
            if line.strip():
                seen.add(json.loads(line)["card"])
    return seen


def pick_style_cards(limit: int, seed: int, clause: str) -> list[str]:
    """Cards having both a style-tagged and an untagged artwork, stratified by tag.

    Stratified rather than random so no candidate tag is left untested: `anime` has 577
    tagged artworks yet appeared in zero labelled pairs under random sampling.
    """
    rows = run_query(
        STYLE_ELIGIBLE_SQL.format(
            tags=sql_list(STYLE_TAGS),
            excluded=sql_list(EXCLUDED_CARDS),
            excluded_sets=sql_list(EXCLUDED_SETS),
            exclude_cards=clause,
            min_art=MIN_ARTWORKS,
            max_art=MAX_ARTWORKS,
        )
    )
    by_tag: dict[str, list[str]] = {t: [] for t in STYLE_TAGS}
    for r in rows:
        for t in set((r["tags"] or "").split(",")):
            if t in by_tag:
                by_tag[t].append(r["card_name"])
    rng = random.Random(seed)
    for cards in by_tag.values():
        rng.shuffle(cards)
    # Round-robin across tags so the scarcest tag still gets its share.
    chosen: list[str] = []
    seen: set[str] = set()
    i = 0
    while len(chosen) < limit and any(len(v) > i for v in by_tag.values()):
        for t in STYLE_TAGS:
            if len(chosen) >= limit:
                break
            if len(by_tag[t]) > i and by_tag[t][i] not in seen:
                seen.add(by_tag[t][i])
                chosen.append(by_tag[t][i])
        i += 1
    print("  stratified by tag: " + ", ".join(f"{t}={sum(1 for c in chosen if c in set(by_tag[t]))}" for t in STYLE_TAGS))
    return chosen


def build_cards(limit: int, seed: int, exclude: set[str], mode: str) -> list[dict[str, object]]:
    clause = ""
    if exclude:
        quoted = ",".join("'" + c.replace("'", "''") + "'" for c in sorted(exclude))
        clause = f"AND card_name NOT IN ({quoted})"
    restrict = ""
    if mode == "style":
        names = pick_style_cards(limit, seed, clause)
        if not names:
            sys.exit("no eligible style-tag cards")
        restrict = f"WHERE card_name IN ({','.join(chr(39) + n.replace(chr(39), chr(39) * 2) + chr(39) for n in names)})"
    rows = run_query(
        QUERY.format(
            excluded=sql_list(EXCLUDED_CARDS),
            excluded_sets=sql_list(EXCLUDED_SETS),
            exclude_cards=clause,
            min_art=MIN_ARTWORKS,
            max_art=MAX_ARTWORKS,
            seed=seed,
            limit=limit,
            restrict=restrict,
        )
    )

    grouped: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        grouped.setdefault(r["card_name"], []).append(
            {
                "sid": r["sid"],
                "art": r["art"],
                "img": r["art_url"],
            }
        )
    rng = random.Random(seed)
    cards = []
    for name, tiles in grouped.items():
        rng.shuffle(tiles)  # position bias must not read as preference
        cards.append({"card": name, "tiles": tiles})
    rng.shuffle(cards)
    return cards


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artwork preference labeller</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.4 system-ui, sans-serif; display:flex; flex-direction:column;
         min-height:100vh; }
  header { display:flex; align-items:baseline; gap:1rem; padding:.6rem 1rem;
           border-bottom:1px solid color-mix(in srgb, currentColor 20%, transparent); }
  h1 { font-size:1rem; margin:0; font-weight:600; }
  #card { font-weight:600; }
  #progress { margin-left:auto; opacity:.7; font-variant-numeric:tabular-nums; }
  main { flex:1; padding:1rem; display:grid; gap:.75rem; align-content:start;
         grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); }
  .tile { border:3px solid transparent; border-radius:10px; padding:0; background:none;
          cursor:pointer; overflow:hidden; position:relative; }
  .tile:hover, .tile:focus-visible { border-color:#4f8cff; outline:none; }
  .crop { position:relative; border-radius:6px; overflow:hidden; }
  .crop img { width:100%; display:block; }
  .num { position:absolute; left:.35rem; top:.35rem; background:#000a; color:#fff;
         font:12px/1 monospace; padding:.2rem .35rem; border-radius:4px; }
  footer { display:flex; gap:.75rem; align-items:center; justify-content:center; padding:.75rem;
           border-top:1px solid color-mix(in srgb, currentColor 20%, transparent); flex-wrap:wrap; }
  button.act { font:inherit; padding:.5rem .9rem; border-radius:8px; cursor:pointer; background:none;
               border:1px solid color-mix(in srgb, currentColor 35%, transparent); }
  .key { font:12px monospace; opacity:.6; }
  #done { display:none; padding:2rem; text-align:center; }
</style></head><body>
<header>
  <h1>Pick the best artwork</h1><span id="card"></span><span id="progress"></span>
</header>
<main id="stage"></main>
<div id="done"><p><strong>Queue finished.</strong> Download your labels below.</p></div>
<footer>
  <button class="act" id="otherBtn">No preference <span class="key">(space)</span></button>
  <button class="act" id="undoBtn">Undo <span class="key">(u)</span></button>
  <button class="act" id="dlBtn">Download labels (JSONL)</button>
  <button class="act" id="resetBtn">Reset</button>
</footer>
<script>
const ALL_CARDS = __CARDS__;
const KEY = 'prefer-score-labels-v2';
let labels = JSON.parse(localStorage.getItem(KEY) || '[]');
const $ = id => document.getElementById(id);

// Resume by CARD, not by count. The queue gets regenerated (new seed, new exclusions,
// bug fixes), and a count-based resume would then point into a reshuffled queue --
// skipping cards never seen and re-showing ones already done. Every label record is
// self-describing (card name + chosen/shown sids), so filtering the queue against
// what is already labelled makes restarts and regenerations safe.
const done = new Set(labels.map(l => l.card));
const CARDS = ALL_CARDS.filter(c => !done.has(c.card));
const CARRIED = labels.length;
const at = () => labels.length - CARRIED;

function preload(i) {
  (CARDS[i]?.tiles || []).forEach(t => { const im = new Image(); im.src = t.img; });
}

function render() {
  const i = at();
  $('progress').textContent = CARRIED
      ? `${i} / ${CARDS.length} left  (${CARRIED} already done)`
      : `${i} / ${CARDS.length}`;
  if (i >= CARDS.length) { $('stage').style.display='none'; $('done').style.display='block'; $('card').textContent=''; return; }
  $('stage').style.display='grid'; $('done').style.display='none';
  const c = CARDS[i];
  $('card').textContent = c.card;
  $('stage').innerHTML = c.tiles.map((t, k) => `
    <button class="tile" data-k="${k}">
      <div class="crop"><img src="${t.img}" alt="artwork ${k+1}"><span class="num">${k+1}</span></div>
    </button>`).join('');
  for (const b of $('stage').querySelectorAll('.tile')) b.onclick = () => record(+b.dataset.k);
  preload(i + 1);
}

function record(k) {
  const i = at();
  if (i >= CARDS.length) return;
  const c = CARDS[i];
  labels.push({
    card: c.card,
    mode: 'artworks',
    verdict: k === null ? 'other' : 'pick',
    chosen_sid: k === null ? null : c.tiles[k].sid,
    chosen_art: k === null ? null : c.tiles[k].art,
    shown: c.tiles.map(t => ({sid: t.sid, art: t.art})),
    labeled_at: new Date().toISOString(),
  });
  localStorage.setItem(KEY, JSON.stringify(labels));
  render();
}

$('otherBtn').onclick = () => record(null);
$('undoBtn').onclick = () => {
  if (labels.length <= CARRIED) return;   // never undo into a previous session's labels
  labels.pop(); localStorage.setItem(KEY, JSON.stringify(labels)); render();
};
$('resetBtn').onclick = () => { if (confirm('Discard all labels in this browser?')) { labels=[]; localStorage.removeItem(KEY); render(); } };
$('dlBtn').onclick = () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([labels.map(l=>JSON.stringify(l)).join('\\n')+'\\n'], {type:'application/x-ndjson'}));
  a.download = 'prefer-score-labels.jsonl'; a.click();
};
addEventListener('keydown', e => {
  const c = CARDS[at()]; if (!c) return;
  if (e.key === ' ') { e.preventDefault(); record(null); }
  else if (e.key === 'u') $('undoBtn').click();
  else if (/^[1-9]$/.test(e.key) && +e.key <= c.tiles.length) record(+e.key - 1);
  else if (e.key === 'ArrowLeft') record(0);
  else if (e.key === 'ArrowRight') record(c.tiles.length - 1);
});
render();
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--mode",
        choices=("artworks", "style"),
        default="artworks",
        help="artworks: any card with 2+ distinct artworks. "
        "style: only cards having BOTH a style-tagged and an untagged artwork, "
        "stratified across candidate tags so none stays untested.",
    )
    ap.add_argument("--limit", type=int, default=150, help="number of cards to queue")
    ap.add_argument("--seed", type=int, default=0, help="selection seed; reaches the SQL, so a new seed means new cards")
    ap.add_argument(
        "--exclude", type=Path, nargs="*", default=[], help="previous label JSONL files whose cards should not reappear"
    )
    ap.add_argument("-o", "--out", type=Path, default=Path("/tmp/labeller.html"))
    args = ap.parse_args()

    skip = already_labelled(args.exclude) if args.exclude else set()
    cards = build_cards(args.limit, args.seed, skip, args.mode)
    args.out.write_text(PAGE.replace("__CARDS__", json.dumps(cards, separators=(",", ":"))))
    tiles = sum(len(c["tiles"]) for c in cards)
    print(
        f"[{args.mode}] {len(cards)} cards / {tiles} artworks -> {args.out}"
        + (f"  (skipped {len(skip)} already-labelled)" if skip else "")
    )
    print(f"open {args.out}")


if __name__ == "__main__":
    main()
