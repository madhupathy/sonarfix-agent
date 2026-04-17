"""Tests for fix report generation."""

import json
import tempfile
from pathlib import Path

from sonarfix.fixer.windsurf import WindsurfResult
from sonarfix.reporting.report import FixReport
from sonarfix.validator.checker import CheckResult


class TestFixReport:
    def _make_report(self) -> FixReport:
        report = FixReport("myproject", branch="main")
        report.total_issues = 5
        report.fix_branch = "sonarfix/main-20250101"
        report.commit_sha = "abc123def456"
        report.batch_results = [
            WindsurfResult(
                success=True,
                output_log=(
                    "[T] FIXED a.py - Issue 1: x\n"
                    "[T] FIXED a.py - Issue 2: y\n"
                    "[T] SKIPPED b.py - Issue 1: z\n"
                    "[T] WORK-COMPLETED\n"
                ),
                batch_index=0,
            ),
        ]
        report.check_results = [
            CheckResult("a.py", True),
            CheckResult("b.py", False, "syntax error line 5"),
        ]
        report.diff_summary = {"files_changed": 2, "insertions": 10, "deletions": 3}
        report.finalize()
        return report

    def test_to_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert d["project_key"] == "myproject"
        assert d["fixed_count"] == 2
        assert d["skipped_count"] == 1
        assert d["syntax_checks_passed"] == 1
        assert d["syntax_checks_failed"] == 1

    def test_write_json(self):
        report = self._make_report()
        with tempfile.TemporaryDirectory() as td:
            path = report.write_json(Path(td))
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["fixed_count"] == 2

    def test_write_markdown(self):
        report = self._make_report()
        with tempfile.TemporaryDirectory() as td:
            path = report.write_markdown(Path(td))
            assert path.exists()
            content = path.read_text()
            assert "# SonarFix Report" in content
            assert "myproject" in content
            assert "sonarfix/main-20250101" in content
            assert "syntax error" in content
