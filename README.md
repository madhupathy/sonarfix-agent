<div align="center">

# SonarFix Agent

**Stop reviewing SonarQube issues one by one. Fix them all in one command.**

SonarFix Agent connects to your SonarQube instance, groups open issues by file and severity, and sends each file through an LLM-powered fix pipeline — context extraction, RAG-retrieved past fixes, retry with syntax validation — then commits a clean fix branch and opens a PR.

[![CI](https://github.com/madhupathy/sonarfix-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/madhupathy/sonarfix-agent/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](pyproject.toml)

</div>

---

## The Problem

Every team with SonarQube has the same backlog. Hundreds of open issues — BUGs, VULNERABILITIEs, CODE_SMELLs — that everyone acknowledges but nobody fixes because:

- **Each fix requires context**: open the file, find the issue, understand the rule, apply the fix
- **Batch fixing is tedious**: 40 issues across 15 files means 15 separate PRs or one massive untraceable commit
- **False positives waste time**: you have to preview issues before deciding what to automate
- **LLM fixes break syntax**: naive "just ask GPT" approaches produce code that doesn't compile

SonarFix Agent solves this with a pipeline that handles context intelligently, validates every fix before committing, and learns from past successes.

---

## Screenshots

**Dashboard — preview issues and launch fix jobs**

![SonarFix Dashboard](image.png)

**Settings — configure SonarQube, LLM, and Git connections**

![SonarFix Settings](image-1.png)

---

## How It Works

Paste your PR URL. Preview the open SonarQube issues. Click Fix. The agent runs each file through a state-machine pipeline:

```
For each file with issues:

  EXTRACT CONTEXT
  ├── Small files  → send full content to LLM
  └── Large files  → extract imports + the function/class containing each issue line
         │
         ▼
  RETRIEVE RAG
  ├── Exact rule match from past successful fixes
  └── Cosine-similarity search for similar fixes
         │
         ▼
  BUILD PROMPT
  └── File context + SonarQube rule name/description + RAG examples + retry feedback
         │
         ▼
  CALL LLM  (vLLM / OpenAI / Azure / Ollama)
         │
         ▼
  APPLY FIX
  ├── Small files  → replace full content
  └── Large files  → splice fixed regions back into original using line markers
         │
         ▼
  VALIDATE  (py_compile / go vet / bash -n / etc.)
  ├── PASS → store fix in RAG database → DONE
  └── FAIL → restore original → retry with error fed back to LLM (up to 3×)
```

Every successful fix is stored in a local SQLite RAG database. Future runs on the same codebase get better results because the LLM sees your team's own past solutions as few-shot examples.

---

## Quick Start

### Prerequisites
- Python 3.9+
- Node.js 18+ (web GUI only)
- A running SonarQube instance with API access
- An OpenAI-compatible LLM endpoint (self-hosted vLLM, OpenAI, Azure, Ollama)
- A Git Personal Access Token for creating PRs

### Install

```bash
git clone https://github.com/madhupathy/sonarfix-agent.git
cd sonarfix-agent
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env — or skip this and use the GUI Settings page
```

### Option A — Web GUI (recommended)

```bash
# Terminal 1: backend
uvicorn sonarfix.api:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: frontend
cd web && npm install && npm run dev
# → http://localhost:3000
```

**Workflow:**
1. **Settings** → configure SonarQube (token auth recommended), LLM, and Git PAT
2. **Dashboard** → paste a PR URL — e.g. `https://github.com/org/repo/pull/42`
3. Enter the **SonarQube Project Key** (from the SonarQube URL)
4. Click **Preview** to see open issues
5. Click **Fix Issues** — watch the pipeline run per file
6. Review the diff, then click **Push & Create PR**

### Option B — CLI

```bash
# Preview issues
sonarfix issues my-project --branch main --severity BLOCKER,CRITICAL

# Full fix pipeline
sonarfix run my-project \
  --repo git@github.com:org/repo.git \
  --branch main \
  --severity BLOCKER,CRITICAL,MAJOR \
  --type BUG,VULNERABILITY

# Dry run — plan without writing any files
sonarfix run my-project --repo git@github.com:org/repo.git --branch main --dry-run
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SONARQUBE_URL` | Yes | Your SonarQube server URL |
| `SONARQUBE_USERNAME` | Yes | API token (preferred) or username |
| `SONARQUBE_PASSWORD` | Conditional | Leave empty when using token auth |
| `LLM_API_KEY` | Yes | `dummy` for vLLM; real key for OpenAI/Azure |
| `LLM_MODEL` | Yes | e.g. `Qwen/Qwen2.5-Coder-32B-Instruct` or `gpt-4o` |
| `LLM_BASE_URL` | Yes | OpenAI-compatible base URL |
| `WORKSPACE_DIR` | No | Temp directory for cloned repos (default: `/tmp/sonarfix-workspaces`) |

> **Never commit `.env`** — it is gitignored. Use the GUI Settings page or `.env.example` only.

---

## Supported LLM Providers

| Provider | Base URL | API Key |
|----------|----------|---------|
| vLLM (self-hosted) | `http://localhost:8000/v1` | `dummy` |
| OpenAI | `https://api.openai.com/v1` | `sk-...` |
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/deployments/<deployment>/` | Azure key |
| Ollama | `http://localhost:11434/v1` | `dummy` |

Best results: `Qwen/Qwen2.5-Coder-32B-Instruct` (vLLM) or `gpt-4o` (OpenAI).

---

## What Makes This Different from "Just Asking ChatGPT"

| Naive LLM approach | SonarFix Agent |
|--------------------|----------------|
| Send whole file → get whole file back | Smart chunking — only sends relevant function blocks for large files |
| One shot, hope it works | Up to 3 retries with the syntax error fed back to the LLM |
| No validation | Every fix syntax-checked before committing (`py_compile`, `go vet`, `bash -n`, etc.) |
| No learning | SQLite RAG database — past fixes used as few-shot examples on future runs |
| Manual PR creation | Automatic fix branch + PR creation via GitHub API |

---

## RAG Store

SonarFix builds a local database of successful fixes at `~/.sonarfix/rag.db`. On each run:
- **Exact rule match**: if your codebase previously fixed `java:S2095`, that fix is retrieved
- **Cosine similarity**: similar code patterns from other rules are retrieved as secondary examples

You can also seed it with your team's coding standards:

```bash
curl -X POST http://localhost:8000/api/rag/standards \
  -H 'Content-Type: application/json' \
  -d '{"source": "team-standards", "title": "Error handling policy", "content": "All errors must be logged before returning. Use structured logging.", "language": "python"}'
```

---

## Project Structure

```
sonarfix/
├── api.py                  # FastAPI backend (bridges GUI ↔ fix pipeline, RAG endpoints)
├── cli.py                  # Typer CLI
├── config.py               # Pydantic settings from .env
├── sonarqube/
│   ├── client.py           # Paginated SonarQube API client
│   ├── models.py           # Issue, Rule, Branch, PullRequest models
│   └── filters.py          # Group by file, rank by severity, deduplicate
├── fixer/
│   ├── graph.py            # Fix Graph state machine (the core pipeline)
│   ├── context_extractor.py # Smart chunking for large files
│   ├── llm_fixer.py        # LLM API integration
│   └── planner.py          # Batch orchestrator
├── rag/
│   └── store.py            # SQLite RAG store with cosine similarity
├── validator/
│   └── checker.py          # Multi-language syntax validation
└── reporting/
    └── report.py           # JSON + Markdown fix reports

web/                        # Next.js 14 + Tailwind + shadcn/ui
tests/                      # 93 tests across 9 test files
```

---

## Running Tests

```bash
pytest -v
# 93 tests across 9 test files
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run `pytest -v` before submitting a PR. Never commit `.env` files.

## License

MIT
