"""Tests for fix planner and prompt generation."""

import tempfile
from pathlib import Path

from sonarfix.fixer.prompt import (
    build_file_instruction,
    build_issue_block,
    build_fix_instructions,
    read_file_context,
)
from sonarfix.sonarqube.models import Issue, Rule


def _issue(
    rule: str = "python:S1234",
    severity: str = "MAJOR",
    component: str = "proj:src/main.py",
    line: int = 10,
    message: str = "Remove this unused import.",
    issue_type: str = "CODE_SMELL",
) -> Issue:
    return Issue(
        key="test-key",
        rule=rule,
        severity=severity,
        component=component,
        line=line,
        status="OPEN",
        message=message,
        type=issue_type,
    )


def _rule(key: str = "python:S1234") -> Rule:
    return Rule(
        key=key,
        name="Unused imports should be removed",
        severity="MAJOR",
        type="CODE_SMELL",
    )


class TestReadFileContext:
    def test_reads_context(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            lines = [f"line {i}" for i in range(1, 31)]
            p.write_text("\n".join(lines))

            ctx = read_file_context(Path(td), "test.py", 15, 15)
            assert "line 15" in ctx
            assert "line 5" in ctx  # context window
            assert "line 25" in ctx

    def test_file_level_issue(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("import os\nimport sys\n")

            ctx = read_file_context(Path(td), "test.py", None, None)
            assert "import os" in ctx

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = read_file_context(Path(td), "nonexistent.py", 1, 1)
            assert "not found" in ctx.lower()


class TestBuildIssueBlock:
    def test_basic_block(self):
        block = build_issue_block(_issue())
        assert "python:S1234" in block
        assert "MAJOR" in block
        assert "Remove this unused import" in block

    def test_with_rule_description(self):
        rule = Rule(
            key="python:S1234",
            name="Unused imports",
            htmlDesc="<p>Remove <b>unused</b> imports.</p>",
            severity="MAJOR",
            type="CODE_SMELL",
        )
        block = build_issue_block(_issue(), rule=rule)
        assert "Remove" in block
        assert "unused" in block
        # HTML tags should be stripped
        assert "<p>" not in block


class TestBuildFileInstruction:
    def test_generates_instruction(self):
        issues = [_issue(line=10), _issue(line=20, message="Fix naming.")]
        rules = {"python:S1234": _rule()}
        instr = build_file_instruction("src/main.py", issues, rules)
        assert "src/main.py" in instr
        assert "Issue 1" in instr
        assert "Issue 2" in instr


class TestBuildFixInstructions:
    def test_full_instructions(self):
        file_instrs = [
            "## Fix issues in `src/a.py`\n### Issue 1\n- Fix something",
            "## Fix issues in `src/b.py`\n### Issue 1\n- Fix other",
        ]
        with tempfile.TemporaryDirectory() as td:
            text = build_fix_instructions(file_instrs, Path(td))
            assert "WORK-COMPLETED" in text
            assert "src/a.py" in text
            assert "src/b.py" in text
            assert "Edit ONLY the files listed below" in text
