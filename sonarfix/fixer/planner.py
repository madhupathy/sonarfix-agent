"""Fix planner — groups issues, enriches with rule data, builds instruction batches."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

from sonarfix.sonarqube.client import SonarQubeClient
from sonarfix.sonarqube.filters import deduplicate, group_by_file, sort_by_severity
from sonarfix.sonarqube.models import Issue, Rule
from sonarfix.fixer.prompt import build_file_instruction, build_fix_instructions

console = Console()

MAX_ISSUES_PER_BATCH = 20  # avoid overwhelming the fixer with too many issues at once


class FixPlan:
    """Represents a batch of file instructions ready for the fixer."""

    def __init__(
        self,
        instructions_text: str,
        file_paths: list[str],
        issue_count: int,
        batch_index: int = 0,
    ):
        self.instructions_text = instructions_text
        self.file_paths = file_paths
        self.issue_count = issue_count
        self.batch_index = batch_index

    def __repr__(self) -> str:
        return (
            f"FixPlan(batch={self.batch_index}, "
            f"files={len(self.file_paths)}, issues={self.issue_count})"
        )


class FixPlanner:
    def __init__(
        self,
        sq_client: SonarQubeClient,
        repo_dir: Path,
        max_per_batch: int = MAX_ISSUES_PER_BATCH,
    ):
        self.sq_client = sq_client
        self.repo_dir = repo_dir
        self.max_per_batch = max_per_batch
        self._rule_cache: dict[str, Rule] = {}

    def _fetch_rule(self, rule_key: str) -> Optional[Rule]:
        if rule_key in self._rule_cache:
            return self._rule_cache[rule_key]
        try:
            rule = self.sq_client.get_rule(rule_key)
            self._rule_cache[rule_key] = rule
            return rule
        except Exception as e:
            console.print(f"[yellow]Warning: could not fetch rule {rule_key}: {e}[/]")
            return None

    def _enrich_rules(self, issues: list[Issue]) -> dict[str, Rule]:
        """Fetch rule details for all unique rules in the issue list."""
        rules: dict[str, Rule] = {}
        unique_keys = {i.rule for i in issues}
        for key in unique_keys:
            rule = self._fetch_rule(key)
            if rule:
                rules[key] = rule
        return rules

    def plan(self, issues: list[Issue]) -> list[FixPlan]:
        """Build fix plans (batched instruction sets) from a list of issues."""
        issues = deduplicate(sort_by_severity(issues))
        if not issues:
            return []

        console.print(f"[bold]Planning fixes for {len(issues)} issues...[/]")

        # Enrich with rule metadata
        rules = self._enrich_rules(issues)

        # Group by file
        file_groups = group_by_file(issues)

        # Build batches
        batches: list[FixPlan] = []
        current_files: list[str] = []
        current_instructions: list[str] = []
        current_count = 0
        batch_idx = 0

        for file_path, file_issues in file_groups.items():
            instr = build_file_instruction(file_path, file_issues, rules, self.repo_dir)
            current_files.append(file_path)
            current_instructions.append(instr)
            current_count += len(file_issues)

            if current_count >= self.max_per_batch:
                text = build_fix_instructions(current_instructions, self.repo_dir)
                batches.append(
                    FixPlan(
                        instructions_text=text,
                        file_paths=list(current_files),
                        issue_count=current_count,
                        batch_index=batch_idx,
                    )
                )
                current_files = []
                current_instructions = []
                current_count = 0
                batch_idx += 1

        # Remaining
        if current_instructions:
            text = build_fix_instructions(current_instructions, self.repo_dir)
            batches.append(
                FixPlan(
                    instructions_text=text,
                    file_paths=list(current_files),
                    issue_count=current_count,
                    batch_index=batch_idx,
                )
            )

        console.print(
            f"[green]Created {len(batches)} batch(es) "
            f"covering {sum(b.issue_count for b in batches)} issues.[/]"
        )
        return batches
