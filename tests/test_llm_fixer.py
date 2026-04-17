"""Tests for LLM fixer code fence stripping and error handling."""

from sonarfix.fixer.llm_fixer import LLMFixer


class TestStripCodeFences:
    def test_no_fences(self):
        text = "print('hello')\nprint('world')"
        assert LLMFixer._strip_code_fences(text) == text

    def test_python_fences(self):
        text = "```python\nprint('hello')\nprint('world')\n```"
        assert LLMFixer._strip_code_fences(text) == "print('hello')\nprint('world')"

    def test_plain_fences(self):
        text = "```\nprint('hello')\n```"
        assert LLMFixer._strip_code_fences(text) == "print('hello')"

    def test_fences_with_trailing_whitespace(self):
        text = "```python  \nprint('hello')\n```  "
        assert LLMFixer._strip_code_fences(text) == "print('hello')"

    def test_fences_with_trailing_newline(self):
        text = "```python\nprint('hello')\n```\n"
        assert LLMFixer._strip_code_fences(text) == "print('hello')"

    def test_multiline_content(self):
        content = "import os\nimport sys\n\ndef main():\n    pass"
        text = f"```python\n{content}\n```"
        assert LLMFixer._strip_code_fences(text) == content

    def test_preserves_internal_backticks(self):
        text = "x = '```not a fence```'\ny = 1"
        assert LLMFixer._strip_code_fences(text) == text


class TestLLMFixerInit:
    def test_raises_on_empty_key(self):
        try:
            LLMFixer(api_key="")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "API key is required" in str(e)

    def test_accepts_valid_key(self):
        fixer = LLMFixer(api_key="sk-test123")
        assert fixer.api_key == "sk-test123"
        assert fixer.model == "gpt-4o"

    def test_custom_model_and_base_url(self):
        fixer = LLMFixer(api_key="sk-test", model="gpt-4o-mini", base_url="http://localhost:8080/v1")
        assert fixer.model == "gpt-4o-mini"
        assert fixer.base_url == "http://localhost:8080/v1"
