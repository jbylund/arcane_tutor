#!/usr/bin/env python3
"""Check CI status on main branch and detect failures.

This script checks the latest commit on the main branch and scans all repository
workflows to identify any failures. It outputs the results for use in GitHub Actions.
"""

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


def set_github_output(name: str, value: str) -> None:
    """Set a GitHub Actions output variable."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        # Fallback for local testing
        pass


def main() -> None:
    """Main function to check CI status."""
    try:
        # Get repository information from environment
        github_repository = os.environ.get("GITHUB_REPOSITORY")
        if not github_repository:
            msg = "GITHUB_REPOSITORY environment variable is required"
            raise ValueError(msg)

        owner, repo = github_repository.split("/", 1)

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


        # Set GitHub Actions outputs
        set_github_output("has_failed", str(has_failed).lower())
        set_github_output("failed_checks", json.dumps(failed_checks))
        set_github_output("commit_sha", latest_commit_sha)


    except (ValueError, requests.RequestException) as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
