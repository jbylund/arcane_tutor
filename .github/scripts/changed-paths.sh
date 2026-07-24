#!/usr/bin/env bash
#
# Emit `changed=true` or `changed=false` to $GITHUB_OUTPUT depending on whether this
# event touches any file matching the extended regex in $1.
#
# WHY THIS EXISTS
#
# Each test suite used to be two workflows: the real one gated on `paths:`, and a
# no-op "skip" stub gated on the mirror-image `paths-ignore:`, both declaring the
# same job name so a required status check was satisfied either way. The stub was
# needed because a workflow skipped by `paths:` leaves its check *Pending* forever
# rather than passing.
#
# That pair cannot express "no relevant files changed", because the two filters are
# not complements:
#
#   paths:        runs when ANY changed file matches
#   paths-ignore: runs when ANY changed file does NOT match
#
# So a PR touching both relevant and irrelevant files — a Rust change plus a doc,
# say — satisfies both, and BOTH workflows run. They report the same check name, so
# the 3-second stub's success sits alongside the real result and can mask a genuine
# failure: on PR #760 `gh pr checks` reported "4 passed, 0 failed" while the real
# run was red, and the failure was only visible by listing workflow runs directly.
#
# The replacement: one workflow per suite with no path filter at all (so its check
# is reported exactly once), a `changes` job that calls this script, and the real
# job carrying `needs: changes` + an `if:`. A job skipped by `if:` reports a
# `skipped` conclusion, which branch protection counts as satisfied — the exact
# behavior the stubs were faking.
#
# Usage (from a workflow step):
#   .github/scripts/changed-paths.sh '\.rs$|(^|/)Cargo\.(toml|lock)$'
#
# Requires `pull-requests: read` permission for the `gh api` call below.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <extended-regex>" >&2
    exit 2
fi
pattern="$1"

emit() {
    echo "changed=$1" >>"$GITHUB_OUTPUT"
}

# Anything that is not a pull request (notably `push` to main) has no meaningful
# "changed files vs. a base" notion for gating purposes — always do the real work.
if [ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]; then
    echo "event is ${GITHUB_EVENT_NAME:-unset}, not pull_request — running the full suite"
    emit true
    exit 0
fi

pr_number="$(jq -r '.pull_request.number' "$GITHUB_EVENT_PATH")"

# The PR's file list comes from the API rather than `git diff`: it needs no
# fetch-depth games, and it is the same list GitHub's own `paths:` filter uses, so
# the classification here matches what the old filters would have decided.
files="$(gh api "repos/${GITHUB_REPOSITORY}/pulls/${pr_number}/files" --paginate --jq '.[].filename')"

if [ -z "$files" ]; then
    # An empty PR (or an API hiccup returning nothing) should not silently skip the
    # suite — failing open is the safe direction for a test gate.
    echo "no files reported for PR #${pr_number} — running the full suite to be safe"
    emit true
    exit 0
fi

echo "changed files in PR #${pr_number}:"
echo "$files" | sed 's/^/  /'

if matched="$(echo "$files" | grep -E "$pattern")"; then
    echo "matched /${pattern}/:"
    echo "$matched" | sed 's/^/  /'
    emit true
else
    echo "nothing matched /${pattern}/ — skipping this suite"
    emit false
fi
