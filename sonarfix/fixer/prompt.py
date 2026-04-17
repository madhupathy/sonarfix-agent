"""Prompt templates for automated fix instructions."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from sonarfix.sonarqube.models import Issue, Rule

CONTEXT_LINES = 10  # lines of surrounding context to include


def read_file_context(
    repo_dir: Path, file_path: str, start_line: Optional[int], end_line: Optional[int]
) -> str:
    """Read surrounding lines from a file for context."""
    full_path = repo_dir / file_path
    if not full_path.exists():
        return f"[File not found: {file_path}]"

    try:
        lines = full_path.read_text(errors="replace").splitlines()
    except Exception:
        return f"[Could not read: {file_path}]"

    if start_line is None:
        # File-level issue — show first 30 lines
        snippet = lines[:30]
        return _numbered(snippet, 1)

    ctx_start = max(0, start_line - 1 - CONTEXT_LINES)
    ctx_end = min(len(lines), (end_line or start_line) + CONTEXT_LINES)
    snippet = lines[ctx_start:ctx_end]
    return _numbered(snippet, ctx_start + 1)


def _numbered(lines: list[str], start: int) -> str:
    width = len(str(start + len(lines)))
    return "\n".join(f"{str(i).rjust(width)}| {line}" for i, line in enumerate(lines, start))


def build_issue_block(
    issue: Issue,
    rule: Optional[Rule] = None,
    repo_dir: Optional[Path] = None,
) -> str:
    """Build a description block for a single issue."""
    parts = [
        f"- **Rule**: `{issue.rule}`",
        f"- **Severity**: {issue.severity}",
        f"- **Type**: {issue.type}",
        f"- **Message**: {issue.message}",
    ]
    if issue.start_line:
        loc = f"line {issue.start_line}"
        if issue.end_line and issue.end_line != issue.start_line:
            loc += f"-{issue.end_line}"
        parts.append(f"- **Location**: {loc}")

    if rule and rule.html_desc:
        # Strip HTML tags for a plain-text summary
        import re
        desc = re.sub(r"<[^>]+>", "", rule.html_desc)
        desc = desc.strip()[:500]
        parts.append(f"- **Rule description**: {desc}")

    if repo_dir:
        ctx = read_file_context(repo_dir, issue.file_path, issue.start_line, issue.end_line)
        parts.append(f"- **Code context**:\n```\n{ctx}\n```")

    return "\n".join(parts)


def build_file_instruction(
    file_path: str,
    issues: list[Issue],
    rules: dict[str, Rule],
    repo_dir: Optional[Path] = None,
) -> str:
    """Build an instruction block for all issues in a single file."""
    header = f"## Fix issues in `{file_path}`\n"
    blocks = []
    for idx, issue in enumerate(issues, 1):
        rule = rules.get(issue.rule)
        block = f"### Issue {idx}\n{build_issue_block(issue, rule, repo_dir)}"
        blocks.append(block)

    return header + "\n\n".join(blocks)


def build_fix_instructions(
    file_instructions: list[str],
    repo_dir: Path,
) -> str:
    """Build the complete fix instructions content."""
    preamble = f"""You are fixing SonarQube issues in the repository at {repo_dir}.

RULES:
1. Edit ONLY the files listed below. Do not create new files.
2. Fix each issue according to its rule description and message.
3. Preserve existing functionality — do not change logic beyond what the fix requires.
4. Maintain the existing code style (indentation, naming conventions).
5. If a fix is ambiguous or risky, add a TODO comment instead of guessing.
6. After each file edit, append a line to windsurf-output.txt with format:
   [TIMESTAMP] FIXED <file_path> - Issue <N>: <brief description>
7. If you cannot fix an issue, append:
   [TIMESTAMP] SKIPPED <file_path> - Issue <N>: <reason>
8. When all issues are processed, append a final line:
   [TIMESTAMP] WORK-COMPLETED

BEGIN ISSUES:
"""
    body = "\n\n---\n\n".join(file_instructions)
    return preamble + body
