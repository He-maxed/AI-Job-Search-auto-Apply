from __future__ import annotations

import json
import urllib.request

import pytest

from job_agent import aggregate
from job_agent.aggregate import SourceReport, collect, dedupe, resolve
from job_agent.jobs import Job, JobQuery, JobSource, SourceError
from job_agent.llm.base import LLMProvider
from job_agent.memory import Memory
from job_agent.pipeline import run


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


VALID_ANALYSIS = """
{
  "analysis": {
    "required_skills": ["python", "terraform"],
    "preferred_skills": [],
    "experience_requirements": null,
    "education_requirements": null,
    "location": "Remote",
    "work_mode": "remote",
    "salary": {"min": 120000, "max": 150000, "currency": "USD", "notes": null},
    "work_authorization": null,
    "responsibilities": "Build backend services.",
    "qualifications": "Python and Terraform."
  }
}
"""


@pytest.fixture
def profile(make_profile, tmp_path):
    data = make_profile(
        preferences={
            "target_roles": ["Python Developer"],
            "excluded_roles": [],
            "excluded_companies": [],
            "excluded_keywords": [],
            "seniority": ["senior"],
            "salary": {"min": 100000, "target": 150000},
        }
    )
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_job(source_key, external_id, title="Python Developer", **overrides):
    base = {
        "title": title,
        "company": "Acme Inc",
        "description": "Build services with Python and Django in a remote team.",
        "skills": ["python", "django"],
        "location": "Remote",
        "remote": True,
        "salary_min": 140000,
        "salary_max": 180000,
        "experience_level": "senior",
        "url": f"https://jobs.example/{source_key}/{external_id}",
        "apply_url": None,
    }
    base.update(overrides)
    return Job(source=source_key, external_id=str(external_id), **base)


# --- source aggregation layer -------------------------------------------------


def test_collect_two_sources_into_one_pool():
    gh = FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1), make_job("greenhouse", 2)])
    lv = FakeSource(key="lever", jobs=[make_job("lever", 9)])
    jobs, reports = collect([gh, lv], JobQuery())
    assert len(jobs) == 3
    assert {j.to_dict()["id"] for j in jobs} == {"greenhouse:1", "greenhouse:2", "lever:9"}
    assert sorted((r.source, r.completed, r.returned) for r in reports) == [
        ("greenhouse", True, 2),
        ("lever", True, 1),
    ]
    assert all(not r.skipped and r.error is None for r in reports)


def test_collect_source_failure_isolated():
    gh = FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1)])
    lv = FakeSource(key="lever", error=SourceError("lever HTTP 500: boom"))
    jobs, reports = collect([gh, lv], JobQuery())
    assert len(jobs) == 1
    assert jobs[0].to_dict()["id"] == "greenhouse:1"
    by_source = {r.source: r for r in reports}
    assert by_source["greenhouse"].completed is True
    assert by_source["greenhouse"].returned == 1
    assert by_source["lever"].completed is False
    assert by_source["lever"].error == "lever HTTP 500: boom"


def test_collect_any_exception_isolated():
    gh = FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1)])
    bad = FakeSource(key="bad", error=ValueError("programmer error"))
    jobs, reports = collect([gh, bad], JobQuery())
    assert len(jobs) == 1
    assert reports[1].source == "bad"
    assert reports[1].completed is False
    assert "programmer error" in reports[1].error


def test_collect_empty_source_is_completed():
    empty = FakeSource(key="greenhouse", jobs=[])
    jobs, reports = collect([empty], JobQuery())
    assert jobs == []
    assert reports[0].completed is True
    assert reports[0].returned == 0
    assert reports[0].error is None


def test_collect_skips_source_missing_credential(monkeypatch):
    class Tokened(FakeSource):
        credential_hint = "FAKE_TOKEN"

    monkeypatch.delenv("FAKE_TOKEN", raising=False)
    tokened = Tokened(key="greenhouse", jobs=[make_job("greenhouse", 1)])
    jobs, reports = collect([tokened], JobQuery())
    assert jobs == []
    assert tokened.calls == 0
    assert reports[0].skipped is True
    assert reports[0].hint == "FAKE_TOKEN"
    assert reports[0].completed is False


def test_dedupe_namespaced_identity_keeps_same_external_id_separate():
    gh = make_job("greenhouse", 7)
    lv = make_job("lever", 7)
    kept, dropped = dedupe([gh, lv])
    assert dropped == 0
    assert [j.to_dict()["id"] for j in kept] == ["greenhouse:7", "lever:7"]


def test_dedupe_same_source_same_id_dropped():
    first = make_job("greenhouse", 7)
    second = make_job("greenhouse", 7)
    kept, dropped = dedupe([first, second])
    assert dropped == 1
    assert [j.to_dict()["id"] for j in kept] == ["greenhouse:7"]


def test_dedupe_identical_url_across_sources_dropped():
    gh = make_job("greenhouse", 1, url="https://careers.example/py-role")
    lv = make_job("lever", 2, url="https://careers.example/py-role")
    kept, dropped = dedupe([gh, lv])
    assert dropped == 1
    assert kept[0].to_dict()["id"] == "greenhouse:1"


def test_dedupe_identical_apply_url_across_sources_dropped():
    gh = make_job("greenhouse", 1, url="", apply_url="https://apply.example/123")
    lv = make_job("lever", 2, url="", apply_url="https://apply.example/123")
    kept, dropped = dedupe([gh, lv])
    assert dropped == 1
    assert [j.to_dict()["id"] for j in kept] == ["greenhouse:1"]


def test_dedupe_never_merges_by_title_alone():
    a = make_job("greenhouse", 1, title="Software Engineer", url="https://a.example/1")
    b = make_job("lever", 2, title="Software Engineer", url="https://b.example/2")
    kept, dropped = dedupe([a, b])
    assert dropped == 0
    assert len(kept) == 2


def test_resolve_explicit_names(monkeypatch):
    registry = {
        "greenhouse": lambda: FakeSource(key="greenhouse"),
        "lever": lambda: FakeSource(key="lever"),
    }
    monkeypatch.setattr(aggregate, "available_sources", lambda: sorted(registry))
    monkeypatch.setattr(aggregate, "get_source", lambda name, **kw: registry[name]())
    sources = resolve(["lever", "greenhouse"])
    assert [s.key for s in sources] == ["lever", "greenhouse"]


def test_resolve_all_uses_registry(monkeypatch):
    registry = {
        "greenhouse": lambda: FakeSource(key="greenhouse"),
        "lever": lambda: FakeSource(key="lever"),
    }
    monkeypatch.setattr(aggregate, "available_sources", lambda: sorted(registry))
    monkeypatch.setattr(aggregate, "get_source", lambda name, **kw: registry[name]())
    sources = resolve(use_all=True)
    assert [s.key for s in sources] == sorted(registry)


def test_resolve_default_uses_config(monkeypatch):
    registry = {
        "greenhouse": lambda: FakeSource(key="greenhouse"),
        "lever": lambda: FakeSource(key="lever"),
    }
    monkeypatch.setattr(aggregate, "available_sources", lambda: sorted(registry))
    monkeypatch.setattr(aggregate, "get_source", lambda name, **kw: registry[name]())
    monkeypatch.setattr(aggregate.config, "job_sources", lambda: ["lever"])
    sources = resolve()
    assert [s.key for s in sources] == ["lever"]


def test_resolve_unknown_name_raises(monkeypatch):
    monkeypatch.setattr(aggregate, "available_sources", lambda: ["greenhouse"])
    with pytest.raises(SourceError, match="Unknown job source"):
        resolve(["greenhouse", "mystery"])


# --- end-to-end pipeline with several sources --------------------------------


def test_run_two_sources_single_unified_pool(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    srcs = [
        FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1, "GH Role")]),
        FakeSource(key="lever", jobs=[make_job("lever", 2, "LV Role")]),
    ]
    assert run(sources=srcs, profile_path=profile, db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "Job sources: greenhouse, lever" in out
    assert "GH Role" in out
    assert "LV Role" in out
    assert "deduplicated" not in out.lower()
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"greenhouse:1", "lever:2"}
        rows = memory.conn.execute("SELECT id, source FROM jobs ORDER BY source").fetchall()
        assert {r["source"] for r in rows} == {"greenhouse", "lever"}
    finally:
        memory.close()


def test_run_source_failure_isolated(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    srcs = [
        FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1, "GH Role")]),
        FakeSource(key="lever", error=SourceError("lever HTTP 500: boom")),
    ]
    assert run(sources=srcs, profile_path=profile, db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "Source 'greenhouse' returned 1 jobs." in out
    assert "Search failed via source 'lever': lever HTTP 500: boom" in out
    assert "GH Role" in out
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"greenhouse:1"}
    finally:
        memory.close()


def test_run_all_sources_failed_returns_1(profile, tmp_path):
    srcs = [
        FakeSource(key="greenhouse", error=SourceError("down")),
        FakeSource(key="lever", error=SourceError("down too")),
    ]
    assert run(sources=srcs, profile_path=profile, db_path=tmp_path / "data.db") == 1


def test_run_all_sources_unconfigured_returns_2(profile, tmp_path, monkeypatch):
    class NeedsConfig(FakeSource):
        credential_hint = "FAKE_TOKEN"

    monkeypatch.delenv("FAKE_TOKEN", raising=False)
    srcs = [NeedsConfig(key="greenhouse"), NeedsConfig(key="lever")]
    assert run(sources=srcs, profile_path=profile, db_path=tmp_path / "data.db") == 2


def test_run_empty_source_completes(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    assert run(
        sources=[FakeSource(key="greenhouse", jobs=[])],
        profile_path=profile,
        db_path=db_path,
    ) == 0
    out = capsys.readouterr().out
    assert "Source 'greenhouse' returned 0 jobs." in out
    assert "No A/B remote/hybrid jobs this run." in out


def test_run_first_seen_preserved_across_repeat_and_sources(profile, tmp_path):
    db_path = tmp_path / "data.db"
    common = make_job("greenhouse", 7, url="https://careers.example/py-role")
    assert run(
        sources=[FakeSource(key="greenhouse", jobs=[common])],
        profile_path=profile,
        db_path=db_path,
    ) == 0
    first = Memory(db_path).conn.execute(
        "SELECT first_seen_at, last_seen_at FROM jobs WHERE id='greenhouse:7'"
    ).fetchone()
    # Second run: same posting surfaces again via a DIFFERENT source (same URL).
    lever_dup = make_job("lever", 99, url="https://careers.example/py-role")
    assert run(
        sources=[
            FakeSource(key="greenhouse", jobs=[common]),
            FakeSource(key="lever", jobs=[lever_dup]),
        ],
        profile_path=profile,
        db_path=db_path,
    ) == 0
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"greenhouse:7"}
        row = memory.conn.execute(
            "SELECT first_seen_at, last_seen_at FROM jobs WHERE id='greenhouse:7'"
        ).fetchone()
        assert row["first_seen_at"] == first["first_seen_at"]
        assert row["last_seen_at"] >= first["last_seen_at"]
    finally:
        memory.close()


def test_run_work_mode_gate_after_aggregation(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    srcs = [
        FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1)]),
        FakeSource(
            key="lever",
            jobs=[make_job("lever", 2, location="New Delhi, India", description="On-site role in the office", remote=False)],
        ),
    ]
    assert run(sources=srcs, profile_path=profile, db_path=db_path) == 0
    out = capsys.readouterr().out
    assert "remote: 1, hybrid: 0, unknown: 0, on_site: 1" in out
    assert "Excluded: on-site role (never recommended): 1" in out
    assert out.count("ranked by fit") == 1
    memory = Memory(db_path)
    try:
        stored = json.loads(
            memory.conn.execute("SELECT raw_json FROM jobs WHERE id='lever:2'").fetchone()[0]
        )
        assert stored["workMode"] == "on_site"
    finally:
        memory.close()


def test_run_deterministic_ranking_of_aggregated_pool(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    weak = make_job(
        "greenhouse", 1, "Weak Match", skills=["java"], salary_min=110000, salary_max=140000
    )
    strong = make_job(
        "lever", 2, "Strong Match", skills=["python", "django"], salary_min=200000, salary_max=240000
    )
    assert run(
        sources=[FakeSource(key="greenhouse", jobs=[weak]), FakeSource(key="lever", jobs=[strong])],
        profile_path=profile,
        db_path=db_path,
    ) == 0
    out = capsys.readouterr().out
    assert out.index("Strong Match") < out.index("Weak Match")


def test_run_optional_llm_enrichment_across_sources(profile, tmp_path):
    db_path = tmp_path / "data.db"
    bare = make_job("greenhouse", 1, description="", skills=[], salary_min=None, salary_max=None)
    srcs = [
        FakeSource(key="greenhouse", jobs=[bare]),
        FakeSource(key="lever", jobs=[make_job("lever", 2, description="", skills=[])]),
    ]
    assert run(sources=srcs, profile_path=profile, db_path=db_path, llm=FakeLLM(VALID_ANALYSIS)) == 0
    memory = Memory(db_path)
    try:
        for job_id in ("greenhouse:1", "lever:2",):
            stored = json.loads(memory.conn.execute("SELECT raw_json FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            assert "terraform" in stored["skills"]
    finally:
        memory.close()


def test_run_no_llm_available_deterministic_only(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    assert run(
        sources=[FakeSource(key="lever", jobs=[make_job("lever", 2)])],
        profile_path=profile,
        db_path=db_path,
        llm=None,
        llm_name="none",
    ) == 0
    out = capsys.readouterr().out
    assert "LLM analysis: not configured" in out
    memory = Memory(db_path)
    try:
        score = memory.conn.execute("SELECT fit_score FROM scores WHERE job_id='lever:2'").fetchone()
        assert score["fit_score"] > 0
    finally:
        memory.close()


def test_run_provider_independent_normalization(profile, tmp_path):
    db_path = tmp_path / "data.db"
    gh_job = make_job("greenhouse", 1)
    lv_job = make_job("lever", 2)
    srcs = [FakeSource(key="greenhouse", jobs=[gh_job]), FakeSource(key="lever", jobs=[lv_job])]
    assert run(sources=srcs, profile_path=profile, db_path=db_path) == 0
    for job in (gh_job, lv_job):
        d = job.to_dict()
        assert d["id"].startswith(("greenhouse:", "lever:"))
    memory = Memory(db_path)
    try:
        rows = memory.conn.execute("SELECT id, source FROM jobs ORDER BY source").fetchall()
        assert {r["id"] for r in rows} == {"greenhouse:1", "lever:2"}
        assert {r["source"] for r in rows} == {"greenhouse", "lever"}
    finally:
        memory.close()


def test_no_network_calls_in_tests(profile, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network call made during tests")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    db_path = tmp_path / "data.db"
    srcs = [
        FakeSource(key="greenhouse", jobs=[make_job("greenhouse", 1)]),
        FakeSource(key="lever", error=SourceError("x")),
    ]
    assert run(sources=srcs, profile_path=profile, db_path=db_path) == 0


def test_malformed_job_isolated_pool_survives(profile, tmp_path, capsys):
    db_path = tmp_path / "data.db"
    broken = Job(
        source="greenhouse",
        external_id="1",
        title="Broken Job",
        company={"name": "Dict Inc"},  # violates the scalar Job schema
        url="https://careers.example/broken",
    )
    healthy = make_job("lever", 2, "Healthy Role")
    assert run(
        sources=[FakeSource(key="greenhouse", jobs=[broken]), FakeSource(key="lever", jobs=[healthy])],
        profile_path=profile,
        db_path=db_path,
    ) == 0
    out = capsys.readouterr().out
    assert "skipped malformed job from 'greenhouse'" in out
    assert "Malformed jobs skipped: 1" in out
    assert "Healthy Role" in out
    memory = Memory(db_path)
    try:
        assert memory.known_job_ids() == {"lever:2"}
    finally:
        memory.close()