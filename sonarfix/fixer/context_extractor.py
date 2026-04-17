"""Smart context extraction — sends only relevant code regions to the LLM instead of full files.

For large files that exceed the LLM context window, this module extracts:
1. File imports/header (first N lines)
2. The function/class/block containing each issue line
3. Surrounding context lines for continuity

The LLM then returns a PATCH (only the changed region) instead of the whole file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# Approximate tokens-per-char ratio (conservative for code)
CHARS_PER_TOKEN = 3.5
# Leave room for system prompt, issue descriptions, and LLM output
# 32768 total - ~4096 output - ~3000 prompt/issues = ~20000 for code context
MAX_CONTEXT_TOKENS = 20000
MAX_CONTEXT_CHARS = int(MAX_CONTEXT_TOKENS * CHARS_PER_TOKEN)
# How many lines of imports/header to always include
HEADER_LINES = 30
# Lines of padding around extracted regions
PADDING_LINES = 15
# Hard cap for any extracted region (prevents gigantic functions from being sent)
MAX_REGION_LINES = 400


@dataclass
class CodeRegion:
    """A contiguous region of code extracted from a file."""
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed, inclusive
    content: str
    label: str = ""  # e.g. "imports", "function foo", "class Bar"


@dataclass
class ExtractedContext:
    """The chunked context for a single file, ready to send to the LLM."""
    file_path: str
    regions: List[CodeRegion] = field(default_factory=list)
    total_lines: int = 0
    is_chunked: bool = False  # True if we had to chunk (file was too large)
    full_content: str = ""  # Only set if is_chunked is False

    def to_prompt(self) -> str:
        """Format the extracted context for the LLM prompt."""
        if not self.is_chunked:
            return self.full_content

        parts = []
        for region in self.regions:
            label = f"  ({region.label})" if region.label else ""
            parts.append(
                f"### Lines {region.start_line}-{region.end_line}{label}\n"
                f"```\n{region.content}\n```"
            )
        return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough token count estimate."""
    return int(len(text) / CHARS_PER_TOKEN)


def _find_block_boundaries(
    lines: List[str], target_line: int, language: str
) -> Tuple[int, int]:
    """Find the start and end of the code block (function/class/method) containing target_line.

    Returns (start, end) as 0-indexed line numbers.
    """
    if not lines:
        return (0, 0)

    target_idx = min(max(target_line - 1, 0), len(lines) - 1)

    if language in ("py", "python"):
        return _find_python_block(lines, target_idx)
    elif language in ("go", "golang"):
        return _find_brace_block(lines, target_idx)
    elif language in ("sh", "bash", "shell"):
        return _find_shell_block(lines, target_idx)
    elif language in ("java", "js", "javascript", "ts", "typescript", "c", "cpp", "cs"):
        return _find_brace_block(lines, target_idx)
    else:
        # Fallback: try brace-based, then use padding
        return _find_brace_block(lines, target_idx)


def _find_python_block(lines: List[str], target_idx: int) -> Tuple[int, int]:
    """Find Python function/class block containing the target line."""
    # Walk backwards to find the def/class at the same or lower indentation
    target_indent = len(lines[target_idx]) - len(lines[target_idx].lstrip())

    block_start = target_idx
    for i in range(target_idx, -1, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent < target_indent or (indent == target_indent and i < target_idx):
            if re.match(r'^\s*(def |class |async def )', lines[i]):
                block_start = i
                break
        if indent == 0 and re.match(r'^(def |class |async def )', stripped):
            block_start = i
            break

    # Walk backwards further to pick up decorators
    for i in range(block_start - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("@"):
            block_start = i
        elif stripped == "" or stripped.startswith("#"):
            continue
        else:
            break

    # Walk forward to find end of block
    if block_start < len(lines):
        block_indent = len(lines[block_start]) - len(lines[block_start].lstrip())
    else:
        block_indent = 0

    block_end = target_idx
    for i in range(target_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= block_indent and stripped and not stripped.startswith("#"):
            # Check if this is a new def/class at same level
            if re.match(r'^\s*(def |class |async def |@)', lines[i]):
                break
        block_end = i

    return (block_start, block_end)


def _find_brace_block(lines: List[str], target_idx: int) -> Tuple[int, int]:
    """Find brace-delimited block (Go, Java, JS, etc.) containing the target line."""
    # Walk backwards to find opening of function/method
    brace_depth = 0
    block_start = target_idx

    for i in range(target_idx, -1, -1):
        line = lines[i]
        brace_depth += line.count('}') - line.count('{')
        if brace_depth <= 0:
            # Check if this looks like a function/method declaration
            if re.match(r'^\s*(func |function |def |class |public |private |protected |export |\w+.*\()', line):
                block_start = i
                break
            if '{' in line and brace_depth <= 0:
                block_start = i
                break

    # Walk backwards for comments/decorators above
    for i in range(block_start - 1, max(block_start - 5, -1), -1):
        stripped = lines[i].strip()
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("@"):
            block_start = i
        elif stripped == "":
            continue
        else:
            break

    # Walk forward to find closing brace
    brace_depth = 0
    block_end = target_idx
    for i in range(block_start, len(lines)):
        line = lines[i]
        brace_depth += line.count('{') - line.count('}')
        block_end = i
        if brace_depth <= 0 and i >= target_idx:
            break

    return (block_start, block_end)


def _find_shell_block(lines: List[str], target_idx: int) -> Tuple[int, int]:
    """Find shell function block containing the target line."""
    block_start = target_idx
    for i in range(target_idx, -1, -1):
        stripped = lines[i].strip()
        if re.match(r'^\w+\s*\(\)\s*\{', stripped) or re.match(r'^function\s+\w+', stripped):
            block_start = i
            break

    block_end = target_idx
    brace_depth = 0
    for i in range(block_start, len(lines)):
        brace_depth += lines[i].count('{') - lines[i].count('}')
        block_end = i
        if brace_depth <= 0 and i >= target_idx:
            break

    return (block_start, block_end)


def _detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    lang_map = {
        ".py": "python", ".go": "go", ".java": "java",
        ".js": "javascript", ".ts": "typescript", ".jsx": "javascript",
        ".tsx": "typescript", ".sh": "shell", ".bash": "shell",
        ".rb": "ruby", ".php": "php", ".c": "c", ".cpp": "cpp",
        ".cs": "cs", ".rs": "rust", ".kt": "kotlin",
    }
    return lang_map.get(ext, "unknown")


def _extract_header(lines: List[str], language: str) -> Tuple[int, List[str]]:
    """Extract the import/header section of a file.

    Returns (end_line_idx, header_lines).
    """
    header_end = 0

    if language == "python":
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                # Blank line — include only if we're still in the header region
                if i < HEADER_LINES:
                    header_end = i + 1
                continue
            if stripped.startswith(("import ", "from ", "#", '"""', "'''")):
                header_end = i + 1
            elif stripped.startswith(("def ", "class ", "async def ", "@")):
                break
            elif i > HEADER_LINES * 2:
                break
            else:
                header_end = i + 1
    elif language in ("go", "golang"):
        in_import = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("package ") or stripped.startswith("import "):
                header_end = i + 1
                if "(" in stripped and ")" not in stripped:
                    in_import = True
            elif in_import:
                header_end = i + 1
                if ")" in stripped:
                    in_import = False
            elif stripped.startswith("//") or stripped == "":
                header_end = i + 1
            elif i > HEADER_LINES * 2:
                break
            else:
                break
    else:
        # Generic: take first HEADER_LINES lines
        header_end = min(HEADER_LINES, len(lines))

    return (header_end, lines[:header_end])


def extract_context(
    file_path: str,
    file_content: str,
    issue_lines: List[int],
    max_chars: int = MAX_CONTEXT_CHARS,
) -> ExtractedContext:
    """Extract smart context for a file given the issue line numbers.

    If the file fits within max_chars, returns the full content.
    Otherwise, extracts only the relevant regions (imports + blocks containing issues).
    """
    if len(file_content) <= max_chars:
        return ExtractedContext(
            file_path=file_path,
            total_lines=file_content.count("\n") + 1,
            is_chunked=False,
            full_content=file_content,
        )

    lines = file_content.splitlines()
    language = _detect_language(file_path)

    # Always include file header/imports
    header_end, header_lines = _extract_header(lines, language)
    header_content = "\n".join(header_lines)

    regions: List[CodeRegion] = []
    if header_lines:
        regions.append(CodeRegion(
            start_line=1,
            end_line=header_end,
            content=header_content,
            label="imports/header",
        ))

    # For each issue line, find the containing block
    covered_ranges: List[Tuple[int, int]] = [(0, header_end - 1)] if header_lines else []

    for issue_line in sorted(set(issue_lines)):
        if issue_line is None or issue_line <= 0:
            continue

        # Check if already covered
        already_covered = False
        for start, end in covered_ranges:
            if start <= issue_line - 1 <= end:
                already_covered = True
                break
        if already_covered:
            continue

        block_start, block_end = _find_block_boundaries(lines, issue_line, language)

        # Add padding
        padded_start = max(0, block_start - PADDING_LINES)
        padded_end = min(len(lines) - 1, block_end + PADDING_LINES)

        # Don't overlap with header
        if padded_start < header_end:
            padded_start = header_end

        if padded_start > padded_end:
            continue

        # If the region is still enormous, trim it around the issue line
        region_line_count = padded_end - padded_start + 1
        if region_line_count > MAX_REGION_LINES:
            half = MAX_REGION_LINES // 2
            issue_idx = max(header_end, min(len(lines) - 1, issue_line - 1))
            new_start = max(header_end, issue_idx - half)
            new_end = new_start + MAX_REGION_LINES - 1
            if new_end > len(lines) - 1:
                new_end = len(lines) - 1
                new_start = max(header_end, new_end - MAX_REGION_LINES + 1)
            padded_start, padded_end = new_start, new_end
            region_line_count = padded_end - padded_start + 1

        region_lines = lines[padded_start:padded_end + 1]
        region_content = "\n".join(region_lines)

        # Detect label from first significant line
        label = ""
        for line in lines[block_start:min(block_start + 3, len(lines))]:
            stripped = line.strip()
            m = re.match(r'(?:def|class|func|function|async def)\s+(\w+)', stripped)
            if m:
                label = f"function/class: {m.group(1)}"
                break

        regions.append(CodeRegion(
            start_line=padded_start + 1,
            end_line=padded_end + 1,
            content=region_content,
            label=label,
        ))
        covered_ranges.append((padded_start, padded_end))

    # Check if total context is still too large; if so, trim padding
    total = sum(len(r.content) for r in regions)
    if total > max_chars and len(regions) > 1:
        # Reduce padding to fit
        trimmed_regions = [regions[0]]  # Keep header
        for region in regions[1:]:
            content_lines = region.content.splitlines()
            if len(content_lines) > PADDING_LINES * 4:
                # Trim to just the core block with minimal padding
                mid = len(content_lines) // 2
                half = min(len(content_lines) // 2, int(max_chars / len(regions) / 4))
                start = max(0, mid - half)
                end = min(len(content_lines), mid + half)
                trimmed = "\n".join(content_lines[start:end])
                trimmed_regions.append(CodeRegion(
                    start_line=region.start_line + start,
                    end_line=region.start_line + end - 1,
                    content=trimmed,
                    label=region.label,
                ))
            else:
                trimmed_regions.append(region)
        regions = trimmed_regions

    return ExtractedContext(
        file_path=file_path,
        regions=regions,
        total_lines=len(lines),
        is_chunked=True,
    )


def apply_chunked_fix(
    original_content: str,
    context: ExtractedContext,
    fixed_regions_content: str,
) -> str:
    """Apply a chunked fix back to the original file.

    When the LLM fixes only extracted regions, we need to splice the
    fixed regions back into the original file at the correct line numbers.
    """
    if not context.is_chunked:
        # Full file was sent — the LLM response replaces the entire file
        return fixed_regions_content

    original_lines = original_content.splitlines()
    fixed_lines = fixed_regions_content.splitlines()

    # Parse the fixed output to identify which regions were returned
    # The LLM is instructed to return regions with line markers
    # Try to match "### Lines N-M" markers
    region_fixes: List[Tuple[int, int, List[str]]] = []
    current_start = None
    current_end = None
    current_lines: List[str] = []
    in_code_block = False

    for line in fixed_lines:
        m = re.match(r'^###\s*Lines\s+(\d+)\s*-\s*(\d+)', line)
        if m:
            if current_start is not None and current_lines:
                region_fixes.append((current_start, current_end, current_lines))
            current_start = int(m.group(1))
            current_end = int(m.group(2))
            current_lines = []
            in_code_block = False
            continue

        if line.strip().startswith("```") and current_start is not None:
            in_code_block = not in_code_block
            continue

        if current_start is not None and (in_code_block or not line.strip().startswith("```")):
            if in_code_block or (current_start is not None and not line.strip().startswith("###")):
                current_lines.append(line)

    if current_start is not None and current_lines:
        region_fixes.append((current_start, current_end, current_lines))

    if not region_fixes:
        # Fallback: if the LLM didn't use region markers, try to match
        # the regions by position order
        if len(context.regions) == 1:
            # Only one region — replace just that region
            region = context.regions[0]
            start_idx = region.start_line - 1
            end_idx = region.end_line - 1
            result = original_lines[:start_idx] + fixed_lines + original_lines[end_idx + 1:]
            return "\n".join(result)
        else:
            # Multiple regions but no markers — treat as full file replacement
            return fixed_regions_content

    # Apply fixes in reverse order (to preserve line numbers)
    result_lines = list(original_lines)
    for start, end, fix_lines in sorted(region_fixes, key=lambda x: x[0], reverse=True):
        start_idx = start - 1
        end_idx = end  # end is inclusive, so we replace up to end_idx
        # Remove trailing empty lines from fix to avoid adding blanks
        while fix_lines and fix_lines[-1].strip() == "":
            fix_lines.pop()
        result_lines[start_idx:end_idx] = fix_lines

    return "\n".join(result_lines)
