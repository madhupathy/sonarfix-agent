"""Tests for SonarQube Pydantic models."""

from sonarfix.sonarqube.models import (
    Issue,
    IssuesSearchResponse,
    Paging,
    Rule,
    RuleShowResponse,
    TextRange,
)


def _sample_issue_dict(**overrides) -> dict:
    base = {
        "key": "abc-123",
        "rule": "python:S1234",
        "severity": "MAJOR",
        "component": "myproject:src/main.py",
        "project": "myproject",
        "line": 42,
        "textRange": {
            "startLine": 42,
            "endLine": 44,
            "startOffset": 0,
            "endOffset": 10,
        },
        "flows": [],
        "status": "OPEN",
        "message": "Remove this unused import.",
        "type": "CODE_SMELL",
        "tags": ["convention"],
        "creationDate": "2025-01-01T00:00:00+0000",
        "updateDate": "2025-01-01T00:00:00+0000",
    }
    base.update(overrides)
    return base


class TestIssueModel:
    def test_parse_basic(self):
        data = _sample_issue_dict()
        issue = Issue.model_validate(data)
        assert issue.key == "abc-123"
        assert issue.rule == "python:S1234"
        assert issue.severity == "MAJOR"
        assert issue.line == 42
        assert issue.message == "Remove this unused import."

    def test_file_path_extraction(self):
        issue = Issue.model_validate(_sample_issue_dict())
        assert issue.file_path == "src/main.py"

    def test_file_path_no_colon(self):
        issue = Issue.model_validate(_sample_issue_dict(component="standalone_file.py"))
        assert issue.file_path == "standalone_file.py"

    def test_start_end_line_from_text_range(self):
        issue = Issue.model_validate(_sample_issue_dict(line=None))
        assert issue.start_line == 42
        assert issue.end_line == 44

    def test_start_end_line_from_line_field(self):
        data = _sample_issue_dict()
        del data["textRange"]
        issue = Issue.model_validate(data)
        assert issue.start_line == 42
        assert issue.end_line == 42

    def test_no_line_info(self):
        data = _sample_issue_dict(line=None)
        del data["textRange"]
        issue = Issue.model_validate(data)
        assert issue.start_line is None
        assert issue.end_line is None

    def test_text_range_parsed(self):
        issue = Issue.model_validate(_sample_issue_dict())
        assert issue.text_range is not None
        assert issue.text_range.start_line == 42
        assert issue.text_range.end_line == 44


class TestIssuesSearchResponse:
    def test_parse_response(self):
        data = {
            "paging": {"pageIndex": 1, "pageSize": 100, "total": 1},
            "issues": [_sample_issue_dict()],
            "components": [{"key": "myproject:src/main.py", "path": "src/main.py"}],
            "rules": [],
        }
        resp = IssuesSearchResponse.model_validate(data)
        assert resp.paging.total == 1
        assert len(resp.issues) == 1
        assert resp.issues[0].key == "abc-123"

    def test_empty_response(self):
        data = {"paging": {"pageIndex": 1, "pageSize": 100, "total": 0}, "issues": []}
        resp = IssuesSearchResponse.model_validate(data)
        assert len(resp.issues) == 0


class TestRuleModel:
    def test_parse_rule(self):
        data = {
            "key": "python:S1234",
            "name": "Unused imports should be removed",
            "htmlDesc": "<p>Remove unused imports.</p>",
            "severity": "MAJOR",
            "type": "CODE_SMELL",
            "lang": "py",
            "langName": "Python",
        }
        rule = Rule.model_validate(data)
        assert rule.key == "python:S1234"
        assert rule.html_desc == "<p>Remove unused imports.</p>"
        assert rule.lang == "py"

    def test_rule_show_response(self):
        data = {
            "rule": {
                "key": "python:S1234",
                "name": "Unused imports",
                "severity": "MAJOR",
                "type": "CODE_SMELL",
            }
        }
        resp = RuleShowResponse.model_validate(data)
        assert resp.rule.key == "python:S1234"
