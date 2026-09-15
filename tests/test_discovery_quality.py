from __future__ import annotations

import io
import json
import urllib.request

import pytest

from job_agent.aggregate import collect, dedupe
from job_agent.jobs import Job, JobQuery, JobSource, SourceError
from job_agent.jobs.query import MAX_ROLE_TERMS, TARGET_ROLE_FAMILIES, expand_role_terms, make_query
from job_agent.jobs.selection import prioritize
from job_agent.jobs.sources.adzuna import AdzunaJobSource
from job_agent.llm.base import LLMProvider
from job_agent.memory import Memory
from job_agent.pipeline import run
from job_agent.relevance import (
    GEO_ENRICH_CATEGORIES,
    IRRELEVANT_CATEGORY,
    LOCATION_FOREIGN,
    LOCATION_INDIA_COMPATIBLE,
    LOCATION_REMOTE_GLOBAL,
    LOCATION_UNKNOWN,
    POSSIBLE_CATEGORY,
    STRONG_CATEGORY,
    classify_location,
    classify_relevance,
)


class FakeSource(JobSource):
    credential_hint = None

    def __init__(self, key="fake", jobs=None, error=None):
        self.key = key
        self._jobs = jobs or []
        self._error = error
        self.calls = 0

    def search(self, query: JobQuery) -> list[Job]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._jobs

    def normalize(self, raw):
        raise NotImplementedError


class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, reply="not json"):
        self.reply = reply

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, **kwargs) -> str:
        return self.reply


class CountingLLM(FakeLLM):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def complete(self, prompt, **kwargs):
        self.calls += 1
        return super().complete(prompt, **kwargs)


FULL_TARGET_ROLES = [
    "AI Engineer",
    "Machine Learning Engineer",
    "ML Engineer",
    "NLP Engineer",
    "Computer Vision Engineer",
    "Python Developer",
]

SIX_ROLE_PROFILE = {
    "personal": {},
    "preferences": {
        "target_roles": FULL_TARGET_ROLES,
        "salary": {"min": 100000},
    },
    "locations": {"current": "Bengaluru", "preferred": ["Bengaluru"], "remote_ok": True},
    "skills": {"languages": ["python", "typescript"], "frameworks": [], "tools": []},
}


@pytest.fixture
def profile(make_profile, tmp_path):
    data = make_profile()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_job(source_key, external_id, title="AI Engineer", location="Remote", **overrides):
    base = {
        "title": title,
        "company": "Acme Inc",
        "description": "Build ML services with Python.",
        "skills": ["python"],
        "location": location,
        "remote": True,
        "salary_min": 140000,
        "salary_max": 180000,
        "experience_level": "senior",
        "url": f"https://jobs.example/{source_key}/{external_id}",
        "apply_url": None,
    }
    base.update(overrides)
    return Job(source=source_key, external_id=str(external_id), **base)


# --- 1-8: geographic classification ------------------------------------------


@pytest.mark.parametrize(
    "location,description,expected",
    [
        ("Remote — India", "Remote", LOCATION_INDIA_COMPATIBLE),
        ("Remote — Anywhere in India", "Remote", LOCATION_INDIA_COMPATIBLE),
        ("Hybrid — Bengaluru, India", "Hybrid", LOCATION_INDIA_COMPATIBLE),
        ("Remote — India / UK", "Remote", LOCATION_INDIA_COMPATIBLE),
        ("Remote (Anywhere)", None, LOCATION_REMOTE_GLOBAL),
        ("Remote — Worldwide", None, LOCATION_REMOTE_GLOBAL),
        ("Remote — Global", None, LOCATION_REMOTE_GLOBAL),
        ("Remote — US only", None, LOCATION_FOREIGN),
        ("Remote — United States", None, LOCATION_FOREIGN),
        ("Remote — UK / EU", None, LOCATION_FOREIGN),
        ("Remote — Europe", None, LOCATION_FOREIGN),
        ("Remote", None, LOCATION_UNKNOWN),
        (None, None, LOCATION_UNKNOWN),
        ("Hyderabad, India", "On-site role.", LOCATION_INDIA_COMPATIBLE),
        ("", "On-site role.", LOCATION_UNKNOWN),
    ],
)
def test_geo_categories(location, description, expected):
    job = make_job("gh", 1).to_dict()
    job["location"] = location
    job["description"] = description
    assert classify_location(job) == expected


def test_bare_remote_never_implies_global_pool():
    job = make_job("gh", 1).to_dict()
    assert job["location"] == "Remote"
    assert classify_location(job) == LOCATION_UNKNOWN
    assert LOCATION_UNKNOWN in GEO_ENRICH_CATEGORIES


def test_remote_global_requires_explicit_pool_wording():
    job = make_job("gh", 1).to_dict()
    job["location"] = "Fully remote"
    assert classify_location(job) == LOCATION_UNKNOWN


# --- 9-13: query generation per role family ----------------------------------


@pytest.mark.parametrize(
    "role,other_not_expected",
    [
        ("AI Engineer", "Computer Vision"),
        ("Machine Learning Engineer", "Speech"),
        ("NLP Engineer", "AI Platform Engineer"),
        ("Computer Vision Engineer", "NLP Engineer"),
        ("Python Developer", "AI Research Engineer"),
    ],
)
def test_role_family_terms_expand_only_active_families(role, other_not_expected):
    profile = {
        "preferences": {"target_roles": [role]},
        "locations": {"current": "Bengaluru", "preferred": ["Bengaluru"], "remote_ok": True},
        "skills": {"languages": [], "frameworks": [], "tools": []},
    }
    query = make_query(profile)
    assert role in query.role_terms
    assert query.roles == [role]
    for term in query.role_terms:
        assert other_not_expected not in term


def test_query_generation_ai_family():
    query = make_query(SIX_ROLE_PROFILE)
    ai = TARGET_ROLE_FAMILIES["ai"]
    assert query.role_terms[: len(ai)] == ai


def test_query_generation_python_family():
    profile = {
        "preferences": {"target_roles": ["Python Developer"]},
        "locations": {"current": "Bengaluru", "preferred": ["Bengaluru"], "remote_ok": True},
        "skills": {"languages": [], "frameworks": [], "tools": []},
    }
    query = make_query(profile)
    assert query.role_terms == TARGET_ROLE_FAMILIES["python"]


def test_query_roles_capped_at_six():
    profile = dict(SIX_ROLE_PROFILE)
    profile["preferences"]["target_roles"] = FULL_TARGET_ROLES + ["Extra Engineer", "Other Engineer"]
    query = make_query(profile)
    assert len(query.roles) == 6


# --- 14: query dedup / bounded ----------------------------------------------


def test_query_expansion_is_deduplicated_and_bounded():
    terms = expand_role_terms(FULL_TARGET_ROLES)
    assert len(terms) == len(set(terms))
    assert len(terms) <= MAX_ROLE_TERMS
    still = expand_role_terms(FULL_TARGET_ROLES)
    assert terms == still  # deterministic


# --- 15: cross-source duplicate preservation/merging -------------------------


def test_cross_source_duplicates_keep_namespaced_identity():
    gh = make_job("greenhouse", 7)
    lv = make_job("lever", 7)
    kept, dropped = dedupe([gh, lv])
    assert dropped == 0
    assert [j.to_dict()["id"] for j in kept] == ["greenhouse:7", "lever:7"]


def test_same_source_same_id_merged_not_duplicated():
    first = make_job("greenhouse", 7)
    second = make_job("greenhouse", 7, title="Python Developer")
    kept, dropped = dedupe([first, second])
    assert dropped == 1
    assert [j.to_dict()["id"] for j in kept] == ["greenhouse:7"]


def test_never_merged_by_title_alone():
    a = make_job("greenhouse", 1, url="https://jobs.example/a")
    b = make_job("lever", 2, url="https://jobs.example/b")
    kept, dropped = dedupe([a, b])
    assert dropped == 0
    assert len(kept) == 2


# --- 16: source failure isolation --------------------------------------------


def test_source_failure_isolated_in_collect():
    gh = FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1)])
    lv = FakeSource(key="lever", error=SourceError("lever HTTP 500: boom"))
    jobs, reports = collect([gh, lv], JobQuery())
    assert [j.to_dict()["id"] for j in jobs] == ["greenhouse:1"]
    by_source = {r.source: r for r in reports}
    assert by_source["greenhouse"].completed is True
    assert by_source["lever"].completed is False
    assert "boom" in by_source["lever"].error


# --- 17: feed source ordering (geo + role, feed-order fallback) --------------

FEED_JOB_ARGS = [
    ("gh", 1, "Grafana Backend Engineer", "Berlin, Germany"),
    ("gh", 2, "AI Engineer", "Remote — Worldwide"),
    ("gh", 3, "AI Engineer", "Remote — India"),
    ("gh", 4, "AI Engineer", "Remote"),
    ("gh", 5, "AI Engineer", "Remote (US Only)"),
]


def test_feed_ordering_geo_before_role_before_feed():
    jobs = [make_job(*args) for args in FEED_JOB_ARGS]
    query = JobQuery(roles=["AI Engineer"], role_terms=["AI Engineer"])
    ordered = prioritize(jobs, query)
    assert [j.external_id for j in ordered] == ["3", "2", "4", "5", "1"]
    assert ordered[0].to_dict()["location"] == "Remote — India"


def test_feed_ordering_role_match_breaks_tie_within_geo():
    jobs = [
        make_job("gh", 1, "Account Executive", "Remote — India"),   # geo first but wrong role
        make_job("gh", 2, "Backend Engineer", "Remote — Worldwide"),  # global but role-less
        make_job("gh", 3, "AI Engineer", "Remote — Worldwide"),       # global + target role
    ]
    ordered = prioritize(jobs, JobQuery(roles=["AI Engineer"], role_terms=["AI Engineer"]))
    assert [j.external_id for j in ordered] == ["1", "3", "2"]


def test_ordering_is_deterministic():
    jobs = [make_job(*args) for args in FEED_JOB_ARGS]
    query = JobQuery(roles=["AI Engineer"], role_terms=["AI Engineer"])
    first = prioritize(jobs, query)
    second = prioritize(jobs, query)
    assert [j.external_id for j in first] == [j.external_id for j in second]


def test_ordering_identity_when_no_role_vocabulary():
    jobs = [make_job(*args) for args in FEED_JOB_ARGS]
    ordered = prioritize(jobs, JobQuery())
    assert [j.external_id for j in ordered] == [str(n) for n in range(1, 6)]


# --- 18: keyword source (Adzuna) uses expanded role terms --------------------


def test_adzuna_uses_expanded_role_terms(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return io.BytesIO(b'{"results": [], "count": 0}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AdzunaJobSource(app_id="abc", app_key="secret", country="in")
    query = make_query(SIX_ROLE_PROFILE)
    assert source.search(query) == []
    assert "what" in captured["url"]
    assert "AI+Engineer" in captured["url"]
    assert "results_per_page=50" in captured["url"]


def test_adzuna_falls_back_to_exact_roles_when_no_expansion(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return io.BytesIO(b'{"results": [], "count": 0}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    source = AdzunaJobSource(app_id="abc", app_key="secret", country="in")
    source.search(JobQuery(roles=["AI Engineer"]))
    assert "what=AI+Engineer" in captured["url"]


# --- 19-24: relevance of common real-world titles ----------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Machine Learning Operations Engineer", STRONG_CATEGORY),
        ("AI Platform Engineer", STRONG_CATEGORY),
        ("AI Research Engineer", STRONG_CATEGORY),
        ("AI Product Manager", IRRELEVANT_CATEGORY),
        ("Sales Engineer", IRRELEVANT_CATEGORY),
        ("Software Engineer", STRONG_CATEGORY),
    ],
)
def test_relevance_of_common_titles(title, expected):
    profile = {"preferences": {"target_roles": FULL_TARGET_ROLES}}
    job = make_job("gh", 99, title=title).to_dict()
    assert classify_relevance(job, profile).category == expected


def test_operations_is_not_inherently_negative():
    profile = {"preferences": {"target_roles": FULL_TARGET_ROLES}}
    job = make_job("gh", 99, title="Operations Engineer").to_dict()
    assert classify_relevance(job, profile).category in {STRONG_CATEGORY, POSSIBLE_CATEGORY}


# --- LLM cost gate: foreign candidates skip enrichment, still scored ---------


def test_foreign_candidate_skips_llm_but_is_scored(profile, tmp_path):
    db_path = tmp_path / "data.db"
    llm = CountingLLM()
    jobs = [make_job("greenhouse", 1, title="AI Research Engineer", location="Remote (US only)")]
    assert run(source=FakeSource(jobs=jobs), profile_path=profile, db_path=db_path, llm=llm) == 0
    assert llm.calls == 0
    memory = Memory(db_path)
    try:
        stored = json.loads(memory.conn.execute("SELECT raw_json FROM jobs").fetchone()[0])
        has_score = memory.conn.execute("SELECT 1 FROM scores").fetchone() is not None
    finally:
        memory.close()
    assert stored["relevance"] == STRONG_CATEGORY
    assert stored["locationCategory"] == LOCATION_FOREIGN
    assert has_score is True


def test_india_or_global_and_unknown_candidate_enriched(profile, tmp_path):
    db_path = tmp_path / "data.db"
    llm = CountingLLM()
    jobs = [
        make_job("greenhouse", 1, title="AI Research Engineer", location="Remote — India"),
        make_job("greenhouse", 2, title="AI Research Engineer", location="Remote (Anywhere)"),
        make_job("greenhouse", 3, title="AI Research Engineer", location="Remote"),
    ]
    assert run(source=FakeSource(jobs=jobs), profile_path=profile, db_path=db_path, llm=llm) == 0
    assert llm.calls == 3