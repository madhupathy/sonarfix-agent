"""SonarQube API client — paginated issue fetch, rule lookup, branch/PR listing."""

from __future__ import annotations

from typing import Dict, List, Optional

import httpx

from sonarfix.config import get_settings
from sonarfix.sonarqube.models import (
    Branch,
    Issue,
    IssuesSearchResponse,
    Paging,
    PullRequest,
    Rule,
    RuleShowResponse,
)

MAX_PAGE_SIZE = 500
MAX_TOTAL_ISSUES = 10_000  # SonarQube hard cap


class SonarQubeClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
    ):
        cfg = get_settings()
        self.base_url = (base_url or cfg.sonarqube_url).rstrip("/")
        self._auth = (
            username or cfg.sonarqube_username,
            password or cfg.sonarqube_password,
        )
        self._verify = verify_ssl if verify_ssl is not None else cfg.sonarqube_verify_ssl
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.base_url,
                auth=self._auth,
                verify=self._verify,
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def get_issues(
        self,
        project_key: str,
        branch: Optional[str] = None,
        pull_request: Optional[str] = None,
        severities: Optional[list[str]] = None,
        statuses: Optional[list[str]] = None,
        types: Optional[list[str]] = None,
        max_issues: int = 500,
    ) -> list[Issue]:
        """Fetch open issues with pagination. Returns up to *max_issues* items."""
        params: dict[str, str] = {
            "componentKeys": project_key,
            "ps": str(min(max_issues, MAX_PAGE_SIZE)),
            "p": "1",
        }
        if branch:
            params["branch"] = branch
        if pull_request:
            params["pullRequest"] = pull_request
        if severities:
            params["severities"] = ",".join(severities)
        if statuses:
            params["statuses"] = ",".join(statuses)
        else:
            params["statuses"] = "OPEN,CONFIRMED,REOPENED"
        if types:
            params["types"] = ",".join(types)

        all_issues: list[Issue] = []
        page = 1

        while True:
            params["p"] = str(page)
            resp = self.client.get("/api/issues/search", params=params)
            resp.raise_for_status()
            data = IssuesSearchResponse.model_validate(resp.json())

            all_issues.extend(data.issues)

            if len(all_issues) >= max_issues:
                all_issues = all_issues[:max_issues]
                break

            paging: Paging = data.paging
            fetched = paging.page_index * paging.page_size
            if fetched >= paging.total or fetched >= MAX_TOTAL_ISSUES:
                break
            page += 1

        return all_issues

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def get_rule(self, rule_key: str) -> Rule:
        """Fetch a single rule's metadata and description."""
        resp = self.client.get("/api/rules/show", params={"key": rule_key})
        resp.raise_for_status()
        data = RuleShowResponse.model_validate(resp.json())
        return data.rule

    # ------------------------------------------------------------------
    # Branches & Pull Requests
    # ------------------------------------------------------------------

    def get_branches(self, project_key: str) -> list[Branch]:
        resp = self.client.get(
            "/api/project_branches/list", params={"project": project_key}
        )
        resp.raise_for_status()
        raw = resp.json().get("branches", [])
        return [Branch.model_validate(b) for b in raw]

    def get_pull_requests(self, project_key: str) -> list[PullRequest]:
        resp = self.client.get(
            "/api/project_pull_requests/list", params={"project": project_key}
        )
        resp.raise_for_status()
        raw = resp.json().get("pullRequests", [])
        return [PullRequest.model_validate(pr) for pr in raw]
