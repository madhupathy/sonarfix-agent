"""Tests for the FixGraph state machine."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

from sonarfix.fixer.graph import (
    FileFixState,
    FixGraph,
    FixStage,
    node_extract_context,
    node_build_prompt,
    node_apply_fix,
    node_retry,
    MAX_RETRIES,
)
from sonarfix.sonarqube.models import Issue, Rule


def _make_issue(
    rule: str = "python:S1234",
    severity: str = "MAJOR",
    msg: str = "Fix this",
    line: int = 5,
    file_path: str = "test.py",
) -> Issue:
    return Issue(
        key="issue-1",
        rule=rule,
        severity=severity,
        component=f"proj:{file_path}",
        message=msg,
        line=line,
        type="CODE_SMELL",
    )


def _make_rule(key: str = "python:S1234") -> Rule:
    return Rule(
        key=key,
        name="Test Rule",
        htmlDesc="<p>You should fix this thing.</p>",
        severity="MAJOR",
        type="CODE_SMELL",
    )


class TestNodeExtractContext:
    def test_file_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            state = FileFixState(
                file_path="nonexistent.py",
                issues=[_make_issue()],
                rules={},
                repo_dir=Path(td),
            )
            state = node_extract_context(state)
            assert state.stage == FixStage.FAILED
            assert "not found" in state.error.lower()

    def test_extracts_small_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("import os\n\ndef foo():\n    x = 1\n    return x\n")
            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue()],
                rules={},
                repo_dir=Path(td),
            )
            state = node_extract_context(state)
            assert state.stage == FixStage.RETRIEVE_RAG
            assert state.context is not None
            assert not state.context.is_chunked

    def test_extracts_large_file_chunked(self):
        with tempfile.TemporaryDirectory() as td:
            lines = ["import os", "import sys", ""]
            for i in range(3000):
                lines.extend([f"def func_{i}():", f"    x_{i} = {i} * 2 + 1  # computation", f"    return x_{i}", ""])
            p = Path(td) / "big.py"
            content = "\n".join(lines)
            assert len(content) > 42000
            p.write_text(content)
            state = FileFixState(
                file_path="big.py",
                issues=[_make_issue(line=100)],
                rules={},
                repo_dir=Path(td),
            )
            state = node_extract_context(state)
            assert state.stage == FixStage.RETRIEVE_RAG
            assert state.context is not None
            assert state.context.is_chunked


class TestNodeBuildPrompt:
    def test_builds_full_prompt(self):
        from sonarfix.fixer.context_extractor import ExtractedContext
        state = FileFixState(
            file_path="test.py",
            issues=[_make_issue()],
            rules={"python:S1234": _make_rule()},
            repo_dir=Path("/tmp"),
        )
        state.context = ExtractedContext(
            file_path="test.py",
            is_chunked=False,
            full_content="def foo():\n    pass\n",
        )
        state = node_build_prompt(state)
        assert state.stage == FixStage.CALL_LLM
        assert "python:S1234" in state.user_prompt
        assert "Fix this" in state.user_prompt
        assert "def foo" in state.user_prompt

    def test_builds_chunked_prompt(self):
        from sonarfix.fixer.context_extractor import ExtractedContext, CodeRegion
        state = FileFixState(
            file_path="test.py",
            issues=[_make_issue()],
            rules={"python:S1234": _make_rule()},
            repo_dir=Path("/tmp"),
        )
        state.context = ExtractedContext(
            file_path="test.py",
            is_chunked=True,
            total_lines=500,
            regions=[
                CodeRegion(1, 5, "import os", "imports/header"),
                CodeRegion(50, 60, "def foo():\n    pass", "function: foo"),
            ],
        )
        state = node_build_prompt(state)
        assert state.stage == FixStage.CALL_LLM
        assert "Lines" in state.system_prompt or "Lines" in state.user_prompt

    def test_retry_adds_feedback(self):
        from sonarfix.fixer.context_extractor import ExtractedContext
        state = FileFixState(
            file_path="test.py",
            issues=[_make_issue()],
            rules={},
            repo_dir=Path("/tmp"),
        )
        state.context = ExtractedContext(
            file_path="test.py", is_chunked=False, full_content="pass",
        )
        state.attempt = 1
        state.error_history = ["Syntax check failed: unexpected indent"]
        state = node_build_prompt(state)
        assert "unexpected indent" in state.system_prompt


class TestNodeApplyFix:
    def test_empty_response(self):
        state = FileFixState(
            file_path="test.py",
            issues=[_make_issue()],
            rules={},
            repo_dir=Path("/tmp"),
        )
        state.llm_response = ""
        state.original_content = "original"
        state = node_apply_fix(state)
        # Should retry or fail
        assert state.stage in (FixStage.RETRY, FixStage.FAILED)

    def test_identical_content_skips(self):
        from sonarfix.fixer.context_extractor import ExtractedContext
        state = FileFixState(
            file_path="test.py",
            issues=[_make_issue()],
            rules={},
            repo_dir=Path("/tmp"),
        )
        state.context = ExtractedContext(file_path="test.py", is_chunked=False)
        state.original_content = "hello world"
        state.llm_response = "hello world"
        state = node_apply_fix(state)
        assert state.stage == FixStage.DONE
        assert state.skipped_issues == 1

    def test_changed_content_writes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("original")
            from sonarfix.fixer.context_extractor import ExtractedContext
            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue()],
                rules={},
                repo_dir=Path(td),
            )
            state.context = ExtractedContext(file_path="test.py", is_chunked=False)
            state.original_content = "original"
            state.llm_response = "fixed content"
            state = node_apply_fix(state)
            assert state.stage == FixStage.VALIDATE
            assert p.read_text() == "fixed content"


class TestNodeRetry:
    def test_increments_attempt(self):
        state = FileFixState(
            file_path="test.py", issues=[], rules={}, repo_dir=Path("/tmp"),
        )
        state.attempt = 0
        state = node_retry(state)
        assert state.attempt == 1
        assert state.stage == FixStage.BUILD_PROMPT


class TestFixGraph:
    def test_full_success_flow(self):
        """Test the happy path: extract → build → call LLM → apply → validate → done."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("x = 1\nprint(x)\n")

            def mock_llm(system: str, user: str) -> str:
                return "y = 1\nprint(y)\n"

            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue(line=1)],
                rules={"python:S1234": _make_rule()},
                repo_dir=Path(td),
            )
            graph = FixGraph(llm_fn=mock_llm)
            state = graph.run(state)

            assert state.stage == FixStage.DONE
            assert state.fixed_issues == 1
            assert p.read_text().strip() == "y = 1\nprint(y)".strip()

    def test_retry_on_syntax_error(self):
        """Test retry when first attempt produces invalid syntax."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("x = 1\n")

            call_count = [0]

            def mock_llm(system: str, user: str) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    return "x = (\n"  # Invalid syntax
                return "x = 2\n"  # Valid fix

            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue(line=1)],
                rules={},
                repo_dir=Path(td),
            )
            graph = FixGraph(llm_fn=mock_llm)
            state = graph.run(state)

            assert state.stage == FixStage.DONE
            assert state.attempt >= 1  # At least one retry
            assert call_count[0] >= 2

    def test_fails_after_max_retries(self):
        """Test that the graph fails after MAX_RETRIES bad attempts."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("x = 1\n")

            def mock_llm(system: str, user: str) -> str:
                return "x = (\n"  # Always invalid

            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue(line=1)],
                rules={},
                repo_dir=Path(td),
            )
            graph = FixGraph(llm_fn=mock_llm)
            state = graph.run(state)

            assert state.stage in (FixStage.FAILED, FixStage.DONE)
            assert state.attempt <= MAX_RETRIES

    def test_llm_error_retries(self):
        """Test retry on LLM API error."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("x = 1\n")

            call_count = [0]

            def mock_llm(system: str, user: str) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("LLM API returned HTTP 500: server error")
                return "x = 2\n"

            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue(line=1)],
                rules={},
                repo_dir=Path(td),
            )
            graph = FixGraph(llm_fn=mock_llm)
            state = graph.run(state)

            assert state.stage == FixStage.DONE
            assert call_count[0] == 2

    def test_context_window_error_no_retry(self):
        """Test that context window errors are not retried."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.py"
            p.write_text("x = 1\n")

            def mock_llm(system: str, user: str) -> str:
                raise RuntimeError(
                    "LLM API returned HTTP 400: context length exceeded, input_tokens=16385"
                )

            state = FileFixState(
                file_path="test.py",
                issues=[_make_issue(line=1)],
                rules={},
                repo_dir=Path(td),
            )
            graph = FixGraph(llm_fn=mock_llm)
            state = graph.run(state)

            assert state.stage == FixStage.FAILED
            assert state.attempt == 0  # No retries attempted
