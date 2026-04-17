"""FastAPI backend API — bridges the web GUI to sonarfix internals."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="SonarFix Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory stores (lightweight, no DB needed)
# ---------------------------------------------------------------------------

_connections: Dict[str, Dict[str, Any]] = {}
_jobs: Dict[str, Dict[str, Any]] = {}
_settings_path = Path.home() / ".sonarfix" / "connections.json"


def _load_connections() -> Dict[str, Dict[str, Any]]:
    global _connections
    if _settings_path.exists():
        try:
            _connections = json.loads(_settings_path.read_text())
        except Exception:
            _connections = {}
    return _connections


def _save_connections() -> None:
    _settings_path.parent.mkdir(parents=True, exist_ok=True)
    _settings_path.write_text(json.dumps(_connections, indent=2))


_load_connections()


# Auto-populate LLM connection from config defaults if not already saved
def _auto_populate_llm():
    from sonarfix.config import get_settings
    cfg = get_settings()
    if "llm" not in _connections and cfg.llm_api_key:
        _connections["llm"] = {
            "auth_type": "token",
            "status": "connected",
            "llm_api_key": cfg.llm_api_key,
            "llm_model": cfg.llm_model,
            "llm_base_url": cfg.llm_base_url,
        }
        _save_connections()


_auto_populate_llm()

# Sync saved credentials to env vars on startup
if _connections:
    # Deferred call — _sync_env is defined below, so use a startup event
    @app.on_event("startup")
    def _startup_sync():
        _sync_env()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ConnectionConfig(BaseModel):
    auth_type: str  # "basic", "token", "sso_saml"
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    sso_url: Optional[str] = None
    sso_entity_id: Optional[str] = None
    config_dir: Optional[str] = None
    llm_api_key: Optional[str] = None  # LLM API key (vLLM: "dummy", OpenAI: real key)
    llm_model: Optional[str] = None  # e.g. Qwen/Qwen2.5-Coder-32B-Instruct, gpt-4o
    llm_base_url: Optional[str] = None  # OpenAI-compatible endpoint


class ConnectionTestRequest(BaseModel):
    connector: str
    config: ConnectionConfig


class FixJobRequest(BaseModel):
    project_key: str
    repo_url: str
    branch: Optional[str] = None
    pull_request: Optional[str] = None
    severities: Optional[List[str]] = None
    types: Optional[List[str]] = None
    max_issues: int = 50
    dry_run: bool = False
    local_repo: Optional[str] = None


# ---------------------------------------------------------------------------
# Connection endpoints
# ---------------------------------------------------------------------------


@app.get("/api/connections")
def get_connections():
    """Return status of all connectors."""
    defaults = {
        "sonarqube": {
            "name": "SonarQube",
            "description": "Code quality & security analysis",
            "status": "not_connected",
            "auth_type": None,
        },
        "llm": {
            "name": "LLM",
            "description": "AI-powered code fixing via vLLM / OpenAI API",
            "status": "not_connected",
            "auth_type": None,
        },
        "git": {
            "name": "Git / SCM",
            "description": "Source code repository access",
            "status": "not_connected",
            "auth_type": None,
        },
    }
    for key, conn in _connections.items():
        if key in defaults:
            defaults[key]["status"] = conn.get("status", "not_connected")
            defaults[key]["auth_type"] = conn.get("auth_type")
            if conn.get("url"):
                defaults[key]["url"] = conn["url"]
    return {"connections": defaults}


@app.post("/api/connections/{connector}")
def save_connection(connector: str, config: ConnectionConfig):
    """Save connector configuration."""
    if connector not in ("sonarqube", "llm", "git"):
        raise HTTPException(400, f"Unknown connector: {connector}")

    entry: Dict[str, Any] = {
        "auth_type": config.auth_type,
        "status": "connected",
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    if config.url:
        entry["url"] = config.url
    if config.username:
        entry["username"] = config.username
    if config.password:
        entry["password"] = config.password
    if config.token:
        entry["token"] = config.token
    if config.sso_url:
        entry["sso_url"] = config.sso_url
    if config.sso_entity_id:
        entry["sso_entity_id"] = config.sso_entity_id
    if config.config_dir:
        entry["config_dir"] = config.config_dir
    if config.llm_api_key:
        entry["llm_api_key"] = config.llm_api_key
    if config.llm_model:
        entry["llm_model"] = config.llm_model
    if config.llm_base_url:
        entry["llm_base_url"] = config.llm_base_url

    _connections[connector] = entry
    _save_connections()

    # Push to sonarfix env config
    _sync_env()

    return {"status": "connected", "connector": connector}


@app.delete("/api/connections/{connector}")
def disconnect(connector: str):
    """Disconnect a connector."""
    if connector in _connections:
        del _connections[connector]
        _save_connections()
        _sync_env()
    return {"status": "disconnected", "connector": connector}


@app.post("/api/connections/{connector}/test")
def test_connection(connector: str, config: ConnectionConfig):
    """Test a connector's credentials without saving."""
    if connector == "sonarqube":
        return _test_sonarqube(config)
    elif connector == "llm":
        return _test_llm(config)
    elif connector == "git":
        return _test_git(config)
    raise HTTPException(400, f"Unknown connector: {connector}")


def _test_sonarqube(config: ConnectionConfig) -> dict:
    import httpx
    url = (config.url or "").rstrip("/")
    if not url:
        return {"success": False, "message": "URL is required"}
    try:
        auth = None
        if config.auth_type == "basic" and config.username and config.password:
            auth = (config.username, config.password)
        elif config.auth_type == "token" and config.token:
            auth = (config.token, "")
        r = httpx.get(f"{url}/api/system/status", auth=auth, verify=False, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {"success": True, "message": f"Connected — {data.get('status', 'UP')}"}
        return {"success": False, "message": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}


def _test_llm(config: ConnectionConfig) -> dict:
    llm_key = config.llm_api_key or ""
    if not llm_key:
        return {"success": False, "message": "LLM API Key is required. Enter it in the 'LLM API Key' field."}
    # Try a lightweight LLM API call to verify the key works
    try:
        import httpx
        base_url = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        model = config.llm_model or "gpt-4o"
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=15.0,
            verify=False,
        )
        if resp.status_code == 200:
            return {"success": True, "message": f"LLM API connected (model: {model})"}
        elif resp.status_code == 401:
            return {"success": False, "message": "Invalid API key (401 Unauthorized)"}
        else:
            return {"success": False, "message": f"LLM API returned HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": f"LLM API error: {str(e)[:200]}"}


def _test_git(config: ConnectionConfig) -> dict:
    if config.auth_type == "sso_saml":
        return {"success": True, "message": "SSO/SAML configured (will authenticate on clone)"}
    if config.auth_type == "token" and config.token:
        return {"success": True, "message": "Token stored (will authenticate on clone)"}
    if config.auth_type == "basic" and config.username:
        return {"success": True, "message": "Credentials stored (will authenticate on clone)"}
    return {"success": False, "message": "No credentials provided"}


def _sync_env():
    """Push connection settings into env vars for sonarfix config module."""
    sq = _connections.get("sonarqube", {})
    if sq.get("url"):
        os.environ["SONARQUBE_URL"] = sq["url"]

    # SonarQube token auth: token goes as username, empty password
    if sq.get("auth_type") == "token" and sq.get("token"):
        os.environ["SONARQUBE_USERNAME"] = sq["token"]
        os.environ["SONARQUBE_PASSWORD"] = ""
    else:
        if sq.get("username"):
            os.environ["SONARQUBE_USERNAME"] = sq["username"]
        if sq.get("password"):
            os.environ["SONARQUBE_PASSWORD"] = sq["password"]

    llm_conn = _connections.get("llm", {})

    # LLM settings
    if llm_conn.get("llm_api_key"):
        os.environ["LLM_API_KEY"] = llm_conn["llm_api_key"]
    if llm_conn.get("llm_model"):
        os.environ["LLM_MODEL"] = llm_conn["llm_model"]
    if llm_conn.get("llm_base_url"):
        os.environ["LLM_BASE_URL"] = llm_conn["llm_base_url"]

    # Reset config singleton so new env vars take effect
    import sonarfix.config as cfg_mod
    cfg_mod._settings = None


# ---------------------------------------------------------------------------
# Fix job endpoints
# ---------------------------------------------------------------------------


@app.post("/api/jobs")
def create_job(req: FixJobRequest):
    """Start a fix job (runs in background thread)."""
    job_id = str(uuid.uuid4())[:8]
    job: Dict[str, Any] = {
        "id": job_id,
        "status": "queued",
        "request": req.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "log": [],
        "result": None,
    }
    _jobs[job_id] = job

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    """List all jobs (newest first)."""
    jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return {"jobs": [{k: v for k, v in j.items() if k != "log"} for j in jobs[:50]]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Get full job details including log."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


def _run_job(job_id: str) -> None:
    """Execute a fix job in a background thread."""
    job = _jobs[job_id]
    req = FixJobRequest(**job["request"])
    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()

    def log(msg: str):
        job["log"].append({"ts": datetime.now(timezone.utc).isoformat(), "msg": msg})

    try:
        # Re-sync connections → env vars → reset settings singleton
        _sync_env()
        from sonarfix.config import get_settings
        import sonarfix.config as cfg_mod
        cfg_mod._settings = None

        import logging
        logging.warning(f"[_run_job] project_key={req.project_key!r} branch={req.branch!r} pr={req.pull_request!r} max={req.max_issues}")
        log(f"project_key={req.project_key!r}  branch={req.branch!r}  pr={req.pull_request!r}")
        log("Fetching issues from SonarQube...")
        from sonarfix.sonarqube.client import SonarQubeClient
        from sonarfix.sonarqube.filters import filter_issues
        sq = SonarQubeClient()

        issues = sq.get_issues(
            req.project_key,
            branch=req.branch,
            pull_request=req.pull_request,
            severities=req.severities,
            types=req.types,
            max_issues=req.max_issues,
        )
        issues = filter_issues(issues, severities=req.severities, types=req.types)
        log(f"Found {len(issues)} issues")
        job["total_issues"] = len(issues)

        if not issues:
            job["status"] = "completed"
            job["result"] = {"fixed": 0, "skipped": 0, "total": 0}
            return

        # Clone / open repo
        log("Preparing repository...")
        from sonarfix.git.manager import GitManager
        import re
        git = GitManager()

        # Resolve repo URL: extract host/org/repo from any URL format
        repo_url = req.repo_url or ""
        host, org, repo_name_extracted = None, None, None

        if req.local_repo:
            log(f"Using local repo: {req.local_repo}")
            repo_dir = git.open_local(Path(req.local_repo))
        elif repo_url == "local":
            raise ValueError("No repo URL or local path provided")
        else:
            # Parse any URL format to extract host/org/repo
            pr_match = re.match(r'https?://([^/]+)/([^/]+)/([^/]+?)(?:/(?:pull|issues|compare|tree))?(?:/.*)?$', repo_url)
            ssh_match = re.match(r'git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$', repo_url)
            if pr_match:
                host, org, repo_name_extracted = pr_match.group(1), pr_match.group(2), pr_match.group(3)
            elif ssh_match:
                host, org, repo_name_extracted = ssh_match.group(1), ssh_match.group(2), ssh_match.group(3)

            # Always clone fresh to WORKSPACE_DIR to avoid modifying local repos
            git_conn = _connections.get("git", {})
            git_token = git_conn.get("token", "")
            git_username = git_conn.get("username", "")

            if host and org and repo_name_extracted:
                if git_token:
                    clone_url = f"https://x-access-token:{git_token}@{host}/{org}/{repo_name_extracted}.git"
                    log(f"Cloning via HTTPS with token: https://{host}/{org}/{repo_name_extracted}.git")
                elif git_username and git_conn.get("password"):
                    from urllib.parse import quote
                    clone_url = f"https://{quote(git_username)}:{quote(git_conn['password'])}@{host}/{org}/{repo_name_extracted}.git"
                    log(f"Cloning via HTTPS with credentials: https://{host}/{org}/{repo_name_extracted}.git")
                else:
                    clone_url = f"https://{host}/{org}/{repo_name_extracted}.git"
                    log(f"Cloning via HTTPS (no auth): https://{host}/{org}/{repo_name_extracted}.git")
                    log("WARNING: No Git token configured in Settings. Clone may fail if repo is private.")
            else:
                clone_url = repo_url
                log(f"Cloning {clone_url}...")

            repo_dir = git.clone_or_open(clone_url, dir_name=repo_name_extracted)

        if req.branch:
            git.checkout_branch(req.branch)
        elif req.pull_request:
            git.checkout_pr(req.pull_request)

        fix_branch = git.create_fix_branch(req.branch)
        log(f"Created fix branch: {fix_branch}")
        job["fix_branch"] = fix_branch

        # Plan
        log("Planning fixes...")
        from sonarfix.fixer.planner import FixPlanner
        planner = FixPlanner(sq, repo_dir)
        plans = planner.plan(issues)
        log(f"Created {len(plans)} batch(es)")

        if req.dry_run:
            instructions_contents = []
            for plan in plans:
                path = repo_dir / f"fix-instructions-batch-{plan.batch_index}.txt"
                path.write_text(plan.instructions_text, encoding="utf-8")
                log(f"Wrote instructions: {path}")
                instructions_contents.append({
                    "batch": plan.batch_index,
                    "path": str(path),
                    "content": plan.instructions_text,
                })
            job["status"] = "completed"
            job["result"] = {
                "fixed": 0, "skipped": 0, "total": len(issues),
                "dry_run": True,
                "instructions": instructions_contents,
            }
            job["repo_dir"] = str(repo_dir)
            sq.close()
            return

        # Apply fixes using LLM-based fixer
        log("Applying fixes via LLM...")
        from sonarfix.fixer.llm_fixer import LLMFixer
        from sonarfix.sonarqube.filters import group_by_file, sort_by_severity, deduplicate
        cfg = get_settings()

        # Resolve LLM API key: check config, then LLM connection llm_api_key field
        llm_api_key = cfg.llm_api_key
        if not llm_api_key:
            llm_conn = _connections.get("llm", {})
            llm_api_key = llm_conn.get("llm_api_key", "")
        llm_model = cfg.llm_model or "gpt-4o"
        llm_base_url = cfg.llm_base_url or None
        llm_timeout = getattr(cfg, "llm_timeout", 180.0)

        fixer = LLMFixer(
            api_key=llm_api_key,
            model=llm_model,
            base_url=llm_base_url,
            timeout=llm_timeout,
        )

        # Prepare issues grouped by file + enriched rules
        deduped = deduplicate(sort_by_severity(issues))
        file_groups = group_by_file(deduped)
        rules = planner._rule_cache  # reuse rules already fetched during planning

        llm_results = fixer.fix_all(repo_dir, file_groups, rules, log_fn=log)

        total_fixed = sum(r.fixed_issues for r in llm_results)
        total_skipped = sum(r.skipped_issues for r in llm_results)
        total_errors = sum(1 for r in llm_results if r.error)
        log(f"LLM fixer done: {total_fixed} fixed, {total_skipped} skipped, {total_errors} errors")

        # Log individual file errors for debugging
        for r in llm_results:
            if r.error:
                log(f"  File error [{r.file_path}]: {r.error}")

        if total_fixed == 0:
            log("WARNING: No files were actually modified by the LLM fixer.")
            if total_errors > 0:
                log(f"All {total_errors} file(s) had errors — check LLM API key and model config.")
            job["status"] = "failed"
            job["error"] = (
                f"LLM fixer produced 0 fixes ({total_errors} errors, {total_skipped} skipped). "
                "Check that a valid LLM API key is configured in Settings → LLM tab."
            )
            job["result"] = {
                "fixed": 0, "skipped": total_skipped, "total": len(issues),
                "errors": total_errors,
                "files": [
                    {"file": r.file_path, "fixed": r.fixed_issues,
                     "skipped": r.skipped_issues, "error": r.error}
                    for r in llm_results
                ],
            }
            job["repo_dir"] = str(repo_dir)
            sq.close()
            return

        # Clean up instruction files so they don't get committed
        for p in repo_dir.glob("fix-instructions-batch-*.txt"):
            p.unlink(missing_ok=True)
        for p in repo_dir.glob("fix-instructions.txt"):
            p.unlink(missing_ok=True)
        for p in repo_dir.glob("fix-output.txt"):
            p.unlink(missing_ok=True)

        # Validate
        log("Validating fixes...")
        from sonarfix.validator.checker import syntax_check_files
        modified = git.modified_files()
        check_results = syntax_check_files(repo_dir, modified) if modified else []

        # Commit
        sha = git.commit_all(
            f"sonarfix: auto-fix {total_fixed} SonarQube issues"
        )
        if sha:
            log(f"Committed: {sha[:12]}")

        # Build result dict directly (no dependency on WindsurfResult)
        from sonarfix.validator.checker import get_diff_stat, get_diff_summary
        result_dict = {
            "project_key": req.project_key,
            "branch": req.branch,
            "pull_request": req.pull_request,
            "fix_branch": fix_branch,
            "commit_sha": sha,
            "total_issues": len(issues),
            "fixed_count": total_fixed,
            "skipped_count": total_skipped,
            "diff_summary": get_diff_summary(repo_dir) if sha else {},
            "diff_stat": get_diff_stat(repo_dir) if sha else "",
            "syntax_checks_passed": sum(1 for c in check_results if c.passed),
            "syntax_checks_failed": sum(1 for c in check_results if not c.passed),
            "files": [
                {
                    "file": r.file_path,
                    "fixed": r.fixed_issues,
                    "skipped": r.skipped_issues,
                    "error": r.error,
                }
                for r in llm_results
            ],
        }

        job["status"] = "completed"
        job["result"] = result_dict
        job["repo_dir"] = str(repo_dir)
        log("Done!")

        sq.close()

    except Exception as e:
        log(f"ERROR: {e}")
        job["status"] = "failed"
        job["error"] = str(e)[:500]


# ---------------------------------------------------------------------------
# Apply fixes from dry-run job (re-run without dry_run)
# ---------------------------------------------------------------------------


@app.post("/api/jobs/{job_id}/apply")
def apply_fixes(job_id: str):
    """Re-run a completed dry-run job with dry_run=False."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    orig = _jobs[job_id]
    if orig.get("status") != "completed":
        raise HTTPException(400, "Job must be completed first")
    if not orig.get("result", {}).get("dry_run"):
        raise HTTPException(400, "Job was not a dry run")

    # Create a new job based on original request but with dry_run=False
    new_id = str(uuid.uuid4())[:8]
    new_req = dict(orig["request"])
    new_req["dry_run"] = False
    _jobs[new_id] = {
        "id": new_id,
        "status": "queued",
        "request": new_req,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "log": [],
        "result": None,
        "parent_job": job_id,
    }
    t = threading.Thread(target=_run_job, args=(new_id,), daemon=True)
    t.start()
    return {"job_id": new_id, "parent_job": job_id}


@app.post("/api/jobs/{job_id}/push")
def push_and_create_pr(job_id: str):
    """Push the fix branch and create a PR on the Git host."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    job = _jobs[job_id]
    if job.get("status") != "completed":
        raise HTTPException(400, "Job must be completed first")

    repo_dir_str = job.get("repo_dir")
    fix_branch = job.get("fix_branch")
    if not repo_dir_str or not fix_branch:
        raise HTTPException(400, "No repo or fix branch found on this job")

    try:
        from sonarfix.git.manager import GitManager
        git = GitManager()
        git.open_local(Path(repo_dir_str))

        # Push
        git.push(branch=fix_branch)

        # Try to create PR via GitHub API
        git_conn = _connections.get("git", {})
        git_token = git_conn.get("token", "")
        pr_url = None

        if git_token:
            req_data = job.get("request", {})
            import re
            repo_url = req_data.get("repo_url", "")
            # Extract host/org/repo
            m = re.match(r'git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$', repo_url)
            if not m:
                m = re.match(r'https?://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$', repo_url)
            if m:
                api_host, api_org, api_repo = m.group(1), m.group(2), m.group(3)
                base_branch = req_data.get("branch") or "staging"
                import httpx
                pr_resp = httpx.post(
                    f"https://{api_host}/api/v3/repos/{api_org}/{api_repo}/pulls",
                    headers={"Authorization": f"token {git_token}", "Accept": "application/json"},
                    json={
                        "title": f"SonarFix: auto-fix SonarQube issues ({fix_branch})",
                        "head": fix_branch,
                        "base": base_branch,
                        "body": f"Automated fixes from SonarFix Agent.\n\nJob: `{job_id}`\nBranch: `{fix_branch}`",
                    },
                    verify=False,
                    timeout=30.0,
                )
                if pr_resp.status_code in (200, 201):
                    pr_data = pr_resp.json()
                    pr_url = pr_data.get("html_url", pr_data.get("url"))

        return {
            "pushed": True,
            "branch": fix_branch,
            "pr_url": pr_url,
            "message": f"Pushed {fix_branch}" + (f" and created PR: {pr_url}" if pr_url else ". Create PR manually on your Git host."),
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


# ---------------------------------------------------------------------------
# Issues preview endpoint (no fix, just list)
# ---------------------------------------------------------------------------


@app.get("/api/issues")
def preview_issues(
    project_key: str,
    branch: Optional[str] = None,
    pull_request: Optional[str] = None,
    max_issues: int = 100,
):
    """Preview SonarQube issues without fixing."""
    import logging
    logging.warning(f"[preview_issues] project_key={project_key!r} branch={branch!r} pull_request={pull_request!r} max_issues={max_issues}")
    try:
        import sonarfix.config as cfg_mod
        cfg_mod._settings = None
        from sonarfix.sonarqube.client import SonarQubeClient
        sq = SonarQubeClient()
        logging.warning(f"[preview_issues] SQ base_url={sq.base_url!r} auth_user={sq._auth[0][:8] if sq._auth[0] else 'EMPTY'}...")
        issues = sq.get_issues(
            project_key, branch=branch, pull_request=pull_request, max_issues=max_issues,
        )
        sq.close()
        return {
            "total": len(issues),
            "issues": [
                {
                    "key": i.key,
                    "rule": i.rule,
                    "severity": i.severity,
                    "type": i.type,
                    "message": i.message,
                    "file": i.file_path,
                    "line": i.start_line,
                }
                for i in issues
            ],
        }
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


# ---------------------------------------------------------------------------
# RAG endpoints
# ---------------------------------------------------------------------------


class StandardDocUpload(BaseModel):
    source: str
    title: str
    content: str
    language: str = ""


@app.get("/api/rag/stats")
def rag_stats():
    """Get RAG store statistics."""
    try:
        from sonarfix.rag.store import RAGStore
        store = RAGStore()
        stats = store.get_stats()
        store.close()
        return stats
    except Exception as e:
        return {"fix_examples": 0, "standard_docs": 0, "error": str(e)[:200]}


@app.post("/api/rag/standards")
def upload_standard(doc: StandardDocUpload):
    """Upload a coding standard document for RAG retrieval."""
    try:
        from sonarfix.rag.store import RAGStore
        store = RAGStore()
        doc_id = store.store_standard(
            source=doc.source,
            title=doc.title,
            content=doc.content,
            language=doc.language,
        )
        store.close()
        return {"id": doc_id, "status": "stored"}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


@app.get("/api/rag/fixes")
def list_fixes(rule_key: Optional[str] = None, limit: int = 20):
    """List stored fix examples, optionally filtered by rule."""
    try:
        from sonarfix.rag.store import RAGStore
        store = RAGStore()
        if rule_key:
            rows = store.conn.execute(
                "SELECT id, rule_key, language, severity, issue_message, created_at "
                "FROM fix_examples WHERE rule_key = ? ORDER BY created_at DESC LIMIT ?",
                (rule_key, limit),
            ).fetchall()
        else:
            rows = store.conn.execute(
                "SELECT id, rule_key, language, severity, issue_message, created_at "
                "FROM fix_examples ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        fixes = [{"id": r[0], "rule_key": r[1], "language": r[2],
                  "severity": r[3], "issue_message": r[4], "created_at": r[5]} for r in rows]
        store.close()
        return {"fixes": fixes, "total": len(fixes)}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health():
    return {"status": "ok"}
