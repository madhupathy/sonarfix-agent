"""LangGraph-style fix agent workflow: fix → validate → retry → verify.

This implements a state-machine approach to fixing SonarQube issues with:
1. Smart context extraction (chunked for large files)
2. Enhanced prompts with rule descriptions and RAG examples
3. Retry loop with error feedback (up to MAX_RETRIES)
4. Post-fix validation (syntax check) with feedback
5. RAG storage of successful fixes for future reference
"""

from __future__ import annotations

import difflib
import re
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from rich.console import Console

from sonarfix.fixer.context_extractor import (
    ExtractedContext,
    apply_chunked_fix,
    extract_context,
)
from sonarfix.rag.store import RAGStore
from sonarfix.sonarqube.models import Issue, Rule
from sonarfix.validator.checker import syntax_check_file

console = Console()

MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class FixStage(str, Enum):
    INIT = "init"
    EXTRACT_CONTEXT = "extract_context"
    RETRIEVE_RAG = "retrieve_rag"
    BUILD_PROMPT = "build_prompt"
    CALL_LLM = "call_llm"
    APPLY_FIX = "apply_fix"
    VALIDATE = "validate"
    RETRY = "retry"
    STORE_SUCCESS = "store_success"
    DONE = "done"
    FAILED = "failed"


@dataclass
class FileFixState:
    """Mutable state for fixing a single file through the graph."""
    file_path: str
    issues: List[Issue]
    rules: Dict[str, Rule]
    repo_dir: Path

    # Extracted context
    context: Optional[ExtractedContext] = None
    original_content: str = ""

    # RAG examples
    rag_examples: str = ""
    rag_standards: str = ""

    # Prompts
    system_prompt: str = ""
    user_prompt: str = ""

    # LLM response
    llm_response: str = ""

    # Fix result
    fixed_content: str = ""
    fix_applied: bool = False

    # Validation
    validation_passed: bool = False
    validation_error: str = ""

    # Retry
    attempt: int = 0
    error_history: List[str] = field(default_factory=list)

    # Final result
    stage: FixStage = FixStage.INIT
    fixed_issues: int = 0
    skipped_issues: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_FULL = """You are an expert code fixer performing MINIMAL, SURGICAL fixes for SonarQube issues.

CRITICAL RULES — violating any of these is a failure:
1. Fix ONLY the specific issue(s) listed. Do NOT fix anything else.
2. Change the MINIMUM number of lines possible. Ideal fix is 1-5 lines changed.
3. NEVER remove or modify comments, docstrings, logging statements, or blank lines.
4. NEVER rename variables, functions, classes, or parameters.
5. NEVER extract code into new helper functions or reorganize code structure.
6. NEVER delete or restructure error handling (try/except/catch blocks).
7. NEVER change import statements unless the issue specifically requires it.
8. NEVER change code formatting, indentation style, or whitespace patterns.
9. Preserve ALL existing functionality — the fix must be behavior-preserving.
10. Return ONLY the complete fixed file content, nothing else.
11. Do NOT wrap the output in markdown code fences or add any prefix/suffix text.
12. The output must be valid, syntactically correct code.

For Cognitive Complexity (S3776) fixes:
- Use early returns/continue to reduce nesting — do NOT extract helper functions.
- Combine related conditions with `and`/`or` to reduce branches.
- Replace nested if/else with guard clauses.
- Keep all code in the SAME function, just simplify the control flow.

If you are unsure how to fix an issue without major refactoring, leave the code unchanged."""

SYSTEM_PROMPT_CHUNKED = """You are an expert code fixer performing MINIMAL, SURGICAL fixes for SonarQube issues.

You will be given EXTRACTED REGIONS of a source code file (not the full file) and SonarQube issues to fix.
Each region is labeled with its line range (e.g. "Lines 1-30").

CRITICAL RULES — violating any of these is a failure:
1. Fix ONLY the specific issue(s) listed. Change the MINIMUM number of lines possible.
2. NEVER remove or modify comments, docstrings, logging statements, or blank lines.
3. NEVER rename variables, functions, classes, or parameters.
4. NEVER extract code into new helper functions or reorganize code structure.
5. NEVER delete or restructure error handling (try/except/catch blocks).
6. NEVER change import statements unless the issue specifically requires it.
7. Preserve ALL existing functionality — the fix must be behavior-preserving.

For Cognitive Complexity (S3776) fixes:
- Use early returns/continue to reduce nesting — do NOT extract helper functions.
- Combine related conditions with `and`/`or`.
- Replace nested if/else with guard clauses.
- Keep all code in the SAME function.

OUTPUT FORMAT — return each fixed region like this:
### Lines {start}-{end}
```
{fixed code for this region}
```

CRITICAL: You MUST return ALL provided regions (even unchanged ones) with their line markers.
Do NOT add explanations. Do NOT wrap the entire output in additional code fences.
If you are unsure how to fix an issue without major refactoring, return the region UNCHANGED."""

RETRY_ADDENDUM = """

IMPORTANT: Previous fix attempt(s) failed. Here is the error feedback:
{error_feedback}

Please try again. If the feedback says "over-refactored", you changed TOO MANY lines.
Make a SMALLER, more targeted fix — change only the specific lines that cause the SonarQube issue.
Do NOT extract helper functions, do NOT remove comments, do NOT restructure code.
Make sure the output is valid, syntactically correct code."""


def _build_issue_description(issue: Issue, rule: Optional[Rule]) -> str:
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
    if rule:
        if rule.name:
            parts.append(f"Rule name: {rule.name}")
        if rule.html_desc:
            desc = re.sub(r"<[^>]+>", "", rule.html_desc).strip()[:1500]
            parts.append(f"Rule description: {desc}")

    # Add actionable hints for common rules
    rule_key = issue.rule.lower()
    if "s1871" in rule_key:
        parts.append(
            "ACTION: Two branches have identical code. Either merge them into "
            "one using `or` / combined conditions, or differentiate their implementations."
        )
    elif "s3776" in rule_key:
        parts.append(
            "ACTION: Reduce Cognitive Complexity using ONLY these techniques "
            "(do NOT extract helper functions or restructure the code):\n"
            "  - Use early return/continue to reduce nesting depth\n"
            "  - Combine related if-conditions with `and`/`or`\n"
            "  - Replace `else` after `return`/`continue`/`raise` with un-indented code\n"
            "  - Flatten nested if-else chains into elif\n"
            "  - Keep ALL code in the same function"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def node_extract_context(state: FileFixState) -> FileFixState:
    """Extract smart context from the file."""
    full_path = state.repo_dir / state.file_path
    if not full_path.exists():
        state.error = f"File not found: {state.file_path}"
        state.stage = FixStage.FAILED
        return state

    try:
        state.original_content = full_path.read_text(errors="replace")
    except Exception as e:
        state.error = f"Cannot read file: {e}"
        state.stage = FixStage.FAILED
        return state

    issue_lines = [i.start_line for i in state.issues if i.start_line]

    # Use smaller budget on re-chunk after context-length error
    from sonarfix.fixer.context_extractor import MAX_CONTEXT_CHARS
    max_chars = MAX_CONTEXT_CHARS
    if getattr(state, '_aggressive_chunk', False):
        max_chars = max_chars // 2  # halve the budget

    state.context = extract_context(
        state.file_path, state.original_content, issue_lines,
        max_chars=max_chars,
    )

    state.stage = FixStage.RETRIEVE_RAG
    return state


def node_retrieve_rag(state: FileFixState, rag_store: Optional[RAGStore] = None) -> FileFixState:
    """Retrieve similar past fixes and relevant standards from RAG store."""
    if rag_store is None:
        state.stage = FixStage.BUILD_PROMPT
        return state

    language = Path(state.file_path).suffix.lstrip(".")

    # Retrieve similar fix examples
    examples_parts = []
    for issue in state.issues[:3]:  # Limit to first 3 issues to save context
        rule = state.rules.get(issue.rule)
        examples = rag_store.retrieve_similar_fixes(
            rule_key=issue.rule,
            issue_message=issue.message,
            language=language,
            top_k=2,
        )
        for ex in examples:
            examples_parts.append(
                f"**Rule {ex.rule_key}** (score: {ex.score:.2f}):\n"
                f"Before:\n```\n{ex.before_snippet[:500]}\n```\n"
                f"After:\n```\n{ex.after_snippet[:500]}\n```"
            )

    if examples_parts:
        state.rag_examples = "## Similar Past Fixes\n" + "\n\n".join(examples_parts[:4])

    # Retrieve relevant coding standards
    query = " ".join(i.message for i in state.issues[:3])
    standards = rag_store.retrieve_standards(query=query, language=language, top_k=2)
    if standards:
        std_parts = [f"**{s.title}** ({s.source}):\n{s.content[:400]}" for s in standards]
        state.rag_standards = "## Relevant Coding Standards\n" + "\n\n".join(std_parts)

    state.stage = FixStage.BUILD_PROMPT
    return state


def node_build_prompt(state: FileFixState) -> FileFixState:
    """Build the LLM prompt with context, issues, RAG examples, and retry feedback."""
    ctx = state.context
    if ctx is None:
        state.error = "No context extracted"
        state.stage = FixStage.FAILED
        return state

    # Choose system prompt based on chunking
    if ctx.is_chunked:
        state.system_prompt = SYSTEM_PROMPT_CHUNKED
    else:
        state.system_prompt = SYSTEM_PROMPT_FULL

    # Add retry feedback if this is a retry
    if state.attempt > 0 and state.error_history:
        feedback = "\n".join(f"Attempt {i+1}: {e}" for i, e in enumerate(state.error_history))
        state.system_prompt += RETRY_ADDENDUM.format(error_feedback=feedback)

    # Build issue descriptions
    issue_blocks = []
    for idx, issue in enumerate(state.issues, 1):
        rule = state.rules.get(issue.rule)
        desc = _build_issue_description(issue, rule)
        issue_blocks.append(f"### Issue {idx}\n{desc}")
    issues_text = "\n\n".join(issue_blocks)

    # Build user prompt
    parts = [f"## File: `{state.file_path}`"]

    if ctx.is_chunked:
        parts.append(f"\n*File is {ctx.total_lines} lines. Showing extracted regions only.*\n")
        parts.append(f"### Extracted Code Regions:\n{ctx.to_prompt()}")
    else:
        parts.append(f"### Current file content:\n```\n{ctx.full_content}\n```")

    parts.append(f"\n### SonarQube Issues to fix:\n{issues_text}")

    # Add RAG context
    if state.rag_examples:
        parts.append(f"\n{state.rag_examples}")
    if state.rag_standards:
        parts.append(f"\n{state.rag_standards}")

    if ctx.is_chunked:
        parts.append(
            "\nReturn ALL fixed regions with their line markers "
            "(### Lines N-M format). Include unchanged regions too."
        )
    else:
        parts.append("\nReturn the complete fixed file content (no code fences, no explanations):")

    state.user_prompt = "\n".join(parts)
    state.stage = FixStage.CALL_LLM
    return state


def node_call_llm(
    state: FileFixState,
    llm_fn: Callable[[str, str], str],
) -> FileFixState:
    """Call the LLM API with the built prompts."""
    try:
        state.llm_response = llm_fn(state.system_prompt, state.user_prompt)
        state.stage = FixStage.APPLY_FIX
    except Exception as e:
        error_msg = str(e)[:300]
        state.error_history.append(f"LLM call error: {error_msg}")

        if "context length" in error_msg.lower() or "too many tokens" in error_msg.lower() or "input_tokens" in error_msg.lower():
            # Context too large — try re-chunking with a smaller budget
            if not getattr(state, '_rechunked', False):
                state._rechunked = True
                state.error_history.append(f"Context too large, re-chunking with smaller budget")
                state.stage = FixStage.EXTRACT_CONTEXT
                # Mark for aggressive chunking on next extraction
                state._aggressive_chunk = True
            else:
                state.error = f"File too large for LLM context window even after aggressive chunking: {error_msg}"
                state.stage = FixStage.FAILED
        elif state.attempt < MAX_RETRIES:
            state.stage = FixStage.RETRY
        else:
            state.error = error_msg
            state.stage = FixStage.FAILED

    return state


def _get_allowed_line_ranges(state: FileFixState) -> List[Tuple[int, int]]:
    """Compute the allowed line ranges for changes based on issue locations.

    For each issue, we allow changes within the function/block containing it
    (found via context extractor) + some padding.  For S3776 (Cognitive Complexity)
    the entire function is allowed since simplifying control flow may touch many lines.
    """
    from sonarfix.fixer.context_extractor import (
        _detect_language,
        _find_block_boundaries,
    )

    lines = state.original_content.splitlines()
    language = _detect_language(state.file_path)
    ranges: List[Tuple[int, int]] = []

    for issue in state.issues:
        target_line = issue.start_line or 1
        block_start, block_end = _find_block_boundaries(lines, target_line, language)

        # For S3776, allow the full function block + generous padding
        rule_key = issue.rule.lower()
        if "s3776" in rule_key:
            padding = 10
        else:
            padding = 20

        allowed_start = max(0, block_start - padding)
        allowed_end = min(len(lines) - 1, block_end + padding)
        ranges.append((allowed_start, allowed_end))

    # Merge overlapping ranges
    if not ranges:
        return ranges
    ranges.sort()
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _filter_changes_by_proximity(
    original: str, fixed: str, allowed_ranges: List[Tuple[int, int]]
) -> str:
    """Keep only the diff hunks that fall within the allowed line ranges.

    For any change outside the allowed ranges, revert to the original lines.
    This prevents the LLM from making unrelated changes far from the issue.
    """
    orig_lines = original.splitlines(keepends=True)
    fix_lines = fixed.splitlines(keepends=True)

    sm = difflib.SequenceMatcher(None, orig_lines, fix_lines)
    result: List[str] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            result.extend(orig_lines[i1:i2])
        elif tag in ("replace", "delete", "insert"):
            # Check if this change overlaps with any allowed range
            # Use orig line indices (0-based) for the check
            change_start = i1
            change_end = max(i1, i2 - 1)
            is_allowed = False
            for a_start, a_end in allowed_ranges:
                if change_start <= a_end and change_end >= a_start:
                    is_allowed = True
                    break
            if is_allowed:
                # Keep the LLM's changes
                if tag == "replace":
                    result.extend(fix_lines[j1:j2])
                elif tag == "delete":
                    pass  # lines removed — keep them removed
                elif tag == "insert":
                    result.extend(fix_lines[j1:j2])
            else:
                # Revert to original — discard LLM's changes here
                result.extend(orig_lines[i1:i2])

    text = "".join(result)
    # Preserve original trailing newline behavior
    if original.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    elif not original.endswith("\n") and text.endswith("\n"):
        text = text.rstrip("\n")
    return text


def node_apply_fix(state: FileFixState) -> FileFixState:
    """Apply the LLM response as a fix to the file.

    Uses line-proximity filtering to discard any LLM changes that are
    far from the reported issue locations.
    """
    response = state.llm_response.strip()
    if not response:
        state.error_history.append("LLM returned empty content")
        if state.attempt < MAX_RETRIES:
            state.stage = FixStage.RETRY
        else:
            state.error = "LLM returned empty content after all retries"
            state.stage = FixStage.FAILED
        return state

    # Strip markdown code fences if present
    response = _strip_code_fences(response)

    ctx = state.context
    if ctx and ctx.is_chunked:
        state.fixed_content = apply_chunked_fix(
            state.original_content, ctx, response
        )
    else:
        state.fixed_content = response

    # --- Surgical filter: only keep changes near issue lines ---
    allowed_ranges = _get_allowed_line_ranges(state)
    if allowed_ranges:
        state.fixed_content = _filter_changes_by_proximity(
            state.original_content, state.fixed_content, allowed_ranges
        )

    # Check if content actually changed (after filtering)
    if state.fixed_content.rstrip() == state.original_content.rstrip():
        state.skipped_issues = len(state.issues)
        state.stage = FixStage.DONE
        return state

    # Write the fixed content
    try:
        full_path = state.repo_dir / state.file_path
        full_path.write_text(state.fixed_content, encoding="utf-8")
        state.fix_applied = True
        state.stage = FixStage.VALIDATE
    except Exception as e:
        state.error_history.append(f"Write error: {e}")
        if state.attempt < MAX_RETRIES:
            state.stage = FixStage.RETRY
        else:
            state.error = f"Failed to write file: {e}"
            state.stage = FixStage.FAILED

    return state


def node_validate(state: FileFixState) -> FileFixState:
    """Run syntax validation on the fixed file."""
    check = syntax_check_file(state.repo_dir, state.file_path)

    if check.passed:
        state.validation_passed = True
        state.fixed_issues = len(state.issues)
        state.stage = FixStage.STORE_SUCCESS
    else:
        state.validation_error = check.output[:500]
        state.error_history.append(f"Syntax check failed: {state.validation_error}")

        # Restore original content before retry
        try:
            full_path = state.repo_dir / state.file_path
            full_path.write_text(state.original_content, encoding="utf-8")
        except Exception:
            pass

        if state.attempt < MAX_RETRIES:
            state.stage = FixStage.RETRY
        else:
            # Last attempt — accept the fix anyway if it's just a warning
            if "warning" in state.validation_error.lower():
                # Re-apply the fix
                try:
                    full_path = state.repo_dir / state.file_path
                    full_path.write_text(state.fixed_content, encoding="utf-8")
                except Exception:
                    pass
                state.fixed_issues = len(state.issues)
                state.stage = FixStage.STORE_SUCCESS
            else:
                state.error = f"Validation failed after {MAX_RETRIES} retries: {state.validation_error}"
                state.stage = FixStage.FAILED

    return state


def node_store_success(
    state: FileFixState,
    rag_store: Optional[RAGStore] = None,
) -> FileFixState:
    """Store the successful fix in RAG for future reference."""
    if rag_store is not None:
        language = Path(state.file_path).suffix.lstrip(".")
        for issue in state.issues:
            # Extract a snippet around the issue line
            orig_lines = state.original_content.splitlines()
            fixed_lines = state.fixed_content.splitlines()
            line = issue.start_line or 1
            start = max(0, line - 6)
            end = min(len(orig_lines), line + 5)

            before = "\n".join(orig_lines[start:end])
            after = "\n".join(fixed_lines[start:min(len(fixed_lines), end)])

            rag_store.store_fix(
                rule_key=issue.rule,
                language=language,
                severity=issue.severity,
                issue_message=issue.message,
                before_snippet=before,
                after_snippet=after,
            )

    state.stage = FixStage.DONE
    return state


def node_retry(state: FileFixState) -> FileFixState:
    """Increment attempt counter and loop back to prompt building."""
    state.attempt += 1
    state.stage = FixStage.BUILD_PROMPT
    return state


def _strip_code_fences(text: str) -> str:
    """Strip leading/trailing markdown code fences if present."""
    text = text.strip()
    m = re.match(r"^```[a-zA-Z0-9]*\s*\n(.*?)\n\s*```\s*$", text, re.DOTALL)
    if m:
        return m.group(1)
    if text.startswith("```") and text.endswith("```"):
        lines = text.split("\n")
        if len(lines) >= 3:
            return "\n".join(lines[1:-1])
    return text


# ---------------------------------------------------------------------------
# Graph runner
# ---------------------------------------------------------------------------

class FixGraph:
    """State machine that runs the fix pipeline for a single file."""

    def __init__(
        self,
        llm_fn: Callable[[str, str], str],
        rag_store: Optional[RAGStore] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.llm_fn = llm_fn
        self.rag_store = rag_store
        self.log_fn = log_fn or (lambda msg: None)

    def run(self, state: FileFixState) -> FileFixState:
        """Execute the fix graph until terminal state (DONE or FAILED)."""
        max_steps = 30  # safety limit
        step = 0

        while state.stage not in (FixStage.DONE, FixStage.FAILED) and step < max_steps:
            step += 1
            prev_stage = state.stage

            if state.stage == FixStage.INIT:
                state.stage = FixStage.EXTRACT_CONTEXT

            elif state.stage == FixStage.EXTRACT_CONTEXT:
                state = node_extract_context(state)

            elif state.stage == FixStage.RETRIEVE_RAG:
                state = node_retrieve_rag(state, self.rag_store)

            elif state.stage == FixStage.BUILD_PROMPT:
                state = node_build_prompt(state)

            elif state.stage == FixStage.CALL_LLM:
                if state.attempt > 0:
                    self.log_fn(f"  Retry {state.attempt}/{MAX_RETRIES} for {state.file_path}")
                state = node_call_llm(state, self.llm_fn)

            elif state.stage == FixStage.APPLY_FIX:
                state = node_apply_fix(state)

            elif state.stage == FixStage.VALIDATE:
                state = node_validate(state)

            elif state.stage == FixStage.STORE_SUCCESS:
                state = node_store_success(state, self.rag_store)

            elif state.stage == FixStage.RETRY:
                state = node_retry(state)

            # Safety: if stage didn't change, something is stuck
            if state.stage == prev_stage and state.stage not in (FixStage.DONE, FixStage.FAILED):
                state.error = f"Graph stuck at stage {state.stage}"
                state.stage = FixStage.FAILED

        return state
