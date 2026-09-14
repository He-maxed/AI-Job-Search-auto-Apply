from __future__ import annotations

from job_agent.profile import all_skills
from job_agent.score import format_decision, score_job


def test_tier_a_high_fit(make_job, make_profile):
    result = score_job(make_job(), make_profile())
    assert result["fit_score"] >= 85
    assert result["tier"] == "A"
    assert result["verdict"] == "APPLY IMMEDIATELY"
    assert not result["blockers"]


def test_tier_a_result_surface_keys(make_job, make_profile):
    result = score_job(make_job(), make_profile())
    assert result["job_id"] == "job-1"
    assert result["url"] == "https://acme.example/jobs/1"
    assert isinstance(result["priority"], float)
    assert result["strong_matches"]
    assert result["effort"] <= 1.4


def test_tier_b_partial_skill_fit(make_job, make_profile):
    result = score_job(make_job(skills=["python", "golang"]), make_profile())
    assert result["fit_score"] >= 70
    assert result["tier"] == "B"
    assert result["verdict"] == "APPLY"
    assert "golang" in result["missing"]


def test_tier_c_weak_fit(make_job, make_profile):
    profile = make_profile(experience=[])
    job = make_job(title="Backend Engineer", skills=["python", "golang"], experienceLevel="mid")
    result = score_job(job, profile)
    assert 55 <= result["fit_score"] < 70
    assert result["tier"] == "C"
    assert result["verdict"] == "CONSIDER"


def test_tier_d_low_fit_without_blockers(make_job, make_profile):
    profile = make_profile(experience=[])
    job = make_job(
        title="Logistics Coordinator",
        company="Global Freight",
        skills=[],
        description="Coordinate shipments between depots.",
        experienceLevel="",
        location="Pune",
        remote=False,
    )
    result = score_job(job, profile)
    assert result["fit_score"] < 55
    assert result["tier"] == "D"
    assert result["verdict"] == "SKIP"
    assert not result["blockers"]


def test_excluded_company_is_blocker(make_job, make_profile):
    profile = make_profile()
    profile["preferences"]["excluded_companies"] = ["acme"]
    result = score_job(make_job(), profile)
    assert result["tier"] == "D"
    assert result["verdict"] == "SKIP"
    assert any("excluded" in b.lower() for b in result["blockers"])


def test_salary_below_minimum_is_blocker(make_job, make_profile):
    job = make_job(salaryMin=90000, salaryMax=90000)
    result = score_job(job, make_profile())
    assert result["tier"] == "D"
    assert any("compensation" in b.lower() for b in result["blockers"])


def test_authorization_requirement_is_blocker(make_job, make_profile):
    profile = make_profile()
    profile["work_authorization"]["requires_sponsorship"] = True
    job = make_job(description="US citizenship required. Remote team.")
    result = score_job(job, profile)
    assert result["tier"] == "D"
    assert any("authorization" in b.lower() or "citizenship" in b.lower() for b in result["blockers"])


def test_excluded_keyword_is_blocker(make_job, make_profile):
    profile = make_profile()
    profile["preferences"]["excluded_keywords"] = ["contract"]
    job = make_job(description="This is a contract role on a product team.")
    result = score_job(job, profile)
    assert result["tier"] == "D"


def test_missing_job_skills_surface_in_missing(make_job, make_profile):
    result = score_job(make_job(skills=["python", "django", "kubernetes"]), make_profile())
    assert "kubernetes" in result["missing"]


def test_empty_profile_skills_dampens_score(make_job, make_profile):
    profile = make_profile(skills={"languages": [], "frameworks": [], "tools": []})
    result = score_job(make_job(), profile)
    assert result["fit_score"] < 85
    assert result["tier"] != "A"


def test_fit_score_stays_in_bounds(make_job, make_profile):
    profile = make_profile(experience=[])
    job = make_job(
        title="Whatever",
        skills=["ruby"],
        experienceLevel="entry",
        location="Nowhere",
        remote=False,
    )
    result = score_job(job, profile)
    assert 0 <= result["fit_score"] <= 100
    assert isinstance(result["fit_score"], int)


def test_all_skills_aggregates_skill_categories(make_profile):
    profile = make_profile()
    profile["skills"] = {
        "languages": ["python", "go"],
        "frameworks": ["django"],
        "ml": [],
        "tools": ["docker"],
        "other": ["linux"],
    }
    skills = all_skills(profile)
    assert set(skills) == {"python", "go", "django", "docker", "linux"}


def test_format_decision_renders_verdict(make_job, make_profile):
    result = score_job(make_job(), make_profile())
    text = format_decision(result)
    assert f"{result['fit_score']}/100" in text
    assert result["verdict"] in text
    assert "Acme Inc" in text