"""SonarFix CLI — Typer application with full command suite."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from sonarfix.config import get_settings

app = typer.Typer(
    name="sonarfix",
    help="Auto-fix SonarQube issues using LLM-powered code generation.",
    no_args_is_help=True,
)
console = Console()


def _severity_badge(severity: str) -> str:
    colors = {
        "BLOCKER": "bold red",
        "CRITICAL": "red",
        "MAJOR": "yellow",
        "MINOR": "cyan",
        "INFO": "dim",
    }
    color = colors.get(severity, "white")
    return f"[{color}]{severity}[/]"


def _type_badge(issue_type: str) -> str:
    colors = {
        "BUG": "red",
        "VULNERABILITY": "magenta",
        "SECURITY_HOTSPOT": "magenta",
        "CODE_SMELL": "yellow",
    }
    color = colors.get(issue_type, "white")
    return f"[{color}]{issue_type}[/]"


# ------------------------------------------------------------------
# sonarfix issues
# ------------------------------------------------------------------

@app.command()
def issues(
    project_key: str = typer.Argument(..., help="SonarQube project key"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Branch name"),
    pr: Optional[str] = typer.Option(None, "--pr", help="Pull request ID"),
    severity: Optional[str] = typer.Option(
        None, "--severity", "-s",
        help="Comma-separated severities: BLOCKER,CRITICAL,MAJOR,MINOR,INFO",
    ),
    issue_type: Optional[str] = typer.Option(
        None, "--type", "-t",
        help="Comma-separated types: BUG,VULNERABILITY,CODE_SMELL,SECURITY_HOTSPOT",
    ),
    max_issues: int = typer.Option(100, "--max", "-m", help="Maximum issues to fetch"),
) -> None:
    """List SonarQube issues for a project/branch/PR (dry run)."""
    from sonarfix.sonarqube.client import SonarQubeClient
    from sonarfix.sonarqube.filters import filter_issues, group_by_file

    sq = SonarQubeClient()
    try:
        severities = [s.strip() for s in severity.split(",")] if severity else None
        types = [t.strip() for t in issue_type.split(",")] if issue_type else None

        console.print(f"[bold]Fetching issues for {project_key}...[/]")
        all_issues = sq.get_issues(
            project_key,
            branch=branch,
            pull_request=pr,
            severities=severities,
            types=types,
            max_issues=max_issues,
        )

        all_issues = filter_issues(all_issues, severities=severities, types=types)

        if not all_issues:
            console.print("[green]No issues found![/]")
            return

        # Summary table
        table = Table(title=f"SonarQube Issues ({len(all_issues)} total)")
        table.add_column("#", style="dim", width=4)
        table.add_column("Severity", width=10)
        table.add_column("Type", width=16)
        table.add_column("Rule", width=25)
        table.add_column("File", width=35)
        table.add_column("Line", width=6)
        table.add_column("Message", width=50)

        for idx, issue in enumerate(all_issues, 1):
            table.add_row(
                str(idx),
                _severity_badge(issue.severity),
                _type_badge(issue.type),
                issue.rule,
                issue.file_path,
                str(issue.start_line or "-"),
                issue.message[:50],
            )

        console.print(table)

        # Group summary
        groups = group_by_file(all_issues)
        console.print(f"\n[bold]Files affected: {len(groups)}[/]")
        for fp, file_issues in groups.items():
            console.print(f"  {fp}: {len(file_issues)} issue(s)")

    finally:
        sq.close()


# ------------------------------------------------------------------
# sonarfix run
# ------------------------------------------------------------------

@app.command()
def run(
    project_key: str = typer.Argument(..., help="SonarQube project key"),
    repo_url: str = typer.Option(..., "--repo", "-r", help="Git repository URL to clone"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Branch name"),
    pr: Optional[str] = typer.Option(None, "--pr", help="Pull request ID"),
    severity: Optional[str] = typer.Option(
        None, "--severity", "-s",
        help="Comma-separated severities: BLOCKER,CRITICAL,MAJOR,MINOR,INFO",
    ),
    issue_type: Optional[str] = typer.Option(
        None, "--type", "-t",
        help="Comma-separated types: BUG,VULNERABILITY,CODE_SMELL,SECURITY_HOTSPOT",
    ),
    max_issues: int = typer.Option(50, "--max", "-m", help="Maximum issues to fix"),
    auto_push: bool = typer.Option(False, "--auto-push", help="Push fix branch to remote"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate instructions without applying fixes"),
    local_repo: Optional[str] = typer.Option(
        None, "--local", "-l",
        help="Path to existing local repo (skip cloning)",
    ),
) -> None:
    """Full pipeline: fetch issues → clone repo → plan fixes → apply fixes → validate → report."""
    from sonarfix.sonarqube.client import SonarQubeClient
    from sonarfix.sonarqube.filters import filter_issues
    from sonarfix.git.manager import GitManager
    from sonarfix.fixer.planner import FixPlanner
    from sonarfix.fixer.windsurf import WindsurfDriver
    from sonarfix.validator.checker import (
        get_diff_stat,
        get_diff_summary,
        syntax_check_files,
    )
    from sonarfix.reporting.report import FixReport

    cfg = get_settings()
    sq = SonarQubeClient()
    report = FixReport(project_key, branch=branch, pull_request=pr)

    try:
        # --- Step 1: Fetch issues ---
        console.rule("[bold blue]Step 1: Fetch SonarQube Issues")
        severities = [s.strip() for s in severity.split(",")] if severity else None
        types = [t.strip() for t in issue_type.split(",")] if issue_type else None

        all_issues = sq.get_issues(
            project_key,
            branch=branch,
            pull_request=pr,
            severities=severities,
            types=types,
            max_issues=max_issues,
        )
        all_issues = filter_issues(all_issues, severities=severities, types=types)
        report.total_issues = len(all_issues)

        if not all_issues:
            console.print("[green]No issues found. Nothing to fix![/]")
            return

        console.print(f"[bold]Found {len(all_issues)} issues to fix.[/]")

        # --- Step 2: Clone/open repo ---
        console.rule("[bold blue]Step 2: Prepare Repository")
        git = GitManager()

        if local_repo:
            repo_dir = git.open_local(Path(local_repo))
            console.print(f"Using local repo: {repo_dir}")
        else:
            repo_dir = git.clone_or_open(repo_url)
            console.print(f"Repository ready at: {repo_dir}")

        if branch:
            git.checkout_branch(branch)
            console.print(f"Checked out branch: {branch}")
        elif pr:
            git.checkout_pr(pr)
            console.print(f"Checked out PR: {pr}")

        fix_branch = git.create_fix_branch(branch)
        report.fix_branch = fix_branch
        console.print(f"Created fix branch: [bold]{fix_branch}[/]")

        # --- Step 3: Plan fixes ---
        console.rule("[bold blue]Step 3: Plan Fixes")
        planner = FixPlanner(sq, repo_dir)
        plans = planner.plan(all_issues)

        if not plans:
            console.print("[yellow]No fix plans generated.[/]")
            return

        if dry_run:
            console.rule("[bold yellow]DRY RUN — Instructions Generated")
            for plan in plans:
                instr_path = repo_dir / f"fix-instructions-batch-{plan.batch_index}.txt"
                instr_path.write_text(plan.instructions_text, encoding="utf-8")
                console.print(
                    f"  Batch {plan.batch_index}: {plan.issue_count} issues, "
                    f"{len(plan.file_paths)} files → {instr_path}"
                )
            console.print("[yellow]Dry run complete. No fixes applied.[/]")
            return

        # --- Step 4: Apply Fixes ---
        console.rule("[bold blue]Step 4: Apply Fixes")
        driver = WindsurfDriver()
        results = driver.run_all(plans, repo_dir)
        report.batch_results = results

        total_fixed = sum(r.fixed_count for r in results)
        total_skipped = sum(r.skipped_count for r in results)
        console.print(
            f"\n[bold]Fix results: "
            f"[green]{total_fixed} fixed[/], "
            f"[yellow]{total_skipped} skipped[/][/]"
        )

        # --- Step 5: Validate ---
        console.rule("[bold blue]Step 5: Validate Fixes")
        modified = git.modified_files()
        if modified:
            console.print(f"Checking {len(modified)} modified files...")
            check_results = syntax_check_files(repo_dir, modified)
            report.check_results = check_results
        else:
            console.print("[yellow]No files were modified.[/]")

        # --- Step 6: Commit & Report ---
        console.rule("[bold blue]Step 6: Commit & Report")

        commit_sha = git.commit_all(
            f"sonarfix: auto-fix {total_fixed} SonarQube issues\n\n"
            f"Project: {project_key}\n"
            f"Branch: {branch or 'N/A'}\n"
            f"PR: {pr or 'N/A'}\n"
            f"Fixed: {total_fixed}, Skipped: {total_skipped}"
        )

        if commit_sha:
            report.commit_sha = commit_sha
            console.print(f"Committed: [bold]{commit_sha[:12]}[/]")

            if auto_push:
                try:
                    git.push()
                    console.print(f"[green]Pushed {fix_branch} to {cfg.git_push_remote}[/]")
                except Exception as e:
                    console.print(f"[red]Push failed: {e}[/]")
        else:
            console.print("[yellow]No changes to commit.[/]")

        report.diff_stat = get_diff_stat(repo_dir) if commit_sha else ""
        report.diff_summary = get_diff_summary(repo_dir) if commit_sha else {}
        report.finalize()

        # Write reports
        report_dir = repo_dir / ".sonarfix"
        json_path = report.write_json(report_dir)
        md_path = report.write_markdown(report_dir)
        console.print(f"\nReports written to:")
        console.print(f"  JSON: {json_path}")
        console.print(f"  Markdown: {md_path}")

        # Final summary
        console.rule("[bold green]Done")
        console.print(
            f"[bold]Fixed {total_fixed}/{report.total_issues} issues "
            f"on branch [cyan]{fix_branch}[/].[/]"
        )

    finally:
        sq.close()


# ------------------------------------------------------------------
# sonarfix validate
# ------------------------------------------------------------------

@app.command()
def validate(
    workspace: str = typer.Argument(..., help="Path to the repository workspace"),
) -> None:
    """Run syntax checks on modified files in a workspace."""
    from sonarfix.validator.checker import syntax_check_files

    repo_dir = Path(workspace)
    if not repo_dir.exists():
        console.print(f"[red]Directory not found: {workspace}[/]")
        raise typer.Exit(1)

    # Find modified files via git
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1"],
        capture_output=True, text=True, cwd=str(repo_dir),
    )
    files = [f for f in result.stdout.strip().split("\n") if f]

    if not files:
        console.print("[yellow]No modified files found.[/]")
        return

    console.print(f"[bold]Checking {len(files)} modified files...[/]")
    results = syntax_check_files(repo_dir, files)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    console.print(f"\n[bold]Results: [green]{passed} passed[/], [red]{failed} failed[/][/]")


# ------------------------------------------------------------------
# sonarfix branches
# ------------------------------------------------------------------

@app.command()
def branches(
    project_key: str = typer.Argument(..., help="SonarQube project key"),
) -> None:
    """List branches for a SonarQube project."""
    from sonarfix.sonarqube.client import SonarQubeClient

    sq = SonarQubeClient()
    try:
        branch_list = sq.get_branches(project_key)
        if not branch_list:
            console.print("[yellow]No branches found.[/]")
            return

        table = Table(title=f"Branches for {project_key}")
        table.add_column("Name", style="bold")
        table.add_column("Main", width=6)
        table.add_column("Type", width=12)

        for b in branch_list:
            table.add_row(b.name, "YES" if b.is_main else "", b.type)

        console.print(table)
    finally:
        sq.close()


# ------------------------------------------------------------------
# sonarfix prs
# ------------------------------------------------------------------

@app.command()
def prs(
    project_key: str = typer.Argument(..., help="SonarQube project key"),
) -> None:
    """List pull requests for a SonarQube project."""
    from sonarfix.sonarqube.client import SonarQubeClient

    sq = SonarQubeClient()
    try:
        pr_list = sq.get_pull_requests(project_key)
        if not pr_list:
            console.print("[yellow]No pull requests found.[/]")
            return

        table = Table(title=f"Pull Requests for {project_key}")
        table.add_column("Key", style="bold", width=8)
        table.add_column("Title", width=40)
        table.add_column("Branch", width=25)
        table.add_column("Base", width=15)

        for p in pr_list:
            table.add_row(p.key, p.title, p.branch, p.base)

        console.print(table)
    finally:
        sq.close()


if __name__ == "__main__":
    app()
