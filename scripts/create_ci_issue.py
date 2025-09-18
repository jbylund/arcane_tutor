#!/usr/bin/env python3
"""Create GitHub issue for CI failures.

This script creates a GitHub issue when CI failures are detected, with detailed
information about the failures and instructions for resolution.
"""

import json
import os
import sys
from typing import Any

import requests


def get_github_headers() -> dict[str, str]:
    """Get headers for GitHub API requests."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        msg = "GITHUB_TOKEN environment variable is required"
        raise ValueError(msg)

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "CI-Monitor-Script/1.0",
    }


def check_existing_issues(owner: str, repo: str) -> list[dict[str, Any]]:
    """Check if there are existing CI failure issues."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = get_github_headers()

    params = {
        "state": "open",
        "labels": "ci-failure",
        "creator": "github-actions[bot]",
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()

    return response.json()


def create_issue(  # noqa: PLR0913
    *,
    assignees: list[str],
    body: str,
    labels: list[str],
    owner: str,
    repo: str,
    title: str,
) -> dict[str, Any]:
    """Create a new GitHub issue."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = get_github_headers()

    payload = {
        "title": title,
        "body": body,
        "labels": labels,
        "assignees": assignees,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    return response.json()


def format_issue_body(commit_sha: str, failed_checks: list[dict[str, Any]]) -> str:
    """Format the issue body with failure details."""
    checks_markdown = ""
    for check in failed_checks:
        checks_markdown += f"- **{check['name']}** ({check['type']})\n"
        if check.get("url"):
            checks_markdown += f"  - [View details]({check['url']})\n"

    return f"""## CI Failure Detected

The automated CI monitor has detected failing checks on the main branch.

**Commit:** {commit_sha}
**Failed Checks:**

{checks_markdown}

**What to do:**
1. Review the failing checks above
2. Fix any linting errors by running: `python -m ruff check --fix --unsafe-fixes`
3. Fix any failing tests by running: `python -m pytest -vvv`
4. Commit and push the fixes to main branch
5. Close this issue once CI passes

This issue was automatically created by the CI monitoring workflow."""


def main() -> None:
    """Main function to create CI failure issue."""
    try:
        # Get inputs from command line arguments
        expected_args = 3
        if len(sys.argv) != expected_args:
            sys.exit(1)

        failed_checks_json = sys.argv[1]
        commit_sha = sys.argv[2]

        # Parse failed checks
        try:
            failed_checks = json.loads(failed_checks_json)
        except json.JSONDecodeError:
            sys.exit(1)

        # Get repository information from environment
        github_repository = os.environ.get("GITHUB_REPOSITORY")
        if not github_repository:
            msg = "GITHUB_REPOSITORY environment variable is required"
            raise ValueError(msg)

        owner, repo = github_repository.split("/", 1)

        # Check if there's already an open issue for CI failures
        existing_issues = check_existing_issues(owner, repo)

        if existing_issues:
            return

        # Create issue title and body
        issue_title = f"CI Checks Failing on Main Branch ({commit_sha[:7]})"
        issue_body = format_issue_body(commit_sha, failed_checks)

        # Create the issue
        create_issue(
            assignees=["copilot-swe-agent"],
            body=issue_body,
            labels=["ci-failure", "bug"],
            owner=owner,
            repo=repo,
            title=issue_title,
        )


    except (ValueError, requests.RequestException, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
