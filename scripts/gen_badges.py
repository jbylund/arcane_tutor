"""Generate the README's dynamic badge assets.

Two kinds of asset land in the output directory:

1. ``languages.svg`` — a donut of source lines per language, counted over tracked files
   only (``git ls-files``), with comments and blank lines excluded. This deliberately
   differs from GitHub's own Languages sidebar, which measures *bytes*: Rust averages
   ~54 bytes/line in this repo against Python's ~42, so the byte view ranks Rust first
   while the line view ranks Python first. Lines are the number a reader actually means
   by "how much code".

2. ``tests-<suite>.json`` — one shields.io endpoint document per test suite, so the
   three badges read alike in the README the way the three checks read alike in the PR
   checks list.

3. ``logo-<language>.svg`` — the header's language marks, mirrored from upstream so the
   README depends on our own CDN rather than on two third-party ones staying up.

Both are uploaded to S3 and served through CloudFront by .github/workflows/badges.yml;
nothing generated here is committed to the repo.

Counting code-vs-comment lines is delegated to ``tokei`` rather than hand-rolled: getting
it right means knowing that Rust block comments nest and that a ``//`` inside a string
literal is not a comment. Note that tokei counts a Python docstring as code, not as a
comment — it is a string expression, not comment syntax — which is worth roughly 600
lines of the Python total here. Tools disagree on this; scc scores it the other way.

Usage:
    python scripts/gen_badges.py --out-dir dist/badges
    python scripts/gen_badges.py --out-dir dist/badges --skip-tests    # chart only
    python scripts/gen_badges.py --out-dir dist/badges --tokei ./tokei # non-PATH binary
    python scripts/gen_badges.py --out-dir dist/badges --skip-logos    # no logo mirror
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("gen_badges")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Language marks for the README header, mirrored onto our own CDN rather than hotlinked.
# A hotlinked header depends on two third-party CDNs staying up and keeping their URL
# schemes; mirroring costs four small files a day and puts the whole header on
# infrastructure we control.
#
# devicon is pinned to a release tag because its `main` is a moving target — an icon
# redraw would land in the README unannounced. simple-icons has no versioned CDN path,
# so the daily snapshot is the pin.
#
# Licensing: devicon is MIT, simple-icons is CC0-1.0. Both permit redistribution; the
# attribution is recorded in docs/legal/legal.md.
DEVICON_VERSION = "v2.17.0"
DEVICON = f"https://cdn.jsdelivr.net/gh/devicons/devicon@{DEVICON_VERSION}/icons"
LOGO_SOURCES = {
    # The GitHub and Rust marks are near-black, so they use the simple-icons dual-color
    # form, which emits a prefers-color-scheme rule instead of a fixed fill. Without it
    # they disappear against a dark README.
    "github": "https://cdn.simpleicons.org/github/181717/ffffff",
    "rust": "https://cdn.simpleicons.org/rust/000000/ffffff",
    "python": f"{DEVICON}/python/python-original.svg",
    "javascript": f"{DEVICON}/javascript/javascript-original.svg",
}
LOGO_FETCH_TIMEOUT_SECONDS = 20

# cdn.simpleicons.org answers 403 to clients that send no User-Agent, which is what
# urllib does by default. The value does not matter; its presence does.
LOGO_FETCH_USER_AGENT = "sylvan-librarian-badge-generator"

# Enough of the response to tell an SVG from a CDN error page.
MARKUP_SNIFF_BYTES = 512

# Which tracked files count as source at all. Only languages GitHub's linguist treats as
# code appear here; prose (.md) and data (.json, .toml, .yml) are excluded so the chart
# answers "how much code" rather than "how many bytes are in the tree".
COUNTED_SUFFIXES = frozenset({".rs", ".py", ".js", ".html", ".sql", ".css", ".sh"})
# Compared case-insensitively: this repo's makefile is lowercase, and matching only
# "Makefile" silently dropped it.
COUNTED_STEMS = frozenset({"makefile", "gnumakefile", "dockerfile"})

# tokei's language names -> the labels used here. Only entries that actually differ need
# listing; anything tokei reports that is missing from this map keeps its own name, and
# anything outside CHARTED_LANGUAGES ends up in "Other" regardless.
TOKEI_NAME_ALIASES = {
    "SQL": "PLpgSQL",  # the schema is Postgres-flavoured; linguist agrees
}

# tokei repeats every count under a synthetic "Total" language; adding it would double
# every figure in the chart.
TOKEI_TOTAL_KEY = "Total"

# linguist's own colors, so the chart matches what GitHub renders in the sidebar.
LANGUAGE_COLORS = {
    "Rust": "#dea584",
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "HTML": "#e34c26",
    "PLpgSQL": "#336790",
    "CSS": "#663399",
    "Makefile": "#427819",
    "Dockerfile": "#384d54",
    "Shell": "#89e051",
    "Other": "#8b949e",
}

# Generated or minified files are checked in for deployment but are not authored code;
# counting them would swamp the chart with one-line minified bundles.
EXCLUDED_PATH_PARTS = (".min.js", ".min.css")

# Benchmark harnesses are measurement scaffolding, not the shipping engine. They are
# excluded in BOTH languages on purpose: card_engine carries ~4.3k lines of bench_*.rs
# and scripts/ carries ~7.1k lines of bench_*.py, so dropping one without the other would
# tilt the very Python-vs-Rust ratio this chart exists to report.
EXCLUDED_PATH_GLOBS = (
    "card_engine/src/bench_*.rs",
    "shared_cache/src/bench_*.rs",
    "scripts/bench_*.py",
)

# Only these get a slice of their own; everything else in COUNTED_SUFFIXES is still
# counted, but lands in "Other". The remaining languages are all support material — the
# schema, the stylesheet, the Makefile — and a legend of six 1% wedges says less about
# what this project is made of than one honest "Other" does.
CHARTED_LANGUAGES = ("Python", "Rust", "JavaScript")

# --- SVG geometry, in user units (the whole card scales with the viewBox) ---
CARD_WIDTH = 344
CARD_PADDING = 16
DONUT_CENTER_X = 88
DONUT_RADIUS = 52  # radius of the stroke's centerline, not its outer edge
DONUT_STROKE_WIDTH = 26
TITLE_BASELINE_Y = 16
LEGEND_LEFT_X = 168
LEGEND_TOP_Y = 34
LEGEND_ROW_HEIGHT = 22
LEGEND_SWATCH_RADIUS = 5
LEGEND_LABEL_DX = 14  # label offset from the swatch center
SEGMENT_GAP_DEGREES = 1.5  # hairline separator so adjacent slices stay distinguishable

# Line counts are abbreviated past this, so "27855" reads as "27.9k".
THOUSAND = 1000

# How much of a malformed tokei response to quote back in the error.
JSON_ERROR_EXCERPT_CHARS = 500


@dataclass
class LanguageCount:
    """Source-line count for one language, excluding comments and blank lines."""

    name: str
    lines: int

    @property
    def color(self) -> str:
        """The linguist color for this language, falling back to the "Other" grey."""
        return LANGUAGE_COLORS.get(self.name, LANGUAGE_COLORS["Other"])


def run(cmd: list[str], cwd: Path = REPO_ROOT, check: bool = True) -> str:
    """Run a command and return its combined output."""
    LOGGER.debug("running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        message = f"{' '.join(cmd)} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        raise RuntimeError(message)
    return result.stdout + result.stderr


def is_counted(path: Path) -> bool:
    """Whether a tracked path contributes to the language chart."""
    if any(part in path.name for part in EXCLUDED_PATH_PARTS):
        return False
    if any(path.match(pattern) for pattern in EXCLUDED_PATH_GLOBS):
        return False
    return path.suffix in COUNTED_SUFFIXES or path.name.lower() in COUNTED_STEMS


def tracked_source_files() -> list[str]:
    """List the tracked files the chart counts.

    Deliberately an explicit file list rather than pointing tokei at the tree: tokei
    would otherwise walk untracked working-tree directories (benchmarks/, ignored/, draft
    blog posts) that are not part of the project's source.
    """
    tracked = run(["git", "ls-files", "-z"]).split("\0")
    return [entry for entry in tracked if entry and is_counted(Path(entry)) and (REPO_ROOT / entry).is_file()]


def count_lines(tokei_binary: str) -> list[LanguageCount]:
    """Count source lines per language across tracked files, largest first.

    Uses tokei's ``code`` figure, which is physical lines minus comments and blanks.
    """
    paths = tracked_source_files()
    if not paths:
        message = "git ls-files matched no countable source files"
        raise RuntimeError(message)
    # One exec with every path: xargs-style batching would split the run into several
    # invocations and emit several JSON documents instead of one.
    raw = run([tokei_binary, "--output", "json", *paths])
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"could not parse tokei output as JSON: {exc}\n{raw[:JSON_ERROR_EXCERPT_CHARS]}"
        raise RuntimeError(message) from exc

    totals: dict[str, int] = {}
    for name, stats in report.items():
        if name == TOKEI_TOTAL_KEY:
            continue
        label = TOKEI_NAME_ALIASES.get(name, name)
        totals[label] = totals.get(label, 0) + stats["code"]
    return sorted((LanguageCount(name, lines) for name, lines in totals.items() if lines), key=lambda lc: -lc.lines)


def collapse_tail(counts: list[LanguageCount]) -> list[LanguageCount]:
    """Fold everything outside CHARTED_LANGUAGES into a single "Other" slice."""
    kept = [entry for entry in counts if entry.name in CHARTED_LANGUAGES]
    folded = sum(entry.lines for entry in counts if entry.name not in CHARTED_LANGUAGES)
    if folded:
        kept.append(LanguageCount("Other", folded))
    return kept


def human_lines(lines: int) -> str:
    """Format a line count the way a reader scans it: 42410 -> "42.4k"."""
    if lines < THOUSAND:
        return str(lines)
    return f"{lines / THOUSAND:.1f}k"


def render_donut(counts: list[LanguageCount]) -> str:
    """Render the language breakdown as a self-contained, theme-aware SVG donut.

    Segments are drawn as dash-array arcs on a single circle rather than as path arcs:
    the arithmetic is a running offset instead of trigonometry per wedge, and there are
    no seams where wedges meet.
    """
    total = sum(lc.lines for lc in counts)
    if not total:
        message = "no countable source lines found"
        raise RuntimeError(message)

    # The legend is a single column that grows with the entry count, and the card grows
    # with it — a wrapped second column holding one orphaned row reads as a mistake.
    donut_extent = 2 * (DONUT_RADIUS + DONUT_STROKE_WIDTH / 2)
    legend_extent = LEGEND_TOP_Y + len(counts) * LEGEND_ROW_HEIGHT
    card_height = int(max(donut_extent + 2 * CARD_PADDING, legend_extent + CARD_PADDING))
    donut_center_y = card_height / 2

    circumference = 2 * math.pi * DONUT_RADIUS
    gap = circumference * (SEGMENT_GAP_DEGREES / 360)

    segments = []
    offset = 0.0
    for entry in counts:
        arc = circumference * entry.lines / total
        # Never let the gap eat a thin slice entirely.
        drawn = max(arc - gap, circumference * 0.002)
        segments.append(
            f'    <circle cx="{DONUT_CENTER_X}" cy="{donut_center_y}" r="{DONUT_RADIUS}" fill="none"'
            f' stroke="{entry.color}" stroke-width="{DONUT_STROKE_WIDTH}"'
            f' stroke-dasharray="{drawn:.2f} {circumference - drawn:.2f}"'
            f' stroke-dashoffset="{-offset:.2f}"><title>{entry.name}: {entry.lines:,} lines</title></circle>'
        )
        offset += arc

    rows = []
    for index, entry in enumerate(counts):
        cy = LEGEND_TOP_Y + index * LEGEND_ROW_HEIGHT
        share = 100 * entry.lines / total
        rows.append(
            f'    <circle cx="{LEGEND_LEFT_X}" cy="{cy}" r="{LEGEND_SWATCH_RADIUS}" fill="{entry.color}"/>\n'
            f'    <text class="legend" x="{LEGEND_LEFT_X + LEGEND_LABEL_DX}" y="{cy + 4}">'
            f'{entry.name} <tspan class="muted">{human_lines(entry.lines)} · {share:.1f}%</tspan></text>'
        )

    newline = "\n"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" height="{card_height}" \
viewBox="0 0 {CARD_WIDTH} {card_height}" role="img" aria-label="Lines of code by language">
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .title {{ font-size: 13px; font-weight: 600; fill: #24292f; }}
    .legend {{ font-size: 12px; fill: #24292f; }}
    .muted {{ fill: #57606a; }}
    .total {{ font-size: 15px; font-weight: 600; fill: #24292f; }}
    .totall {{ font-size: 9px; fill: #57606a; letter-spacing: 0.06em; }}
    @media (prefers-color-scheme: dark) {{
      .title, .legend, .total {{ fill: #e6edf3; }}
      .muted, .totall {{ fill: #8b949e; }}
    }}
  </style>
  <text class="title" x="{LEGEND_LEFT_X}" y="{TITLE_BASELINE_Y}">Lines of code by language</text>
{newline.join(segments)}
  <text class="total" x="{DONUT_CENTER_X}" y="{donut_center_y + 1}" text-anchor="middle">{human_lines(total)}</text>
  <text class="totall" x="{DONUT_CENTER_X}" y="{donut_center_y + 15}" text-anchor="middle">LINES</text>
{newline.join(rows)}
</svg>
"""


def count_python_tests() -> int | None:
    """Collect (without running) the pytest suite."""
    output = run([sys.executable, "-m", "pytest", "--collect-only", "-q"], check=False)
    match = re.search(r"(\d+) tests? collected", output)
    return int(match.group(1)) if match else None


def count_rust_tests() -> int | None:
    """Sum the test counts every cargo test binary reports under --list."""
    total = 0
    found = False
    for manifest in ("card_engine/Cargo.toml", "shared_cache/Cargo.toml"):
        output = run(["cargo", "test", "--manifest-path", manifest, "--", "--list"], check=False)
        for match in re.finditer(r"(\d+) tests?, \d+ benchmarks", output):
            total += int(match.group(1))
            found = True
    return total if found else None


def count_js_tests() -> int | None:
    """Run jest and read the count off its summary line."""
    output = run(["npx", "jest"], check=False)
    match = re.search(r"^Tests:.*?(\d+) total", output, re.MULTILINE)
    return int(match.group(1)) if match else None


TEST_SUITES = {
    "python": count_python_tests,
    "rust": count_rust_tests,
    "js": count_js_tests,
}

# shields.io renders the endpoint document; this is its schema version, not ours.
SHIELDS_SCHEMA_VERSION = 1
SHIELDS_COLOR = "#0a7bbb"


def write_test_badges(out_dir: Path) -> None:
    """Write one shields.io endpoint document per suite.

    A suite whose count cannot be collected is skipped rather than written as zero: a
    missing badge is an obvious problem, whereas "0 tests" looks like a real answer.
    """
    for suite, counter in TEST_SUITES.items():
        try:
            count = counter()
        except RuntimeError as exc:
            LOGGER.warning("could not count %s tests: %s", suite, exc)
            continue
        if count is None:
            LOGGER.warning("could not parse a test count for %s; leaving its badge untouched", suite)
            continue
        target = out_dir / f"tests-{suite}.json"
        target.write_text(
            json.dumps(
                {
                    "schemaVersion": SHIELDS_SCHEMA_VERSION,
                    "label": suite,
                    "message": f"{count:,} tests",
                    "color": SHIELDS_COLOR,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        LOGGER.info("wrote %s (%d tests)", target, count)


def mirror_logos(out_dir: Path) -> None:
    """Copy the header's language marks into the output directory.

    A logo that cannot be fetched is skipped rather than written empty: the previous
    upload stays in place on the CDN, so a transient upstream outage leaves yesterday's
    mark in the README instead of a broken image.
    """
    for name, url in LOGO_SOURCES.items():
        request = urllib.request.Request(url, headers={"User-Agent": LOGO_FETCH_USER_AGENT})  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=LOGO_FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310
                body = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            LOGGER.warning("could not fetch the %s logo from %s: %s", name, url, exc)
            continue
        # Guard against a CDN error page being mirrored as if it were the icon.
        if b"<svg" not in body[:MARKUP_SNIFF_BYTES]:
            LOGGER.warning("%s did not return SVG for %s; leaving the published copy alone", url, name)
            continue
        target = out_dir / f"logo-{name}.svg"
        target.write_bytes(body)
        LOGGER.info("wrote %s (%d bytes)", target, len(body))


def main() -> int:
    """Render the badge assets into --out-dir."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True, help="directory to write badge assets into")
    parser.add_argument("--skip-tests", action="store_true", help="skip the per-suite test counts")
    parser.add_argument("--skip-logos", action="store_true", help="skip mirroring the header logos")
    parser.add_argument("--tokei", default="tokei", help="path to the tokei binary (default: found on PATH)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    counts = collapse_tail(count_lines(args.tokei))
    chart = args.out_dir / "languages.svg"
    chart.write_text(render_donut(counts), encoding="utf-8")
    LOGGER.info("wrote %s (%s)", chart, ", ".join(f"{c.name} {c.lines:,}" for c in counts))

    if not args.skip_tests:
        write_test_badges(args.out_dir)
    if not args.skip_logos:
        mirror_logos(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
