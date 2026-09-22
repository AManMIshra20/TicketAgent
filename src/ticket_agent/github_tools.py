"""GitHub-facing functions, split hard into READ (safe, given to the LLM as
tools) and WRITE (never given to the LLM -- only called by the executor,
after a human has approved a TriageProposal).
"""
from __future__ import annotations

from github import Github
from github.Issue import Issue
from github.Repository import Repository

from .config import get_settings


def _repo() -> Repository:
    s = get_settings()
    gh = Github(s.github_token)
    return gh.get_repo(s.github_repo)


# ---------------------------------------------------------------------------
# READ tools -- safe to expose to the LLM directly (no side effects).
# ---------------------------------------------------------------------------

def get_open_issues_without_label(label: str = "triaged") -> list[dict]:
    """Return open issues that don't yet have the given label (i.e. not yet
    processed by the agent)."""
    repo = _repo()
    issues = repo.get_issues(state="open")
    result = []
    for issue in issues:
        if issue.pull_request is not None:
            continue  # skip PRs, GitHub's API lumps them in with issues
        label_names = {lbl.name for lbl in issue.labels}
        if label not in label_names:
            result.append(_issue_to_dict(issue))
    return result


def get_issue(issue_number: int) -> dict:
    repo = _repo()
    return _issue_to_dict(repo.get_issue(issue_number))


def search_similar_issues(query: str, limit: int = 5) -> list[dict]:
    """Search closed+open issues in the repo for similar past tickets, so the
    agent can reuse prior resolutions -- the Confluence-lookup equivalent."""
    s = get_settings()
    gh = Github(s.github_token)
    results = gh.search_issues(query=f"{query} repo:{s.github_repo} type:issue")
    out = []
    for i, issue in enumerate(results):
        if i >= limit:
            break
        out.append(_issue_to_dict(issue))
    return out


def _issue_to_dict(issue: Issue) -> dict:
    return {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body or "",
        "state": issue.state,
        "labels": [lbl.name for lbl in issue.labels],
        "created_at": issue.created_at.isoformat(),
        "comments_count": issue.comments,
    }


# ---------------------------------------------------------------------------
# WRITE tools -- NOT exposed to the LLM. Only called by executor.py, and only
# after a human has approved the specific TriageProposal these come from.
# ---------------------------------------------------------------------------

def post_comment(issue_number: int, body: str) -> None:
    repo = _repo()
    repo.get_issue(issue_number).create_comment(body)


def add_labels(issue_number: int, labels: list[str]) -> None:
    repo = _repo()
    repo.get_issue(issue_number).add_to_labels(*labels)


def assign_issue(issue_number: int, assignee: str) -> None:
    repo = _repo()
    repo.get_issue(issue_number).add_to_assignees(assignee)


READ_TOOLS = {
    "get_open_issues_without_label": get_open_issues_without_label,
    "get_issue": get_issue,
    "search_similar_issues": search_similar_issues,
}
