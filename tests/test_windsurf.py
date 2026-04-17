"""Tests for Windsurf driver result parsing and output polling logic."""

from sonarfix.fixer.windsurf import WindsurfResult


class TestWindsurfResult:
    def test_fixed_count(self):
        log = (
            "[2025-01-01T00:00:00Z] FIXED src/a.py - Issue 1: removed import\n"
            "[2025-01-01T00:00:01Z] FIXED src/a.py - Issue 2: renamed var\n"
            "[2025-01-01T00:00:02Z] SKIPPED src/b.py - Issue 1: ambiguous\n"
            "[2025-01-01T00:00:03Z] WORK-COMPLETED\n"
        )
        result = WindsurfResult(success=True, output_log=log, batch_index=0)
        assert result.fixed_count == 2
        assert result.skipped_count == 1

    def test_empty_log(self):
        result = WindsurfResult(success=False, output_log="", batch_index=0)
        assert result.fixed_count == 0
        assert result.skipped_count == 0

    def test_timed_out(self):
        result = WindsurfResult(
            success=False, output_log="partial", timed_out=True, batch_index=1
        )
        assert result.timed_out is True
        assert result.batch_index == 1
