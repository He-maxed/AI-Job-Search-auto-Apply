"""LLM layer: provider-agnostic AI adapters. Default is none (deterministic-only)."""

from job_agent.llm.base import LLMProvider, LLMUnavailableError, UnavailableProvider
from job_agent.llm.ollama import OllamaProvider
from job_agent.llm.registry import available_llms, get_llm, register

register(OllamaProvider())

__all__ = [
    "LLMProvider",
    "LLMUnavailableError",
    "UnavailableProvider",
    "available_llms",
    "get_llm",
]