# SonarFix Agent

> Auto-fix SonarQube issues using an LLM — with a **web GUI**, a **CLI**, smart context chunking, RAG-powered examples, and automatic retry with syntax validation.

[![CI](https://github.com/madhupathy/sonarfix-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/madhupathy/sonarfix-agent/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](pyproject.toml)

Paste a PR URL, preview issues from SonarQube, review the planned fixes, then apply them and create a new PR — all from the browser or the command line.

---

## Screenshots

**Dashboard — preview issues and launch fix jobs**

![SonarFix Dashboard](image.png)

**Settings — configure SonarQube, LLM, and Git connections**

![SonarFix Settings](image-1.png)

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  Web GUI (Next.js :3000)  ──or──  CLI (sonarfix)           │
└────────────────────────┬────────────────────────────────────┘
                         │  /api/*
            ┌────────────▼────────────┐
            │  FastAPI Backend (:8000) │
            └────────────┬────────────┘
                         │
   ┌─────────────────────┼──────────────────────┐
   ▼                     ▼                      ▼
SonarQube           Fix Planner           Git Manager
Fetcher          (group & rank)         (clone / branch)
                         │
               ┌─────────▼──────────┐
               │     Fix Graph       │
               │  state machine      │
               └──────┬─────────────┘
                      │
   ┌──────┬───────┬───┴────┬────────┐
   ▼      ▼       ▼        ▼        ▼
Context  RAG    LLM     Syntax   RAG
Extract Fetch  Fixer   Validate  Store
```

### Fix Graph Pipeline (per file)

```
EXTRACT_CONTEXT → RETRIEVE_RAG → BUILD_PROMPT → CALL_LLM → APPLY_FIX → VALIDATE → STORE → DONE
                                       ▲                                    │
                                       └──────── RETRY (up to 3×) ─────────┘
                                                 (with error feedback)
```

---

## Quick Start

### Prerequisites

- Python 3.9+
- Node.js 18+ (web GUI only)
- An OpenAI-compatible LLM endpoint (vLLM, OpenAI, Azure, Ollama)
- SonarQube with API access
- Git Personal Access Token (for cloning private repos)

### Install

```bash
git clone https://github.com/madhupathy/sonarfix-agent.git
cd sonarfix-agent
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env with your SonarQube URL, LLM key, etc.
```

### Start the backend

```bash
uvicorn sonarfix.api:app --host 127.0.0.1 --port 8000 --reload
```

### Start the web GUI (separate terminal)

```bash
cd web
npm install
npm run dev
# → http://localhost:3000
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SONARQUBE_URL` | Yes | SonarQube server URL |
| `SONARQUBE_USERNAME` | Yes | API token (preferred) or username |
| `SONARQUBE_PASSWORD` | Conditional | Basic auth password; leave empty for token auth |
| `LLM_API_KEY` | Yes | `dummy` for vLLM; real key for OpenAI/Azure |
| `LLM_MODEL` | Yes | e.g. `Qwen/Qwen2.5-Coder-32B-Instruct` or `gpt-4o` |
| `LLM_BASE_URL` | Yes | OpenAI-compatible base URL (default: `http://localhost:8000/v1`) |
| `WORKSPACE_DIR` | No | Temp dir for cloned repos (default: `/tmp/sonarfix-workspaces`) |

> ⚠️ **Never commit `.env`** — it is gitignored. Configure credentials via the GUI Settings page or `.env` file only.

---

## CLI Usage

```bash
# Preview issues (dry run)
sonarfix issues my-project --branch main
sonarfix issues my-project --pr 42 --severity BLOCKER,CRITICAL

# Full auto-fix pipeline
sonarfix run my-project \
  --repo git@github.com:org/repo.git \
  --branch main \
  --severity BLOCKER,CRITICAL,MAJOR \
  --type BUG,VULNERABILITY \
  --max 30

# Dry run (plan only, no changes)
sonarfix run my-project --repo git@github.com:org/repo.git --branch main --dry-run

# Push fix branch automatically
sonarfix run my-project --repo git@github.com:org/repo.git --branch main --auto-push
```

| Command | Description |
|---|---|
| `sonarfix issues` | List SonarQube issues for a project/branch/PR |
| `sonarfix run` | Full pipeline: fetch → clone → plan → fix → validate → report |
| `sonarfix validate` | Run syntax checks on modified files |
| `sonarfix branches` | List branches for a SonarQube project |
| `sonarfix prs` | List pull requests for a SonarQube project |

---

## Supported LLM Providers

| Provider | Example Base URL | API Key |
|---|---|---|
| vLLM (self-hosted) | `http://localhost:8000/v1` | `dummy` |
| OpenAI | `https://api.openai.com/v1` | `sk-...` |
| Azure OpenAI | `https://<resource>.openai.azure.com/...` | Azure key |
| Ollama | `http://localhost:11434/v1` | `dummy` |

Best results with `Qwen/Qwen2.5-Coder-32B-Instruct` (vLLM) or `gpt-4o` (OpenAI).

---

## Key Features

| Feature | Description |
|---|---|
| **Smart Context Chunking** | Large files chunked to imports + relevant function blocks — avoids context-window errors |
| **Retry with Feedback** | Up to 3 automatic retries; syntax errors fed back into the prompt for self-correction |
| **Post-Fix Validation** | Every fix is syntax-checked (`py_compile`, `go vet`, `bash -n`); invalid code is rolled back |
| **RAG Store** | SQLite at `~/.sonarfix/rag.db` — past fixes used as few-shot examples on future runs |
| **RAG Standards** | Upload coding standard docs via API; retrieved and injected into prompts |
| **State Machine** | Each file fix is a deterministic graph traversal, not a single LLM call |

---

## Project Structure

```
sonarfix/
├── api.py                  # FastAPI backend
├── cli.py                  # Typer CLI
├── config.py               # Pydantic settings
├── sonarqube/              # SonarQube API client + models + filters
├── fixer/                  # Fix Graph state machine + LLM fixer + chunking
├── git/                    # GitPython wrapper
├── rag/                    # SQLite RAG store
├── validator/              # Syntax validation
└── reporting/              # JSON + Markdown reports

web/                        # Next.js 14 + Tailwind + shadcn/ui
tests/                      # 93 pytest tests
```

---

## Running Tests

```bash
pytest -v
# 93 tests across 9 test files
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Quick summary:

1. Fork → branch → commit → PR
2. Run `pytest -v` before pushing
3. Never commit `.env` files or credentials

---

## License

MIT — see [pyproject.toml](pyproject.toml).
