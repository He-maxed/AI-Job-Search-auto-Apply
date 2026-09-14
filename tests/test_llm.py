from __future__ import annotations

import pytest

from job_agent.llm import LLMUnavailableError, UnavailableProvider, get_llm
from job_agent.llm.ollama import OllamaProvider


def test_unavailable_provider_reports_not_available():
    p = UnavailableProvider()
    assert p.available() is False
    with pytest.raises(LLMUnavailableError, match="LLM provider unavailable"):
        p.complete("summarize this")


def test_get_llm_default_is_none(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_llm(), UnavailableProvider)


def test_get_llm_unknown_falls_back_to_unavailable():
    assert isinstance(get_llm("does-not-exist"), UnavailableProvider)


def test_ollama_is_registered():
    provider = get_llm("ollama")
    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"


def test_ollama_offline_is_unavailable():
    p = OllamaProvider(base_url="http://127.0.0.1:1")
    assert p.available() is False
    with pytest.raises(LLMUnavailableError, match="LLM provider unavailable"):
        p.complete("anything")