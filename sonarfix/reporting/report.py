"""Fix reporting — generate JSON and Markdown summaries of the fix run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sonarfix.fixer.windsurf import WindsurfResult
from sonarfix.validator.checker import CheckResult


class FixReport:
    """Collects data and generates fix reports."""

    def __init__(
        self,
        project_key: str,
        branch: Optional[str] = None,
        pull_request: Optional[str] = None,
    ):
        self.project_key = project_key
        self.branch = branch
        self.pull_request = pull_request
        self.total_issues: int = 0
        self.batch_results: list[WindsurfResult] = []
        self.check_results: list[CheckResult] = []
        self.fix_branch: Optional[str] = None
        self.commit_sha: Optional[str] = None
        self.diff_stat: str = ""
        self.diff_summary: dict[str, int] = {}
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.finished_at: Optional[str] = None

    def finalize(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    @property
    def fixed_count(self) -> int:
        return sum(r.fixed_count for r in self.batch_results)

    @property
    def skipped_count(self) -> int:
        return sum(r.skipped_count for r in self.batch_results)

    @property
    def batches_succeeded(self) -> int:
        return sum(1 for r in self.batch_results if r.success)

    @property
    def batches_failed(self) -> int:
        return sum(1 for r in self.batch_results if not r.success)

    @property
    def syntax_pass_count(self) -> int:
        return sum(1 for c in self.check_results if c.passed)

    @property
    def syntax_fail_count(self) -> int:
        return sum(1 for c in self.check_results if not c.passed)

    def to_dict(self) -> dict:
        return {
            "project_key": self.project_key,
            "branch": self.branch,
            "pull_request": self.pull_request,
            "fix_branch": self.fix_branch,
            "commit_sha": self.commit_sha,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_issues": self.total_issues,
            "fixed_count": self.fixed_count,
            "skipped_count": self.skipped_count,
            "batches_total": len(self.batch_results),
            "batches_succeeded": self.batches_succeeded,
            "batches_failed": self.batches_failed,
            "syntax_checks_passed": self.syntax_pass_count,
            "syntax_checks_failed": self.syntax_fail_count,
            "diff_summary": self.diff_summary,
        }

    def write_json(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "fix-report.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def write_markdown(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "fix-report.md"

        lines = [
            f"# SonarFix Report",
            "",
            f"**Project**: `{self.project_key}`",
        ]
        if self.branch:
            lines.append(f"**Branch**: `{self.branch}`")
        if self.pull_request:
            lines.append(f"**Pull Request**: `{self.pull_request}`")
        if self.fix_branch:
            lines.append(f"**Fix Branch**: `{self.fix_branch}`")
        if self.commit_sha:
            lines.append(f"**Commit**: `{self.commit_sha[:12]}`")

        lines.extend([
            f"**Started**: {self.started_at}",
            f"**Finished**: {self.finished_at or 'N/A'}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total issues | {self.total_issues} |",
            f"| Fixed | {self.fixed_count} |",
            f"| Skipped | {self.skipped_count} |",
            f"| Batches succeeded | {self.batches_succeeded} |",
            f"| Batches failed | {self.batches_failed} |",
            f"| Syntax checks passed | {self.syntax_pass_count} |",
            f"| Syntax checks failed | {self.syntax_fail_count} |",
        ])

        if self.diff_summary:
            lines.extend([
                "",
                "## Diff Summary",
                "",
                f"- **Files changed**: {self.diff_summary.get('files_changed', 0)}",
                f"- **Insertions**: {self.diff_summary.get('insertions', 0)}",
                f"- **Deletions**: {self.diff_summary.get('deletions', 0)}",
            ])

        if self.diff_stat:
            lines.extend([
                "",
                "## Diff Stat",
                "",
                "```",
                self.diff_stat,
                "```",
            ])

        # Batch details
        if self.batch_results:
            lines.extend(["", "## Batch Details", ""])
            for wr in self.batch_results:
                status = "SUCCESS" if wr.success else ("TIMEOUT" if wr.timed_out else "FAILED")
                lines.append(f"### Batch {wr.batch_index} — {status}")
                lines.append("")
                lines.append(f"- Fixed: {wr.fixed_count}")
                lines.append(f"- Skipped: {wr.skipped_count}")
                if wr.output_log:
                    lines.extend(["", "```", wr.output_log.strip(), "```"])
                lines.append("")

        # Syntax check failures
        failures = [c for c in self.check_results if not c.passed]
        if failures:
            lines.extend(["", "## Syntax Check Failures", ""])
            for c in failures:
                lines.append(f"- **{c.file_path}**: {c.output[:200]}")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path
