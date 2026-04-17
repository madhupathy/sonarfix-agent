"""Tests for issue filtering, grouping, and deduplication."""

from typing import Optional

from sonarfix.sonarqube.filters import (
    deduplicate,
    filter_issues,
    group_by_file,
    sort_by_severity,
)
from sonarfix.sonarqube.models import Issue


def _issue(
    severity: str = "MAJOR",
    issue_type: str = "CODE_SMELL",
    rule: str = "python:S1234",
    component: str = "proj:src/main.py",
    line: Optional[int] = 10,
    message: str = "Fix this",
) -> Issue:
    return Issue(
        key=f"key-{severity}-{line}-{rule}",
        rule=rule,
        severity=severity,
        component=component,
        line=line,
        status="OPEN",
        message=message,
        type=issue_type,
    )


class TestSortBySeverity:
    def test_severity_order(self):
        issues = [
            _issue(severity="MINOR"),
            _issue(severity="BLOCKER"),
            _issue(severity="MAJOR"),
            _issue(severity="CRITICAL"),
            _issue(severity="INFO"),
        ]
        sorted_issues = sort_by_severity(issues)
        severities = [i.severity for i in sorted_issues]
        assert severities == ["BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"]

    def test_secondary_sort_by_type(self):
        issues = [
            _issue(severity="MAJOR", issue_type="CODE_SMELL"),
            _issue(severity="MAJOR", issue_type="BUG"),
        ]
        sorted_issues = sort_by_severity(issues)
        types = [i.type for i in sorted_issues]
        assert types == ["BUG", "CODE_SMELL"]


class TestGroupByFile:
    def test_groups_by_file(self):
        issues = [
            _issue(component="proj:a.py", line=1),
            _issue(component="proj:b.py", line=5),
            _issue(component="proj:a.py", line=10),
        ]
        groups = group_by_file(issues)
        assert set(groups.keys()) == {"a.py", "b.py"}
        assert len(groups["a.py"]) == 2
        assert len(groups["b.py"]) == 1

    def test_sorted_within_groups(self):
        issues = [
            _issue(component="proj:a.py", severity="MINOR", line=5),
            _issue(component="proj:a.py", severity="BLOCKER", line=1),
        ]
        groups = group_by_file(issues)
        assert groups["a.py"][0].severity == "BLOCKER"


class TestDeduplicate:
    def test_removes_duplicates(self):
        issues = [
            _issue(rule="python:S1", component="proj:a.py", line=10),
            _issue(rule="python:S1", component="proj:a.py", line=10),
            _issue(rule="python:S2", component="proj:a.py", line=10),
        ]
        unique = deduplicate(issues)
        assert len(unique) == 2

    def test_keeps_different_lines(self):
        issues = [
            _issue(rule="python:S1", component="proj:a.py", line=10),
            _issue(rule="python:S1", component="proj:a.py", line=20),
        ]
        unique = deduplicate(issues)
        assert len(unique) == 2


class TestFilterIssues:
    def test_filter_by_severity(self):
        issues = [
            _issue(severity="BLOCKER"),
            _issue(severity="MINOR"),
            _issue(severity="INFO"),
        ]
        result = filter_issues(issues, severities=["BLOCKER"])
        assert len(result) == 1
        assert result[0].severity == "BLOCKER"

    def test_filter_by_type(self):
        issues = [
            _issue(issue_type="BUG"),
            _issue(issue_type="CODE_SMELL"),
        ]
        result = filter_issues(issues, types=["BUG"])
        assert len(result) == 1
        assert result[0].type == "BUG"

    def test_filter_combined(self):
        issues = [
            _issue(severity="BLOCKER", issue_type="BUG"),
            _issue(severity="BLOCKER", issue_type="CODE_SMELL"),
            _issue(severity="MINOR", issue_type="BUG"),
        ]
        result = filter_issues(issues, severities=["BLOCKER"], types=["BUG"])
        assert len(result) == 1

    def test_no_filter(self):
        issues = [_issue(), _issue(severity="MINOR")]
        result = filter_issues(issues)
        assert len(result) == 2
