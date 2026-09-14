from __future__ import annotations

import json
import urllib.request
from typing import Any

from job_agent import config
from job_agent.llm.base import LLMProvider, LLMUnavailableError


class OllamaProvider(LLMProvider):
    """First-class optional local LLM provider. Requires no Ollama install to import."""

    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ):
        self.base_url = (base_url or config.ollama_base_url()).rstrip("/")
        self.model = model or config.ollama_model()
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                message = parsed.get("error") or detail
            except json.JSONDecodeError:
                message = detail or str(exc)
            raise LLMUnavailableError(f"ollama request failed ({exc.code}): {message}") from exc
        except urllib.error.URLError as exc:
            raise LLMUnavailableError(f"ollama unreachable at {self.base_url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMUnavailableError(f"ollama timed out: {exc}") from exc

    def available(self) -> bool:
        try:
            self._request("GET", "/api/tags", timeout=2.0)
            return True
        except Exception:
            return False

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        if not self.available():
            raise LLMUnavailableError("LLM provider unavailable (ollama not reachable)")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        data = self._request("POST", "/api/chat", body)
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMUnavailableError(f"ollama returned an unexpected response: {exc}") from exc