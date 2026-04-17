"""Filter, group, and rank SonarQube issues for fix planning."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from sonarfix.sonarqube.models import Issue

SEVERITY_ORDER = {
    "BLOCKER": 0,
    "CRITICAL": 1,
    "MAJOR": 2,
    "MINOR": 3,
    "INFO": 4,
}

TYPE_ORDER = {
    "BUG": 0,
    "VULNERABILITY": 1,
    "SECURITY_HOTSPOT": 2,
    "CODE_SMELL": 3,
}


def sort_by_severity(issues: list[Issue]) -> list[Issue]:
    """Sort issues highest-severity-first, then by type priority."""
    return sorted(
        issues,
        key=lambda i: (
            SEVERITY_ORDER.get(i.severity, 99),
            TYPE_ORDER.get(i.type, 99),
            i.start_line or 0,
        ),
    )


def group_by_file(issues: list[Issue]) -> dict[str, list[Issue]]:
    """Group issues by their file path, sorted by severity within each group."""
    groups: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        groups[issue.file_path].append(issue)
    return {fp: sort_by_severity(group) for fp, group in groups.items()}


def deduplicate(issues: list[Issue]) -> list[Issue]:
    """Remove duplicate issues (same rule + same file + same line)."""
    seen: set[tuple[str, str, int | None]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (issue.rule, issue.file_path, issue.start_line)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def filter_issues(
    issues: list[Issue],
    severities: list[str] | None = None,
    types: list[str] | None = None,
) -> list[Issue]:
    """Filter issues by severity and/or type."""
    result = issues
    if severities:
        sev_set = {s.upper() for s in severities}
        result = [i for i in result if i.severity in sev_set]
    if types:
        type_set = {t.upper() for t in types}
        result = [i for i in result if i.type in type_set]
    return result
