"""Tests for the smart context extraction module."""

from __future__ import annotations

import pytest

from sonarfix.fixer.context_extractor import (
    ExtractedContext,
    apply_chunked_fix,
    estimate_tokens,
    extract_context,
    _detect_language,
    _find_python_block,
    _find_brace_block,
    _extract_header,
)


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        tokens = estimate_tokens("hello world")
        assert 1 <= tokens <= 10

    def test_longer_text(self):
        text = "x" * 3500
        tokens = estimate_tokens(text)
        assert 900 <= tokens <= 1100


class TestDetectLanguage:
    def test_python(self):
        assert _detect_language("foo/bar.py") == "python"

    def test_go(self):
        assert _detect_language("main.go") == "go"

    def test_shell(self):
        assert _detect_language("deploy.sh") == "shell"

    def test_javascript(self):
        assert _detect_language("app.js") == "javascript"

    def test_unknown(self):
        assert _detect_language("data.csv") == "unknown"


class TestFindPythonBlock:
    def test_finds_function(self):
        lines = [
            "import os",
            "",
            "def foo():",
            "    x = 1",
            "    return x",
            "",
            "def bar():",
            "    y = 2",
            "    return y",
        ]
        start, end = _find_python_block(lines, 3)  # line 4 (0-indexed 3) inside foo
        assert start <= 2  # should include 'def foo():'
        assert end >= 4  # should include 'return x'

    def test_finds_class_method(self):
        lines = [
            "class MyClass:",
            "    def method(self):",
            "        x = 1",
            "        return x",
            "",
            "    def other(self):",
            "        pass",
        ]
        start, end = _find_python_block(lines, 2)  # inside method
        assert start <= 1
        assert end >= 3

    def test_with_decorator(self):
        lines = [
            "@decorator",
            "def foo():",
            "    pass",
            "",
            "def bar():",
            "    pass",
        ]
        start, end = _find_python_block(lines, 2)  # inside foo
        assert start == 0  # should include decorator


class TestFindBraceBlock:
    def test_finds_go_function(self):
        lines = [
            "package main",
            "",
            "func foo() {",
            "    x := 1",
            "    return x",
            "}",
            "",
            "func bar() {",
            "    y := 2",
            "}",
        ]
        start, end = _find_brace_block(lines, 3)  # inside foo
        assert start <= 2
        assert end >= 5


class TestExtractHeader:
    def test_python_header(self):
        lines = [
            "import os",
            "from pathlib import Path",
            "",
            "# Module comment",
            "",
            "def main():",
            "    pass",
        ]
        end, header = _extract_header(lines, "python")
        assert end >= 2  # at least the imports
        assert "import os" in header

    def test_go_header(self):
        lines = [
            "package main",
            "",
            'import (',
            '    "fmt"',
            '    "os"',
            ')',
            "",
            "func main() {",
        ]
        end, header = _extract_header(lines, "go")
        assert end >= 6  # should include closing paren
        assert "package main" in header


class TestExtractContext:
    def test_small_file_returns_full(self):
        content = "import os\n\ndef foo():\n    pass\n"
        ctx = extract_context("test.py", content, [3])
        assert not ctx.is_chunked
        assert ctx.full_content == content

    def test_large_file_is_chunked(self):
        # Create a file that exceeds the token limit (~42000 chars)
        lines = ["import os", "import sys", ""]
        for i in range(3000):
            lines.append(f"def func_{i}():")
            lines.append(f"    x_{i} = {i} * 2 + 1  # computation for variable")
            lines.append(f"    return x_{i}")
            lines.append("")
        content = "\n".join(lines)
        assert len(content) > 42000  # sanity check
        ctx = extract_context("big.py", content, [100])
        assert ctx.is_chunked
        assert len(ctx.regions) >= 1
        # Should have header region
        assert any("import" in r.label.lower() or "header" in r.label.lower()
                    for r in ctx.regions)

    def test_to_prompt_full(self):
        ctx = ExtractedContext(
            file_path="test.py",
            is_chunked=False,
            full_content="print('hello')",
        )
        assert ctx.to_prompt() == "print('hello')"

    def test_to_prompt_chunked(self):
        from sonarfix.fixer.context_extractor import CodeRegion
        ctx = ExtractedContext(
            file_path="test.py",
            is_chunked=True,
            regions=[
                CodeRegion(start_line=1, end_line=3, content="import os", label="imports/header"),
                CodeRegion(start_line=10, end_line=15, content="def foo():\n    pass", label="function: foo"),
            ],
        )
        prompt = ctx.to_prompt()
        assert "Lines 1-3" in prompt
        assert "Lines 10-15" in prompt
        assert "import os" in prompt


class TestApplyChunkedFix:
    def test_full_file_replacement(self):
        ctx = ExtractedContext(file_path="test.py", is_chunked=False)
        result = apply_chunked_fix("old content", ctx, "new content")
        assert result == "new content"

    def test_chunked_with_markers(self):
        from sonarfix.fixer.context_extractor import CodeRegion
        original = "line1\nline2\nline3\nline4\nline5"
        ctx = ExtractedContext(
            file_path="test.py",
            is_chunked=True,
            regions=[
                CodeRegion(start_line=2, end_line=3, content="line2\nline3"),
            ],
        )
        fixed = "### Lines 2-3\n```\nfixed2\nfixed3\n```"
        result = apply_chunked_fix(original, ctx, fixed)
        assert "fixed2" in result
        assert "fixed3" in result
        assert "line1" in result

    def test_single_region_fallback(self):
        from sonarfix.fixer.context_extractor import CodeRegion
        original = "line1\nline2\nline3\nline4\nline5"
        ctx = ExtractedContext(
            file_path="test.py",
            is_chunked=True,
            regions=[
                CodeRegion(start_line=2, end_line=4, content="line2\nline3\nline4"),
            ],
        )
        # No markers — fallback replaces the region
        result = apply_chunked_fix(original, ctx, "fixed2\nfixed3\nfixed4")
        assert "line1" in result
        assert "fixed2" in result
        assert "line5" in result
