"""Git operations: clone, checkout, branch, commit, push."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError

from sonarfix.config import get_settings


class GitManager:
    def __init__(self, workspace: Optional[Path] = None):
        cfg = get_settings()
        self.workspace = workspace or cfg.workspace_dir
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._repo: Optional[Repo] = None

    @property
    def repo(self) -> Repo:
        if self._repo is None:
            raise RuntimeError("No repository loaded. Call clone_or_open() first.")
        return self._repo

    @property
    def repo_dir(self) -> Path:
        return Path(self.repo.working_dir)

    def clone_or_open(self, repo_url: str, dir_name: Optional[str] = None) -> Path:
        """Clone the repo into workspace, or open if already present."""
        name = dir_name or repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        target = self.workspace / name

        if target.exists():
            try:
                self._repo = Repo(target)
                self._repo.git.fetch("--all")
                return target
            except InvalidGitRepositoryError:
                shutil.rmtree(target)

        self._repo = Repo.clone_from(repo_url, str(target))
        return target

    def open_local(self, repo_path: Path) -> Path:
        """Open an existing local repository."""
        self._repo = Repo(repo_path)
        return repo_path

    def checkout_branch(self, branch_name: str) -> None:
        """Checkout an existing remote or local branch."""
        repo = self.repo
        # Fetch latest
        try:
            repo.git.fetch("--all")
        except GitCommandError:
            pass

        # Try local branch first
        if branch_name in [h.name for h in repo.heads]:
            repo.heads[branch_name].checkout()
        else:
            # Create local tracking branch from remote
            repo.git.checkout("-b", branch_name, f"origin/{branch_name}")

    def checkout_pr(self, pr_id: str, remote: str = "origin") -> None:
        """Fetch and checkout a pull request by ID."""
        repo = self.repo
        local_branch = f"pr-{pr_id}"
        try:
            repo.git.fetch(remote, f"pull/{pr_id}/head:{local_branch}")
            repo.git.checkout(local_branch)
        except GitCommandError as e:
            raise RuntimeError(f"Failed to checkout PR {pr_id}: {e}") from e

    def create_fix_branch(self, base_branch: Optional[str] = None) -> str:
        """Create a new branch for fixes, based on current HEAD or specified branch."""
        repo = self.repo
        if base_branch:
            self.checkout_branch(base_branch)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        current = repo.active_branch.name
        fix_branch = f"sonarfix/{current}-{ts}"
        repo.git.checkout("-b", fix_branch)
        return fix_branch

    def commit_all(self, message: str) -> Optional[str]:
        """Stage all changes and commit. Returns commit SHA or None if nothing to commit."""
        repo = self.repo
        repo.git.add("-A")

        if not repo.is_dirty(untracked_files=True):
            return None

        cfg = get_settings()
        if cfg.git_user_name:
            repo.config_writer().set_value("user", "name", cfg.git_user_name).release()
        if cfg.git_user_email:
            repo.config_writer().set_value("user", "email", cfg.git_user_email).release()

        commit = repo.index.commit(message)
        return str(commit.hexsha)

    def push(self, remote: Optional[str] = None, branch: Optional[str] = None) -> None:
        """Push the current branch to remote."""
        repo = self.repo
        cfg = get_settings()
        remote_name = remote or cfg.git_push_remote
        branch_name = branch or repo.active_branch.name
        repo.git.push(remote_name, branch_name)

    def diff_stat(self) -> str:
        """Return git diff --stat for staged/unstaged changes."""
        repo = self.repo
        return repo.git.diff("--stat", "HEAD")

    def modified_files(self) -> list[str]:
        """Return list of modified file paths relative to repo root."""
        repo = self.repo
        diff = repo.git.diff("--name-only", "HEAD")
        if not diff.strip():
            return []
        return diff.strip().split("\n")
