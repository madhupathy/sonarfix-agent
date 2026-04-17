"""LLM-based fixer with smart context extraction, retry loop, validation, and RAG.

Uses the FixGraph state machine:
  extract_context → retrieve_rag → build_prompt → call_llm → apply_fix → validate → store_success
With automatic retry (up to 3x) on validation failure or LLM errors.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

from sonarfix.fixer.graph import FileFixState, FixGraph, FixStage
from sonarfix.rag.store import RAGStore
from sonarfix.sonarqube.models import Issue, Rule

console = Console()


class LLMFixResult:
    """Result from fixing issues in a single file."""

    def __init__(
        self,
        file_path: str,
        success: bool,
        fixed_issues: int = 0,
        skipped_issues: int = 0,
        error: str = "",
    ):
        self.file_path = file_path
        self.success = success
        self.fixed_issues = fixed_issues
        self.skipped_issues = skipped_issues
        self.error = error


class LLMFixer:
    """Applies SonarQube fixes directly using an LLM API (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        rag_enabled: bool = True,
        timeout: float = 180.0,
    ):
        if not api_key:
            raise ValueError(
                "LLM API key is required. Configure it in Settings → LLM tab "
                "or set LLM_API_KEY environment variable."
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._rag_store: Optional[RAGStore] = None
        if rag_enabled:
            try:
                self._rag_store = RAGStore()
            except Exception:
                pass  # RAG is optional — degrade gracefully

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM API and return the assistant response text."""
        import httpx

        url = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 8192,
        }

        resp = httpx.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
            verify=False,
        )
        if resp.status_code != 200:
            body = resp.text[:500]
            raise RuntimeError(
                f"LLM API returned HTTP {resp.status_code}: {body}"
            )
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"LLM API returned no choices: {json.dumps(data)[:500]}")
        content = choices[0].get("message", {}).get("content", "")
        finish_reason = choices[0].get("finish_reason", "")
        if finish_reason == "length":
            console.print("  [yellow]WARNING[/] LLM response was truncated (finish_reason=length)")
        return content

    def _read_file(self, repo_dir: Path, file_path: str) -> Optional[str]:
        """Read the full file content."""
        full_path = repo_dir / file_path
        if not full_path.exists():
            return None
        try:
            return full_path.read_text(errors="replace")
        except Exception:
            return None

    def _write_file(self, repo_dir: Path, file_path: str, content: str) -> None:
        """Write content back to the file."""
        full_path = repo_dir / file_path
        full_path.write_text(content, encoding="utf-8")

    def _build_issue_description(self, issue: Issue, rule: Optional[Rule]) -> str:
        """Build a concise description of a single issue for the LLM."""
        parts = [
            f"Rule: {issue.rule}",
            f"Severity: {issue.severity}",
            f"Type: {issue.type}",
            f"Message: {issue.message}",
        ]
        if issue.start_line:
            loc = f"Line {issue.start_line}"
            if issue.end_line and issue.end_line != issue.start_line:
                loc += f"-{issue.end_line}"
            parts.append(f"Location: {loc}")
        if rule and rule.html_desc:
            desc = re.sub(r"<[^>]+>", "", rule.html_desc).strip()[:800]
            parts.append(f"Rule description: {desc}")
        return "\n".join(parts)

    def fix_file(
        self,
        repo_dir: Path,
        file_path: str,
        issues: List[Issue],
        rules: Dict[str, Rule],
        log_fn=None,
    ) -> LLMFixResult:
        """Fix all issues in a single file using the FixGraph pipeline."""
        state = FileFixState(
            file_path=file_path,
            issues=issues,
            rules=rules,
            repo_dir=repo_dir,
        )

        graph = FixGraph(
            llm_fn=self._call_llm,
            rag_store=self._rag_store,
            log_fn=log_fn or (lambda msg: None),
        )

        state = graph.run(state)

        if state.stage == FixStage.DONE:
            if state.fixed_issues > 0:
                console.print(f"  [green]FIXED[/] {file_path} — {state.fixed_issues} issue(s)")
                if state.attempt > 0:
                    console.print(f"    (succeeded on attempt {state.attempt + 1})")
            else:
                console.print(f"  [yellow]SKIP[/] {file_path} — no changes needed")
            return LLMFixResult(
                file_path, True,
                fixed_issues=state.fixed_issues,
                skipped_issues=state.skipped_issues,
            )
        else:
            console.print(f"  [red]ERROR[/] {file_path} — {state.error}")
            return LLMFixResult(
                file_path, False,
                skipped_issues=len(issues),
                error=state.error,
            )

    def fix_all(
        self,
        repo_dir: Path,
        file_groups: Dict[str, List[Issue]],
        rules: Dict[str, Rule],
        log_fn=None,
    ) -> List[LLMFixResult]:
        """Fix issues across all files using the graph pipeline."""
        results: List[LLMFixResult] = []

        for file_path, issues in file_groups.items():
            if log_fn:
                log_fn(f"Fixing {file_path} ({len(issues)} issues)...")
            result = self.fix_file(repo_dir, file_path, issues, rules, log_fn=log_fn)
            results.append(result)
            if log_fn:
                if result.fixed_issues:
                    log_fn(f"Fixed {result.fixed_issues} issue(s) in {file_path}")
                elif result.error:
                    log_fn(f"Error in {file_path}: {result.error}")
                else:
                    log_fn(f"Skipped {result.skipped_issues} issue(s) in {file_path}")

        # Log RAG stats
        if self._rag_store and log_fn:
            stats = self._rag_store.get_stats()
            log_fn(f"RAG store: {stats['fix_examples']} fix examples, {stats['standard_docs']} standard docs")

        return results

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Strip leading/trailing markdown code fences if present."""
        text = text.strip()
        # Match ```python\n...\n``` or ```\n...\n``` (with optional trailing whitespace)
        m = re.match(r"^```[a-zA-Z0-9]*\s*\n(.*?)\n\s*```\s*$", text, re.DOTALL)
        if m:
            return m.group(1)
        # Also handle ``` on same line
        if text.startswith("```") and text.endswith("```"):
            # Remove first line and last line
            lines = text.split("\n")
            if len(lines) >= 3:
                return "\n".join(lines[1:-1])
        return text
