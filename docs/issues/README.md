# Issue Doc Conventions

`docs/issues/` holds the deep design and implementation notes for engine and product work. This is
the primary source of truth. The essentials are summarized in [CLAUDE.md](../../CLAUDE.md#issue-tracking);
this doc is the full version.

## Relationship to GitHub issues

GitHub issues are a secondary triage-and-status layer, and they must stand on their own — a reader
should understand the problem and the gist of the approach from the GitHub issue alone, without
opening the doc. But the doc is where the real depth lives (measurements, rejected alternatives,
iteration history), so the GitHub issue does not need to duplicate that. Link to it instead.

## Naming

`#####-slug.md`, with a 5-digit zero-padded GitHub issue number — or a PR number if no issue exists.
For example `00623-engine-flavor-absent-gram-bitmap.md` for #623. Prefer the issue number over its
merging PR's number when both exist. Finished work moves to `done/`.

Docs with no GitHub issue of their own take a prefix that signals intent, so a reader can tell
backlog from background at a glance:

- **`local-slug.md` — proposed / not-yet-filed work.** A design or fix meant to happen but not yet
  filed as an issue, e.g. `local-engine-range-veto-redundancy.md`. When a `local-` doc later gets a
  GitHub issue, rename it to the assigned number.
- **`reference-slug.md` — reference material that is deliberately not scheduled.** A general pattern,
  design study, or rejected-but-preserved approach that is *not* todo work and should not be read as
  a backlog item, e.g. `reference-engine-printing-varying-plane-repair-pattern.md`. Promote to
  `local-` or a number only if it actually becomes planned work.
- **`security-slug.md` — unfixed security findings.** Never committed; see below.

## Unfixed security findings

The one exception to "all of it, tracked". This repo is public, so committing a description of a
live, unfixed vulnerability publishes a working recipe against the running deployment — permanently,
since removing the file later does not remove it from history. `docs/issues/security-*` is gitignored
to make that mechanical rather than something to remember.

**When the finding is fixed, rename it off the prefix** (to `local-`, a number, or `done/`) and
commit it. At that point the write-up is a useful record rather than a map, and it should not stay
invisible.

### The design doc for a fix usually needs the prefix too

The bar is not "did I omit the specifics." It is whether a reader could derive the issue from what
remains. A doc explaining *why* a boundary is missing generally shows *where* it is missing, so in
practice these fail the test even when written carefully — default to `security-` and rename once the
fix lands. Only a genuinely general architecture note, one that would read the same had no
vulnerability prompted it, belongs in-tree before the fix.

### While fixing

Keep commit messages boring and factual — `Refactor: Extract Router from APIResource`, not
`Fix: unauthenticated admin endpoints`. Commits on a public repo are visible in real time, and a
message that advertises the defect narrows the window between disclosure and deploy.

### Two caveats that come with the gitignore

- It protects against `git add -A` and `git clean -fd`, but **not** `git clean -fdx`, which
  deletes ignored files.
- These docs then exist on one machine only. A finding worth keeping needs a home outside the
  working tree.

### Findings split by blast radius

This changes where they belong. A defect in code or compose that **ships in the repo** affects
everyone who deploys it, so it gates tagged releases (see
[#778](https://github.com/jbylund/sylvan_librarian/issues/778)) — fix before releasing and there is
never a vulnerable version to disclose. A misconfiguration of *our own* host (nginx, a stray
credential) affects only us and needs no disclosure path at all.

## Length and scope

Issue docs are not subject to the ~100-line length ideal in the global markdown rules. They are the
deep source of truth, so length should match the material.

What governs instead is **one shippable idea per doc**. The split signal is not line count but
whether the doc holds multiple *independent* ideas that would land as separate PRs. When it does,
extract each into its own doc and cross-link. In practice docs cluster around 100–300 lines; treat
~500 as the prompt to check for an independent idea hiding inside.

The other global conventions still apply in full — cross-link rather than duplicate, and prefer a
well-linked narrative over bare index docs.
