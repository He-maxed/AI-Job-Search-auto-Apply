from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from job_agent.analysis.model import JobAnalysis, SalaryRange
from job_agent.analysis.parse import MalformedAnalysisError, parse_analysis
from job_agent.analysis.service import analyze_job, enrich_job_with_analysis
from job_agent.llm.base import LLMProvider, LLMUnavailableError


class FakeLLM(LLMProvider):
    """Fake provider so tests never require Ollama or any real model."""

    name = "fake"

    def __init__(self, output: str = "", available: bool = True):
        self.output = output
        self.available_flag = available
        self.last_prompt: str | None = None
        self.last_kwargs: dict | None = None

    def available(self) -> bool:
        return self.available_flag

    def complete(self, prompt: str, **kwargs) -> str:
        self.last_prompt = prompt
        self.last_kwargs = kwargs
        return self.output


VALID_JSON = json.dumps(
    {
        "required_skills": ["Python", "SQL"],
        "preferred_skills": ["Kubernetes"],
        "experience_requirements": "5+ years",
        "education_requirements": "Bachelor's in CS",
        "location": "Remote",
        "work_mode": "remote",
        "salary": {"min": 120000, "max": 160000, "currency": "USD"},
        "work_authorization": "US citizenship required",
        "responsibilities": "Build and operate services.",
        "qualifications": "Strong Python and SQL.",
    }
)


def sample_job():
    return {
        "id": "j1",
        "title": "Backend Engineer",
        "company": "Acme Inc",
        "location": "Remote",
        "description": "Python and SQL required. 5+ years.",
    }


def test_parse_valid_json():
    a = parse_analysis(VALID_JSON)
    assert a.required_skills == ["Python", "SQL"]
    assert a.preferred_skills == ["Kubernetes"]
    assert a.experience_requirements == "5+ years"
    assert a.education_requirements == "Bachelor's in CS"
    assert a.location == "Remote"
    assert a.work_mode == "remote"
    assert a.salary.min == 120000
    assert a.salary.max == 160000
    assert a.salary.currency == "USD"
    assert a.work_authorization == "US citizenship required"
    assert a.responsibilities
    assert a.qualifications


def test_parse_missing_fields_stay_empty():
    a = parse_analysis(
        '{"required_skills": [], "preferred_skills": null, "work_mode": null, "salary": null}'
    )
    assert a.required_skills == []
    assert a.preferred_skills == []
    assert a.experience_requirements is None
    assert a.education_requirements is None
    assert a.location is None
    assert a.work_mode is None
    assert a.work_authorization is None
    assert a.responsibilities is None
    assert a.qualifications is None
    assert a.salary.min is None and a.salary.max is None and a.salary.currency is None


def test_parse_fenced_json():
    a = parse_analysis('```json\n' + json.dumps({"required_skills": ["Go"]}) + '\n```')
    assert a.required_skills == ["Go"]


def test_parse_prose_wrapped_json():
    raw = 'Here is the result:\n{"required_skills": ["Go"]}\nHope that helps.'
    a = parse_analysis(raw)
    assert a.required_skills == ["Go"]


def test_parse_single_json_object_amid_noise():
    a = parse_analysis('Sure, {"required_skills": ["Python"], "preferred_skills": null, "work_mode": null} thanks')
    assert a.required_skills == ["Python"]


def test_parse_braces_inside_strings_ignored():
    raw = 'FYI: {"required_skills": ["Go"], "note": "braces { inside } strings"} all good'
    a = parse_analysis(raw)
    assert a.required_skills == ["Go"]


def test_parse_multiple_objects_rejected():
    with pytest.raises(MalformedAnalysisError):
        parse_analysis('{"required_skills": ["Go"]}{"required_skills": ["Rust"]}')


@pytest.mark.parametrize(
    "output",
    [
        "this is not json",
        "[1, 2, 3]",
        '"just a string"',
        "",
    ],
)
def test_parse_malformed_structures(output):
    with pytest.raises(MalformedAnalysisError):
        parse_analysis(output)


@pytest.mark.parametrize(
    "output",
    [
        '{"required_skills": "Python"}',
        '{"preferred_skills": 5}',
        '{"experience_requirements": ["5+"]}',
        '{"salary": 120000}',
        '{"work_mode": ["remote"]}',
    ],
)
def test_parse_malformed_types(output):
    with pytest.raises(MalformedAnalysisError):
        parse_analysis(output)


def test_analyze_success_with_fake_provider():
    fake = FakeLLM(VALID_JSON)
    analysis = analyze_job(sample_job(), llm=fake)
    assert isinstance(analysis, JobAnalysis)
    assert analysis.required_skills == ["Python", "SQL"]
    assert fake.last_prompt and "Backend Engineer" in fake.last_prompt
    assert fake.last_kwargs["temperature"] == 0.0


def test_analyze_requires_available_provider():
    fake = FakeLLM(VALID_JSON, available=False)
    with pytest.raises(LLMUnavailableError):
        analyze_job(sample_job(), llm=fake)


def test_analyze_propagates_malformed_output():
    fake = FakeLLM("not json at all")
    with pytest.raises(MalformedAnalysisError):
        analyze_job(sample_job(), llm=fake)


def test_enrich_job_with_analysis_merges_explicit_fields_only():
    job = {"id": "j1", "title": "T", "company": "C", "description": "d", "skills": ["Python"]}
    analysis = JobAnalysis(
        required_skills=["Python", "Django"],
        salary=SalaryRange(min=100000, max=180000),
        location="Remote",
        work_mode="remote",
    )
    enriched = enrich_job_with_analysis(job, analysis)
    assert enriched["skills"] == ["Python", "Django"]
    assert enriched["salaryMin"] == 100000
    assert enriched["salaryMax"] == 180000
    assert enriched["location"] == "Remote"
    assert enriched["remote"] is True
    assert enriched["title"] == "T"
    assert not enriched.get("experienceLevel")


def test_enrich_does_not_override_existing_values():
    job = {"id": "j1", "skills": ["Go"], "salaryMin": 200000, "location": "Bangalore", "remote": False}
    analysis = JobAnalysis(required_skills=["Python"], salary=SalaryRange(min=100000), location="Remote")
    enriched = enrich_job_with_analysis(job, analysis)
    assert enriched["skills"] == ["Go", "Python"]
    assert enriched["salaryMin"] == 200000
    assert enriched["location"] == "Bangalore"


def test_deterministic_score_consumes_analysis():
    from job_agent.profile import all_skills
    from job_agent.score import score_job

    job = {"id": "j1", "title": "Senior Python Developer", "company": "Acme", "description": "Python + Django"}
    analysis = JobAnalysis(required_skills=["Python", "Django"], salary=SalaryRange(min=120000, max=200000))
    enriched = enrich_job_with_analysis(job, analysis)
    profile = {
        "locations": {"remote_ok": True},
        "skills": {"languages": ["python"], "frameworks": ["django"]},
        "preferences": {"target_roles": ["Python Developer"], "seniority": ["senior"], "salary": {"min": 100000}},
    }
    decision = score_job(enriched, profile)
    assert 0 <= decision["fit_score"] <= 100
    assert decision["tier"] in {"A", "B", "C", "D"}
    assert "python" in decision["strong_matches"]


def test_analysis_business_logic_does_not_reference_ollama():
    import job_agent.analysis as analysis_pkg

    package_dir = Path(analysis_pkg.__file__).parent
    core_modules = [
        package_dir / "__init__.py",
        package_dir / "model.py",
        package_dir / "prompt.py",
        package_dir / "parse.py",
        package_dir / "service.py",
    ]
    for module in core_modules:
        if "ollama" in module.read_text(encoding="utf-8").lower():
            pytest.fail(f"{module} references ollama; business logic must stay provider-independent")


def test_ollama_http_error_maps_to_unavailable(monkeypatch):
    import io

    import job_agent.llm.ollama as ollama_module
    from job_agent.llm.ollama import OllamaProvider

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/api/tags"):
            raise urllib.error.HTTPError(
                req.full_url, 404, "model not found", {}, io.BytesIO(b'{"error":"model \\"x\\" not found"}')
            )
        raise AssertionError("should not reach the chat endpoint")

    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", fake_urlopen)
    p = OllamaProvider(base_url="http://127.0.0.1:11434")
    assert p.available() is False


def test_ollama_model_missing_maps_to_unavailable(monkeypatch):
    import io

    import job_agent.llm.ollama as ollama_module
    from job_agent.llm.ollama import OllamaProvider

    class FakeResp:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/api/tags"):
            return FakeResp(b'{"models": []}')
        raise urllib.error.HTTPError(
            req.full_url, 404, "not found", {}, io.BytesIO(b'{"error":"model \\"x\\" not found"}')
        )

    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", fake_urlopen)
    p = OllamaProvider(base_url="http://127.0.0.1:11434")
    with pytest.raises(LLMUnavailableError, match="model"):
        p.complete("hello")


def test_ollama_unreachable_maps_to_unavailable(monkeypatch):
    import job_agent.llm.ollama as ollama_module
    from job_agent.llm.ollama import OllamaProvider

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", fake_urlopen)
    p = OllamaProvider(base_url="http://127.0.0.1:1")
    assert p.available() is False
    with pytest.raises(LLMUnavailableError, match="LLM provider unavailable"):
        p.complete("hello")