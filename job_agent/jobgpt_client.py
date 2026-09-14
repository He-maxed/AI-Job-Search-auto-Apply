from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class JobGPTError(RuntimeError):
    pass


class JobGPTClient:
    """Thin wrapper around the same REST API the JobGPT MCP server uses."""

    def __init__(self, api_key: str, api_url: str = "https://6figr.com", timeout: int = 60):
        if not api_key:
            raise JobGPTError(
                "JOBGPT_API_KEY is missing. Create a key at https://6figr.com/account "
                "(MCP Integrations) and put it in .env — never commit it."
            )
        self.base = f"{api_url.rstrip('/')}/api/v1/mcp"
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "job-agent/0.1",
        }

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base}{path}"
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                url = f"{url}?{qs}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                message = parsed.get("message") or detail
            except json.JSONDecodeError:
                message = detail or str(exc)
            raise JobGPTError(f"API Error ({exc.code}): {message}") from exc
        except urllib.error.URLError as exc:
            raise JobGPTError(f"Network error talking to JobGPT: {exc.reason}") from exc

        if not payload.get("success", True):
            raise JobGPTError(payload.get("message") or f"Request failed: {method} {path}")
        return payload.get("data", payload)

    def get_credits(self) -> dict[str, Any]:
        return self._request("GET", "/credits")

    def search_jobs(
        self,
        filters: dict[str, Any],
        limit: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/jobs/search",
            {"filters": filters, "limit": min(limit, 50), "page": page},
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

    def calculate_match_score(self, application_id: str) -> dict[str, Any]:
        """Not exposed as an MCP tool, but present on the API client in jobgpt-mcp-server."""
        return self._request("POST", f"/job-applications/{application_id}/match-score")
