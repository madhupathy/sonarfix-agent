"""Validation: syntax checks, diff summary, optional re-scan."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()

# Map file extensions to syntax-check commands
SYNTAX_CHECKERS: dict[str, list[str]] = {
    ".py": ["python", "-m", "py_compile"],
    ".sh": ["bash", "-n"],
    ".bash": ["bash", "-n"],
    ".go": ["go", "vet"],
    ".js": ["node", "--check"],
    ".ts": ["node", "--check"],
    ".java": ["javac", "-d", "/tmp/sonarfix-javac"],
    ".rb": ["ruby", "-c"],
    ".php": ["php", "-l"],
}


class CheckResult:
    def __init__(self, file_path: str, passed: bool, output: str = ""):
        self.file_path = file_path
        self.passed = passed
        self.output = output

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"CheckResult({self.file_path}: {status})"


def syntax_check_file(repo_dir: Path, rel_path: str) -> CheckResult:
    """Run a language-specific syntax check on a single file."""
    full_path = repo_dir / rel_path
    if not full_path.exists():
        return CheckResult(rel_path, False, "File not found")

    ext = full_path.suffix.lower()
    checker = SYNTAX_CHECKERS.get(ext)

    if checker is None:
        return CheckResult(rel_path, True, "No syntax checker available; skipped")

    cmd = checker + [str(full_path)]

    # Special handling for go vet (needs directory, not file)
    if ext == ".go":
        cmd = ["go", "vet", str(full_path.parent)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=str(repo_dir)
        )
        passed = result.returncode == 0
        output = result.stdout + result.stderr
        return CheckResult(rel_path, passed, output.strip())
    except FileNotFoundError:
        return CheckResult(rel_path, True, f"Checker '{checker[0]}' not installed; skipped")
    except subprocess.TimeoutExpired:
        return CheckResult(rel_path, False, "Syntax check timed out")


def syntax_check_files(repo_dir: Path, file_paths: list[str]) -> list[CheckResult]:
    """Run syntax checks on a list of files."""
    results: list[CheckResult] = []
    for fp in file_paths:
        result = syntax_check_file(repo_dir, fp)
        results.append(result)
        icon = "[green]PASS[/]" if result.passed else "[red]FAIL[/]"
        console.print(f"  {icon} {fp}")
        if not result.passed and result.output:
            console.print(f"       {result.output[:200]}")
    return results


def get_diff_stat(repo_dir: Path) -> str:
    """Run git diff --stat on the repo."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD~1"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_dir),
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_diff_summary(repo_dir: Path) -> dict[str, int]:
    """Get a summary of changes: files changed, insertions, deletions."""
    try:
        result = subprocess.run(
            ["git", "diff", "--shortstat", "HEAD~1"],
            capture_output=True, text=True, timeout=10, cwd=str(repo_dir),
        )
        text = result.stdout.strip()
        summary: dict[str, int] = {"files_changed": 0, "insertions": 0, "deletions": 0}
        if not text:
            return summary

        import re
        m = re.search(r"(\d+) files? changed", text)
        if m:
            summary["files_changed"] = int(m.group(1))
        m = re.search(r"(\d+) insertions?", text)
        if m:
            summary["insertions"] = int(m.group(1))
        m = re.search(r"(\d+) deletions?", text)
        if m:
            summary["deletions"] = int(m.group(1))
        return summary
    except Exception:
        return {"files_changed": 0, "insertions": 0, "deletions": 0}


def run_shellcheck(repo_dir: Path, file_path: str) -> Optional[CheckResult]:
    """Run shellcheck on a shell script if available."""
    full_path = repo_dir / file_path
    if not full_path.exists():
        return None
    if full_path.suffix.lower() not in (".sh", ".bash"):
        return None

    try:
        result = subprocess.run(
            ["shellcheck", str(full_path)],
            capture_output=True, text=True, timeout=30,
        )
        passed = result.returncode == 0
        return CheckResult(file_path, passed, (result.stdout + result.stderr).strip())
    except FileNotFoundError:
        return None
