# SonarFix Agent

Auto-fix SonarQube issues using an LLM (via vLLM or any OpenAI-compatible API).
Includes a **web GUI** and a **CLI** — use whichever fits your workflow.

> Paste a PR URL, preview issues from SonarQube, review fixes,
> then apply them and create a new PR — all from the browser.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Web GUI (Next.js :3000)  ──or──  CLI (sonarfix)                            │
└────────────────────────────┬─────────────────────────────────────────────────┘
                             │  /api/*
                ┌────────────▼────────────┐
                │  FastAPI Backend (:8000) │
                └────────────┬────────────┘
                             │
┌─────────────┐      ┌──────▼───────┐      ┌──────────────────┐
│  Settings /  │─────▶│  SonarQube   │─────▶│   Fix Planner    │
│  Connections │      │  Fetcher     │      │  (group & rank)  │
└─────────────┘      └──────────────┘      └────────┬─────────┘
                                                     │
                                           ┌─────────▼──────────┐
                                           │     Fix Graph       │
                                           │  (state machine)    │
                                           └─────────┬──────────┘
                                                     │
         ┌───────────┬──────────┬──────────┬─────────┴────────┐
         ▼           ▼          ▼          ▼                  ▼
   ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────┐      ┌──────────┐
   │ Context  │ │  RAG   │ │  LLM   │ │Syntax  │      │  RAG     │
   │ Extractor│ │Retrieve│ │ Fixer  │ │Validate│      │  Store   │
   │ (chunk)  │ │(past   │ │(vLLM / │ │+ Retry │      │(save fix)│
   │          │ │ fixes) │ │OpenAI) │ │  loop  │      │          │
   └──────────┘ └────────┘ └────────┘ └────────┘      └──────────┘
                                                              │
       ┌──────────────┐      ┌──────────────────┐            │
       │  Git Manager │─────▶│  Commit + Push   │◀───────────┘
       │  (clone/br)  │      │  & Create PR     │
       └──────────────┘      └──────────────────┘
```

### Fix Graph Pipeline (per file)

Each file goes through a **state-machine pipeline** with automatic retry:

```
EXTRACT_CONTEXT → RETRIEVE_RAG → BUILD_PROMPT → CALL_LLM → APPLY_FIX → VALIDATE → STORE_SUCCESS → DONE
                                      ▲                                     │
                                      │              RETRY (up to 3x)       │
                                      └─────────────────────────────────────┘
                                        (with error feedback in prompt)
```

- **EXTRACT_CONTEXT** — For small files, sends the full content. For large files (>12K tokens), extracts only the imports/header + the function/class block containing each issue line.
- **RETRIEVE_RAG** — Queries the RAG store for similar past fixes (exact rule match + cosine similarity) and relevant coding standards.
- **BUILD_PROMPT** — Constructs the LLM prompt with file context, issue descriptions (including full rule name + description), RAG examples, and retry error feedback (if retrying).
- **CALL_LLM** — Sends the prompt to the LLM API. Context-window errors are terminal (no retry). Other errors trigger retry.
- **APPLY_FIX** — For full files, replaces the content. For chunked files, splices fixed regions back into the original using line markers.
- **VALIDATE** — Runs language-specific syntax checks (`py_compile`, `bash -n`, `go vet`, etc.). On failure, restores original file and retries with the syntax error fed back to the LLM.
- **STORE_SUCCESS** — Saves the successful fix (before/after snippets per issue) into the RAG store for future retrieval.

**Flow**: Fetch SonarQube issues → Resolve repo (local auto-detect → HTTPS+token clone) → Group by file & severity → Run Fix Graph per file (with chunking, RAG, retry, validation) → Commit to fix branch → Push & Create PR.

## Key Features (v2)

| Feature | Description |
|---------|-------------|
| **Smart Context Chunking** | Large files are automatically chunked — only imports + relevant function blocks are sent to the LLM. Fixes the "context window exceeded" crash for files >16K tokens. |
| **Retry with Feedback** | Up to 3 automatic retries per file. Syntax errors and LLM failures are fed back into the prompt so the LLM can self-correct. |
| **Post-Fix Validation** | Every fix is syntax-checked immediately. Invalid code is rolled back and retried with the error message. |
| **Enhanced Prompts** | Full SonarQube rule name + description included. Separate prompt templates for full-file vs chunked mode. |
| **RAG Store** | SQLite database (`~/.sonarfix/rag.db`) indexes every successful fix. On future runs, similar past fixes are retrieved and included as few-shot examples. |
| **RAG Standards** | Upload coding standard documents via API. They're retrieved and included in prompts when relevant. |
| **State Machine** | Each file fix is a graph traversal (not a single LLM call). The pipeline handles errors, retries, and validation deterministically. |

## Quick Start

### Prerequisites

- **Python 3.9+**
- **Node.js 18+** (for the web GUI)
- **LLM endpoint (OpenAI-compatible)** — e.g. vLLM, OpenAI, Azure OpenAI
- **SonarQube credentials** — token (recommended) or username/password
- **Git Personal Access Token** — for HTTPS cloning of private repos (not needed if repo is already cloned locally)

### LLM Server Setup

SonarFix works with any **OpenAI-compatible** LLM endpoint. Recommended options:

| Provider | Example Base URL | API Key |
|----------|------------------|---------|
| vLLM (self-hosted) | `http://localhost:8000/v1` | `dummy` |
| OpenAI | `https://api.openai.com/v1` | `sk-...` |
| Azure OpenAI | `https://<resource>.openai.azure.com/...` | Your Azure key |
| Ollama | `http://localhost:11434/v1` | `dummy` |

For best results with code fixes, use a code-specialized model like `Qwen/Qwen2.5-Coder-32B-Instruct` (vLLM) or `gpt-4o` (OpenAI).

```bash
# Verify your LLM endpoint
curl http://localhost:8000/v1/models

# Quick test
curl http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer dummy' \
  -d '{"model":"Qwen/Qwen2.5-Coder-32B-Instruct","messages":[{"role":"user","content":"Say hi"}],"max_tokens":5}'
```

### Install

```bash
cd ~/sonarfix-agent
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

| Variable | Description |
|----------|-------------|
| `SONARQUBE_URL` | SonarQube server URL |
| `SONARQUBE_USERNAME` | Basic auth username **or** API token (for token auth, use token here with empty password) |
| `SONARQUBE_PASSWORD` | Basic auth password (leave empty for token auth) |
| `LLM_API_KEY` | LLM API key (`dummy` for vLLM, real key for OpenAI/Azure) |
| `LLM_MODEL` | Model name (e.g. `Qwen/Qwen2.5-Coder-32B-Instruct`, `gpt-4o`) |
| `LLM_BASE_URL` | OpenAI-compatible API base URL (default: `http://localhost:8000/v1`) |
| `WORKSPACE_DIR` | Temp directory for cloned repos |

> **Tip**: The GUI Settings page is the easiest way to configure credentials — no `.env` file needed.

## Running the Web GUI

No deployment needed — everything runs locally.

### 1. Install frontend dependencies (one-time)

```bash
cd ~/sonarfix-agent/web
npm install
```

### 2. Start the backend API

```bash
cd ~/sonarfix-agent
uvicorn sonarfix.api:app --host 127.0.0.1 --port 8000 --reload
```

### 3. Start the frontend (in a second terminal)

```bash
cd ~/sonarfix-agent/web
npm run dev
```

### 4. Open your browser

Go to **http://localhost:3000** (or `:3001` if 3000 is in use).

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Enter project key + repo URL, preview issues, start fix jobs |
| Settings | `/settings` | Configure connections for SonarQube, LLM, and Git |

### GUI Features

- **Smart URL Input** — paste any PR/branch/repo web page URL and it auto-extracts the SSH clone URL, PR ID, and branch name
- **Connectors** — SonarQube (basic auth / token / SSO-SAML), LLM (API key + model + base URL), Git (PAT / basic / SSO-SAML)
- **Test Connection** — validate credentials before saving
- **Issue Preview** — fetch and browse SonarQube issues in a sortable table before fixing
- **Fix Jobs** — launch fixes in background with live log polling
- **Dry Run** — preview which files and issues will be fixed before committing
- **Apply Fixes** — LLM reads each file + its SonarQube issues, returns fixed code, writes it directly
- **Push & Create PR** — after fixes are applied, push the fix branch and auto-create a PR on your Git host (GitHub Enterprise API)
- **Local Repo Auto-Detect** — if a repo matching the URL exists in `~/`, it's used directly (no clone needed)
- **HTTPS + Token Clone** — if no local clone found, clones via HTTPS using your Git PAT from Settings

> Credentials are stored locally in `~/.sonarfix/connections.json` and synced to env vars at runtime.
> RAG data is stored in `~/.sonarfix/rag.db` (SQLite).

### Recommended Workflow

1. **Settings** → configure SonarQube (token auth), LLM (vLLM defaults pre-filled), and Git (Personal Access Token)
2. **Dashboard** → paste PR URL (e.g. `https://your-git-host/org/repo/pull/123`)
3. Enter the **SonarQube Project Key** (find it in the SonarQube dashboard URL, e.g. `my-org::my-project`)
4. Click **Preview** to see issues
5. Click **Fix Issues** — the graph pipeline fixes each file with chunking, RAG, retries, and validation
6. Review the results and diff in the GUI
7. Click **Push & Create PR** to push and open a PR

> Each successful fix is automatically stored in the RAG database. Subsequent runs benefit from these past examples.

### Uploading Coding Standards (optional)

You can seed the RAG store with your project's coding standards so the LLM has domain-specific context:

```bash
curl -X POST http://localhost:8000/api/rag/standards \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "project-standards",
    "title": "Error handling policy",
    "content": "All functions must return errors, not panic. Use fmt.Errorf with %w for wrapping.",
    "language": "go"
  }'
```

Check RAG store stats:

```bash
curl http://localhost:8000/api/rag/stats
# {"fix_examples": 42, "standard_docs": 5}
```

---

## CLI Usage

### List issues (dry run)

```bash
sonarfix issues my-project --branch main
sonarfix issues my-project --pr 42 --severity BLOCKER,CRITICAL
```

### Full auto-fix pipeline

```bash
# Fix issues on a branch
sonarfix run my-project --repo git@github.com:org/repo.git --branch develop

# Fix issues on a PR
sonarfix run my-project --repo git@github.com:org/repo.git --pr 42

# With filters
sonarfix run my-project --repo git@github.com:org/repo.git \
  --branch main \
  --severity BLOCKER,CRITICAL,MAJOR \
  --type BUG,VULNERABILITY \
  --max 30

# Dry run (generate instructions only)
sonarfix run my-project --repo git@github.com:org/repo.git --branch main --dry-run

# Use existing local repo
sonarfix run my-project --repo git@github.com:org/repo.git --branch main --local /path/to/repo

# Auto-push fix branch
sonarfix run my-project --repo git@github.com:org/repo.git --branch main --auto-push
```

### Validate a workspace

```bash
sonarfix validate /tmp/sonarfix-workspaces/my-repo
```

### List branches and PRs

```bash
sonarfix branches my-project
sonarfix prs my-project
```

## Commands

| Command | Description |
|---------|-------------|
| `sonarfix issues` | List SonarQube issues for a project/branch/PR |
| `sonarfix run` | Full pipeline: fetch → clone → plan → fix (with graph) → validate → report |
| `sonarfix validate` | Run syntax checks on modified files |
| `sonarfix branches` | List branches for a SonarQube project |
| `sonarfix prs` | List pull requests for a SonarQube project |

## How It Works

1. **Fetch** — Pulls open issues from SonarQube API (`/api/issues/search`) with pagination, filtered by severity/type
2. **Resolve Repo** — Auto-detects local clones in `~/` or `WORKSPACE_DIR`, falls back to HTTPS+token clone
3. **Plan** — Groups issues by file, ranks by severity, enriches with rule descriptions from `/api/rules/show`
4. **Fix (Graph Pipeline)** — For each file, runs the Fix Graph state machine:
   - **Extract Context** — Small files sent in full; large files chunked to imports + relevant blocks
   - **Retrieve RAG** — Fetches similar past fixes (exact rule match, then cosine similarity) and coding standards
   - **Build Prompt** — Assembles system + user prompt with context, issues, RAG examples, and retry feedback
   - **Call LLM** — Sends to vLLM/OpenAI API (`/v1/chat/completions`), receives fixed code
   - **Apply Fix** — Writes fixed content (full replacement or region splice for chunked files)
   - **Validate** — Syntax check; on failure, rolls back and retries with error feedback (up to 3x)
   - **Store Success** — Saves fix to RAG store for future runs
5. **Report** — Generates `fix-report.json` and `fix-report.md` with fix summary, diff stats, and batch details
6. **Commit** — Creates a `sonarfix/<branch>-<timestamp>` branch with all fixes
7. **Push & PR** — (GUI) Pushes fix branch and creates a PR via GitHub Enterprise API

## Project Structure

```
sonarfix/
├── cli.py                      # Typer CLI
├── api.py                      # FastAPI backend (bridges GUI ↔ CLI, RAG endpoints)
├── config.py                   # Pydantic settings (.env)
├── sonarqube/
│   ├── client.py               # SonarQube API client (httpx, basic auth)
│   ├── models.py               # Pydantic models (Issue, Rule, Component)
│   └── filters.py              # Group/rank/deduplicate issues
├── git/
│   └── manager.py              # GitPython wrapper
├── fixer/
│   ├── llm_fixer.py            # LLM fixer — delegates to FixGraph pipeline
│   ├── graph.py                # Fix Graph state machine (extract → RAG → LLM → validate → retry)
│   ├── context_extractor.py    # Smart chunking (imports + function blocks for large files)
│   ├── prompt.py               # Prompt templates (legacy, used by planner)
│   └── planner.py              # Batch orchestrator
├── rag/
│   ├── __init__.py
│   └── store.py                # SQLite-backed RAG store (fix examples + coding standards)
├── validator/
│   └── checker.py              # Syntax checks + diff summary
└── reporting/
    └── report.py               # JSON + Markdown reports

web/                            # Next.js frontend
├── app/
│   ├── layout.tsx              # Root layout + Toaster
│   ├── page.tsx                # Dashboard (issue preview, fix jobs)
│   ├── settings/page.tsx       # Connectors (SonarQube, LLM, Git)
│   └── globals.css             # Tailwind + gradient/blob animations
├── components/ui/              # shadcn/ui primitives
├── lib/utils.ts                # cn() helper
├── tailwind.config.ts
├── next.config.mjs             # API proxy rewrite to :8000
└── package.json

tests/                          # 93 tests
├── test_filters.py             # Issue filter/sort/group tests
├── test_models.py              # Pydantic model parsing tests
├── test_llm_fixer.py           # LLMFixer init + strip code fences
├── test_planner.py             # Prompt building + file context reading
├── test_context_extractor.py   # Smart chunking, block detection, apply chunked fix
├── test_graph.py               # FixGraph state machine (success, retry, failure, context errors)
├── test_rag_store.py           # RAG store CRUD, embeddings, similarity search
├── test_report.py              # Report generation
└── test_windsurf.py            # Legacy WindsurfResult
```

## Tech Stack

| Component | Technology |
|-----------|----------|
| Language | Python 3.9+ |
| CLI | Typer + Rich |
| Config | pydantic-settings |
| HTTP | httpx |
| Git | GitPython |
| Code Fixer | LLM via OpenAI-compatible API (default: vLLM + Qwen2.5-Coder-32B) |
| Fix Pipeline | Graph-based state machine with retry + validation |
| RAG Store | SQLite (`~/.sonarfix/rag.db`) with pseudo-embeddings + cosine similarity |
| Backend API | FastAPI + Uvicorn |
| Frontend | Next.js 14, React 18, Tailwind CSS 3, shadcn/ui |
| Icons | Lucide React |
| Notifications | Sonner (toast) |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/connections` | List connector status |
| `POST` | `/api/connections/{connector}` | Save credentials |
| `DELETE` | `/api/connections/{connector}` | Disconnect |
| `POST` | `/api/connections/{connector}/test` | Test credentials |
| `GET` | `/api/issues` | Preview SonarQube issues |
| `POST` | `/api/jobs` | Start a fix job |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/jobs/{id}` | Get job status + logs |
| `POST` | `/api/jobs/{id}/apply` | Re-run a dry-run job as actual fix |
| `POST` | `/api/jobs/{id}/push` | Push fix branch + create PR |
| `GET` | `/api/rag/stats` | RAG store statistics (fix example + standard doc counts) |
| `POST` | `/api/rag/standards` | Upload a coding standard document for RAG |
| `GET` | `/api/rag/fixes` | List stored fix examples (optionally filter by `rule_key`) |

## Running Tests

```bash
pytest -v
# 93 tests across 9 test files
```
