from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class LLMUnavailableError(RuntimeError):
    pass


class LLMProvider(ABC):
    """Adapter interface for AI text generation. Business logic never names a provider."""

    name: ClassVar[str]

    def available(self) -> bool:
        return True

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        """Generate text from a single prompt. Raise LLMUnavailableError when unavailable."""


class UnavailableProvider(LLMProvider):
    """Default provider for deterministic-only mode."""

    name = "none"

    def available(self) -> bool:
        return False

    def complete(self, prompt: str, **_: object) -> str:
        raise LLMUnavailableError("LLM provider unavailable")