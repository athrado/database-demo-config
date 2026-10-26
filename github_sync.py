"""Commit files to a GitHub repository through the GitHub REST API (no git needed on the machine that runs the app).

Used by "Update seed data": on a hosted app (Streamlit Community Cloud) the disk is wiped at every restart, so the
seed data only survives when it is committed. One call makes ONE commit, however many files it touches.

Settings (environment variables, a line in .env, or Streamlit secrets):
    GITHUB_TOKEN    a fine-grained personal access token for this one repository, with "Contents: Read and write"
    GITHUB_REPO     owner/name, e.g. teneo-ai/database-demo-config
    GITHUB_BRANCH   the branch to commit to (default: main)
    GITHUB_API_URL  only for GitHub Enterprise (default: https://api.github.com)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from store import StoreError, load_dotenv

DEFAULT_API = "https://api.github.com"
TIMEOUT = 20


@dataclass
class GitHubConfig:
    token: str
    repo: str
    branch: str = "main"
    api_url: str = DEFAULT_API

    def describe(self) -> str:
        return f"{self.repo} ({self.branch})"


def get_config() -> GitHubConfig | None:
    """The GitHub settings, or None when they are not set (then the seed data is only written to the local file)."""
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip().strip("/")
    if not token and not repo:
        return None
    if not token or not repo:
        raise StoreError("GitHub is half set up: GITHUB_TOKEN and GITHUB_REPO are both needed")
    if repo.count("/") != 1 or " " in repo:
        raise StoreError(f"GITHUB_REPO must look like owner/name, not {repo!r}")
    return GitHubConfig(token, repo, os.environ.get("GITHUB_BRANCH", "").strip() or "main",
                        (os.environ.get("GITHUB_API_URL", "").strip() or DEFAULT_API).rstrip("/"))


def _call(cfg: GitHubConfig, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{cfg.api_url}/repos/{cfg.repo}/{path}"
    headers = {"Authorization": f"Bearer {cfg.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests.request(method, url, headers=headers, json=body, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise StoreError(f"Cannot reach GitHub ({cfg.api_url}): {exc.__class__.__name__}") from exc
    if resp.status_code < 300:
        return resp.json() if resp.content else {}
    try:
        detail = resp.json().get("message", "")
    except ValueError:
        detail = resp.text[:200]
    hints = {
        401: "the token is wrong or expired",
        403: "the token may not write to this repository (it needs Contents: Read and write), or the branch is protected",
        404: f"repository {cfg.repo} or branch {cfg.branch} not found, or the token cannot see the repository",
        409: "the branch changed meanwhile",
        422: "GitHub refused the change (for example a protected branch, or the branch moved meanwhile)",
    }
    raise StoreError(f"GitHub said HTTP {resp.status_code}: {detail}" + (f" ({hints[resp.status_code]})" if resp.status_code in hints else ""))


def check_access(cfg: GitHubConfig) -> str:
    """Read-only check of repository, branch and write permission."""
    repo = _call(cfg, "GET", "")
    _call(cfg, "GET", f"git/ref/heads/{cfg.branch}")
    if not (repo.get("permissions") or {}).get("push", True):
        raise StoreError(f"The token can read {cfg.repo} but not write to it (it needs Contents: Read and write)")
    return f"Connected to {cfg.describe()}"


def commit_files(cfg: GitHubConfig, changes: dict[str, str | None], message: str) -> dict | None:
    """One commit on the branch. `changes` maps a path in the repository to the new text of that file, or to None to delete it.
    Returns {"sha", "url"}, or None when nothing differs from what the branch already has."""
    if not changes:
        return None
    message = message.strip()
    if not message:
        raise StoreError("The commit message is empty")
    for attempt in (1, 2):                                                # a second try if someone else pushed in between
        ref = _call(cfg, "GET", f"git/ref/heads/{cfg.branch}")
        parent = ref["object"]["sha"]
        base_tree = _call(cfg, "GET", f"git/commits/{parent}")["tree"]["sha"]
        entries = []
        for path, text in changes.items():
            sha = None if text is None else _call(cfg, "POST", "git/blobs", {"content": text, "encoding": "utf-8"})["sha"]
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        tree = _call(cfg, "POST", "git/trees", {"base_tree": base_tree, "tree": entries})["sha"]
        if tree == base_tree:
            return None
        commit = _call(cfg, "POST", "git/commits", {"message": message, "tree": tree, "parents": [parent]})
        try:
            _call(cfg, "PATCH", f"git/refs/heads/{cfg.branch}", {"sha": commit["sha"], "force": False})
        except StoreError as exc:
            if attempt == 1 and ("HTTP 422" in str(exc) or "HTTP 409" in str(exc)) and _moved(cfg, parent):
                continue
            raise
        return {"sha": commit["sha"], "url": f"https://github.com/{cfg.repo}/commit/{commit['sha']}"}
    raise StoreError("The branch kept changing while committing; try again")


def _moved(cfg: GitHubConfig, parent: str) -> bool:
    return _call(cfg, "GET", f"git/ref/heads/{cfg.branch}")["object"]["sha"] != parent
