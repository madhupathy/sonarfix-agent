# Contributing to SonarFix Agent

Thank you for your interest! SonarFix Agent is a Python + Next.js project that auto-fixes SonarQube issues using LLM-powered code generation.

## Getting Started

1. **Fork** the repository and clone your fork.
2. Create a branch:
   ```bash
   git checkout -b feature/my-feature
   # or
   git checkout -b fix/issue-123
   ```
3. Make your changes, then open a **Pull Request** against `main`.

## Development Setup

### Prerequisites
- Python 3.9+
- Node.js 18+ (for the web GUI)
- An OpenAI-compatible LLM endpoint (vLLM, OpenAI, Ollama, etc.)
- A SonarQube instance with API access

### Backend
```bash
pip install -e ".[dev]"
cp .env.example .env   # fill in your credentials
uvicorn sonarfix.api:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend
```bash
cd web
npm install
npm run dev   # → http://localhost:3000
```

### Tests
```bash
pytest -v   # 93 tests across 9 test files
```

## Code Style

- **Python**: use `ruff` for linting (`ruff check .`) and formatting (`ruff format .`).
- **TypeScript**: run `npm run lint` inside `web/`.
- Follow existing module patterns — `client.py` for API clients, `models.py` for Pydantic models, `graph.py` for state-machine logic.

## Security

- **Never** commit `.env` files, API keys, tokens, SonarQube passwords, or Git PATs.
- Only add placeholder values to `.env.example`.
- If you accidentally expose a secret, rotate it immediately and open an issue.

## Commit Messages

Use the conventional commits format:

```
feat: add Azure OpenAI support
fix: handle context-window error in chunked mode
docs: add RAG upload example to README
test: add graph retry-on-syntax-error test
```

## Pull Request Checklist

- [ ] `pytest -v` passes
- [ ] No new lint warnings
- [ ] `.env.example` updated if new variables were added
- [ ] README updated if new commands or features were added
- [ ] No hardcoded secrets, tokens, or passwords

## Reporting Bugs

Open a [GitHub Issue](../../issues/new?template=bug_report.md) with:
- Steps to reproduce
- Expected vs actual behavior
- Python version, OS, and LLM provider

## License

By contributing, you agree your contributions will be licensed under the [MIT License](../../blob/main/pyproject.toml).
