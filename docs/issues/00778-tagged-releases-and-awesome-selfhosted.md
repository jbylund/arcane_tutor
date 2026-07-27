# Tagged Releases, a Changelog, and Submission to awesome-selfhosted

GitHub issue: [#778](https://github.com/jbylund/sylvan_librarian/issues/778)

awesome-selfhosted is a durable discovery channel rather than a one-day traffic spike, which makes
it worth more over a year than any single link-sharing push. The project satisfies every one of
their requirements except tagged releases — and that requirement carries a four-month waiting
period, so the clock should start now regardless of when we intend to submit.

## The gate

From [their PR template](https://github.com/awesome-selfhosted/awesome-selfhosted-data/blob/master/.github/PULL_REQUEST_TEMPLATE.md):

> - [ ] Any software project you are adding was first released more than 4 months ago.

and from the canned replies in `CONTRIBUTING.md`, which exist because this comes up often:

> Hi, thanks for your contribution.
>
> However, there are no tagged releases for this project. Our guidelines require that *Any software
> project you are adding was first released more than 4 months ago.* We encourage you to create a
> release now and/or a simple changelog that will help users keep track of changes in the software
> (especially breaking changes or changes requiring configuration tweaks), and will allow
> administrators to install a known working, unchanging version (as opposed to always installing the
> latest development version).

Current state, measured 2026-07-26:

```
$ git tag
pre-squash-702          # internal marker, not a version

$ gh release list --repo jbylund/sylvan_librarian
(empty)
```

So we would be rejected on sight. The reasoning in that canned reply is also just correct on its own
terms — a self-hoster currently has no way to pin a known-good version.

## Audit against the rest of their requirements

Checked against `CONTRIBUTING.md` and the PR template on 2026-07-26. Everything else passes:

| requirement | status |
| --- | --- |
| FOSS license | ISC, present in their `licenses.yml` as `Internet Systems Consortium License` |
| platforms exist | `platforms/python.yml`, `platforms/rust.yml`, `platforms/docker.yml` |
| category exists | `tags/games.yml` — there is no Search Engines category |
| actively maintained | yes (their removal rule is 6–12 months of inactivity) |
| working installation instructions | yes, and verified from a clean clone on both Linux tooling and stock macOS |
| interactive demo | <https://sylvan-librarian.com/> — must be interactive, not a video |
| not a library, PaaS, or cloud-dependent | correct, none of the disqualifiers apply |
| not already listed elsewhere | not present in awesome-sysadmin |

Note that in single-page mode software appears **only under its first tag**, so `Games` is the entire
placement decision, not merely the first of several.

## Phase 1 — releases and changelog (do now)

1. Write `CHANGELOG.md`. There is raw material in `docs/changelog/`, currently a set of dated
   per-change notes rather than a released-version history.
2. Choose an initial version. The project is well past a `0.1`-shaped state, but calling it `1.0.0`
   implies stability commitments around the query DSL that may not be wanted yet.
3. Tag it and cut a GitHub release. `pre-squash-702` does not count and should probably be left
   alone rather than reused.

This is worth doing independently of the list — it is the only thing that currently prevents someone
from pinning a version.

## Phase 2 — submission (roughly four months after the first release)

Submit a single-item PR to
[awesome-selfhosted-data](https://github.com/awesome-selfhosted/awesome-selfhosted-data) adding
`software/sylvan-librarian.yml`, kebab-case filename, comments and unused optional fields removed.
Merge lands about a week after approval.

The entry below is drafted and validated against their formatting rules — 241 characters against a
250 limit, no leading article, and none of the words they reject as redundant (`open-source`,
`free`, `self-hosted`). The `(alternative to $PRODUCT)` suffix is their required convention.

```yaml
name: "Sylvan Librarian"
website_url: "https://sylvan-librarian.com/"
source_code_url: "https://github.com/jbylund/sylvan_librarian"
description: "Magic: The Gathering card search with Scryfall-compatible query syntax, extended with arithmetic across card fields such as cmc+1<power. Searches are served from an in-memory Rust index, with PostgreSQL as fallback. (alternative to Scryfall)"
licenses:
  - ISC
platforms:
  - Python
  - Rust
  - Docker
tags:
  - Games
depends_3rdparty: true
demo_url: "https://sylvan-librarian.com/"
```

### Open question: `depends_3rdparty`

The field means the software depends on a third-party service outside the user's control. An
instance cannot populate itself without Scryfall's bulk export, so `true` is the honest answer and
avoids a reviewer catching it later. The cost is that some readers filter the flag out. Left as
`true` in the draft; flip it only with a deliberate reason.

## Rejected alternative

Submitting now and hoping the repository's age carries the four-month requirement. The repo has
existed on GitHub since 2023-03-05, which is well beyond four months, but the requirement is written
in terms of *releases* and the canned reply keys off `git tag` being empty. Not worth spending a
maintainer's review cycle to test.
