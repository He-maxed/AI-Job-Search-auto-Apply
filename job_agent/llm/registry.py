from __future__ import annotations

from job_agent.config import llm_provider as configured_provider
from job_agent.llm.base import LLMProvider, UnavailableProvider

PROVIDERS: dict[str, LLMProvider] = {}


def register(provider: LLMProvider) -> LLMProvider:
    PROVIDERS[provider.name] = provider
    return provider


def available_llms() -> list[str]:
    return sorted(PROVIDERS)


def get_llm(name: str | None = None) -> LLMProvider:
    wanted = (name or configured_provider()).strip().lower()
    if wanted in ("", "none"):
        return UnavailableProvider()
    return PROVIDERS.get(wanted, UnavailableProvider())