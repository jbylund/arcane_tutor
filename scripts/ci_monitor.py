#!/usr/bin/env python3
"""Consolidated CI monitoring script.

This script checks CI status on main branch, detects failures, and optionally
creates GitHub issues when CI failures are detected. It combines the functionality
of check_ci_status.py and create_ci_issue.py into a single, more maintainable script.
"""

import argparse
import json
import os
import sys
from pathlib import Path
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


def get_main_branch_commit(owner: str, repo: str) -> str:
    """Get the latest commit SHA from the main branch."""
    url = f"https://api.github.com/repos/{owner}/{repo}/branches/main"
    headers = get_github_headers()

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data["commit"]["sha"]


def get_repository_workflows(owner: str, repo: str) -> list[dict[str, Any]]:
    """Get all workflows in the repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows"
    headers = get_github_headers()

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data["workflows"]


def check_workflow_runs(owner: str, repo: str, workflow_id: int) -> list[dict[str, Any]]:
    """Get the latest workflow run for a specific workflow on main branch."""
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
    headers = get_github_headers()

    params = {
        "branch": "main",
        "per_page": 1,
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data["workflow_runs"]


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


def set_github_output(name: str, value: str) -> None:
    """Set a GitHub Actions output variable."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        # Fallback for local testing
        pass


def check_ci_status(owner: str, repo: str) -> tuple[bool, list[dict[str, Any]], str]:
    """Check CI status and return failure status, failed checks, and commit SHA."""
    # Get latest commit on main branch
    latest_commit_sha = get_main_branch_commit(owner, repo)

    # Get all workflows
    workflows = get_repository_workflows(owner, repo)

    has_failed = False
    failed_checks = []

    # Check each workflow's recent runs on main branch
    for workflow in workflows:
        # Skip the CI Monitor workflow itself
        if workflow["name"] == "CI Monitor" or "ci-monitor" in workflow["path"]:
            continue

        try:
            runs = check_workflow_runs(owner, repo, workflow["id"])

            if runs:
                latest_run = runs[0]

                # Check if this run is for the latest commit and failed
                if latest_run["head_sha"] == latest_commit_sha and latest_run["conclusion"] == "failure":
                    has_failed = True
                    failed_checks.append({
                        "name": workflow["name"],
                        "type": "workflow",
                        "url": latest_run["html_url"],
                        "conclusion": latest_run["conclusion"],
                    })
        except requests.RequestException as error:
            print(f"Error checking workflow {workflow['name']}: {error}")  # noqa: T201

    return has_failed, failed_checks, latest_commit_sha


def create_ci_issue_if_needed(owner: str, repo: str, failed_checks: list[dict[str, Any]], commit_sha: str) -> bool:
    """Create CI failure issue if needed. Returns True if issue was created."""
    # Check if there's already an open issue for CI failures
    existing_issues = check_existing_issues(owner, repo)

    if existing_issues:
        print("CI failure issue already exists, skipping creation")  # noqa: T201
        return False

    # Create issue title and body
    issue_title = f"CI Checks Failing on Main Branch ({commit_sha[:7]})"
    issue_body = format_issue_body(commit_sha, failed_checks)

    # Create the issue
    issue = create_issue(
        assignees=["copilot-swe-agent"],
        body=issue_body,
        labels=["ci-failure", "bug"],
        owner=owner,
        repo=repo,
        title=issue_title,
    )

    print(f"Created CI failure issue: {issue['html_url']}")  # noqa: T201
    return True


def get_repository_info() -> tuple[str, str]:
    """Get repository owner and name from environment."""
    github_repository = os.environ.get("GITHUB_REPOSITORY")
    if not github_repository:
        msg = "GITHUB_REPOSITORY environment variable is required"
        raise ValueError(msg)

    owner, repo = github_repository.split("/", 1)
    return owner, repo


def get_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Monitor CI status and optionally create issues for failures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --check-only               # Just check CI status and set outputs
  %(prog)s --check-and-create-issue   # Check CI status and create issue if needed
  %(prog)s --create-issue --failed-checks='[...]' --commit-sha=abc123  # Create issue directly

Environment Variables:
  GITHUB_TOKEN      - Required GitHub token for API access
  GITHUB_REPOSITORY - Required repository in format 'owner/repo'
  GITHUB_OUTPUT     - GitHub Actions output file (optional, for CI integration)
        """,
    )

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--check-only",
        action="store_true",
        help="Only check CI status and set GitHub Actions outputs (equivalent to old check_ci_status.py)",
    )
    action_group.add_argument(
        "--check-and-create-issue",
        action="store_true",
        help="Check CI status and create issue if failures detected (combined functionality)",
    )
    action_group.add_argument(
        "--create-issue",
        action="store_true",
        help="Create issue directly using provided failed checks (equivalent to old create_ci_issue.py)",
    )

    # Arguments for direct issue creation
    parser.add_argument(
        "--failed-checks",
        type=str,
        help="JSON string of failed checks (required when using --create-issue)",
    )
    parser.add_argument(
        "--commit-sha",
        type=str,
        help="Commit SHA for the failure (required when using --create-issue)",
    )

    return parser.parse_args()


def handle_check_only(owner: str, repo: str) -> None:
    """Handle --check-only mode."""
    has_failed, failed_checks, commit_sha = check_ci_status(owner, repo)

    # Set GitHub Actions outputs
    set_github_output("has_failed", str(has_failed).lower())
    set_github_output("failed_checks", json.dumps(failed_checks))
    set_github_output("commit_sha", commit_sha)

    print(f"CI Status: {'FAILED' if has_failed else 'PASSED'}")  # noqa: T201
    if has_failed:
        print(f"Failed checks: {len(failed_checks)}")  # noqa: T201
        for check in failed_checks:
            print(f"  - {check['name']}")  # noqa: T201


def handle_check_and_create_issue(owner: str, repo: str) -> None:
    """Handle --check-and-create-issue mode."""
    has_failed, failed_checks, commit_sha = check_ci_status(owner, repo)

    # Set GitHub Actions outputs
    set_github_output("has_failed", str(has_failed).lower())
    set_github_output("failed_checks", json.dumps(failed_checks))
    set_github_output("commit_sha", commit_sha)

    print(f"CI Status: {'FAILED' if has_failed else 'PASSED'}")  # noqa: T201

    if has_failed:
        print(f"Failed checks: {len(failed_checks)}")  # noqa: T201
        for check in failed_checks:
            print(f"  - {check['name']}")  # noqa: T201

        # Create issue if needed
        issue_created = create_ci_issue_if_needed(owner, repo, failed_checks, commit_sha)
        if not issue_created:
            print("Issue creation skipped (existing issue found)")  # noqa: T201


def handle_create_issue_direct(owner: str, repo: str, args: argparse.Namespace) -> None:
    """Handle --create-issue mode."""
    if not args.failed_checks or not args.commit_sha:
        print("Error: --failed-checks and --commit-sha are required when using --create-issue", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    try:
        failed_checks = json.loads(args.failed_checks)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in --failed-checks: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    create_ci_issue_if_needed(owner, repo, failed_checks, args.commit_sha)


def main() -> None:
    """Main function to monitor CI status and manage issues."""
    try:
        args = get_args()
        owner, repo = get_repository_info()

        if args.check_only:
            handle_check_only(owner, repo)
        elif args.check_and_create_issue:
            handle_check_and_create_issue(owner, repo)
        elif args.create_issue:
            handle_create_issue_direct(owner, repo, args)

    except (ValueError, requests.RequestException, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
