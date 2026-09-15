from __future__ import annotations

import pytest

from job_agent.score import score_job


def _ai_profile(make_profile):
    return make_profile(
        education=[{"degree": "M.Tech", "field": "Artificial Intelligence and Data Science"}],
        experience=[
            {
                "role": "R&D Intern",
                "years": 0.5,
                "tools": ["Python", "PyTorch", "TensorFlow", "NLP libraries", "signal analysis"],
            }
        ],
        projects=[
            {"name": "Medical Imaging CNN", "summary": "CNN image segmentation", "technologies": ["Python", "Keras", "TensorFlow"]},
            {"name": "Face Recognition", "summary": "OpenCV CNN", "technologies": ["OpenCV", "CNN", "Python"]},
        ],
        skills={
            "languages": ["Python", "Java"],
            "frameworks": [],
            "ml": ["Pandas", "TensorFlow", "OpenCV"],
            "tools": ["MySQL", "SQL"],
            "other": ["Data Structures and Algorithms"],
        },
        preferences={
            "target_roles": [
                "AI Engineer",
                "Machine Learning Engineer",
                "ML Engineer",
                "NLP Engineer",
                "Computer Vision Engineer",
                "Python Developer",
            ],
            "excluded_roles": [],
            "excluded_companies": [],
            "excluded_keywords": [],
            "seniority": [],
            "employment_types": ["full_time"],
            "salary": {"min": None, "target": None},
        },
    )


def test_ai_platform_engineer_scores_materially_above_unrelated(make_job, make_profile):
    profile = _ai_profile(make_profile)
    ai = score_job(
        make_job(
            title="AI Platform Engineer",
            skills=[],
            description="Build an AI platform: training ML models with Python and TensorFlow.",
        ),
        profile,
    )
    unrelated = score_job(
        make_job(
            title="Facilities Manager",
            skills=[],
            description="Building maintenance, vendor management and office operations.",
        ),
        profile,
    )
    assert ai["fit_score"] - unrelated["fit_score"] >= 25
    assert ai["fit_breakdown"]["roleFamilyFit"] == 0.75
    assert ai["fit_breakdown"]["matchedSkills"]
    assert "python" in ai["fit_breakdown"]["matchedSkills"]
    assert not ai["blockers"]


def test_ai_research_engineer_receives_research_modeling_credit(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="AI Research Engineer",
            skills=[],
            description="Research and build ML models using Python and PyTorch.",
        ),
        profile,
    )
    assert result["fit_breakdown"]["roleFamilyFit"] == 0.75
    assert result["fit_breakdown"]["domainFit"] == 0.65
    assert any("target-stack role family" in m and "ai_ml" in m for m in result["fit_breakdown"]["matchedRoles"])


def test_ml_ops_engineer_not_penalized_for_operations(make_job, make_profile):
    profile = _ai_profile(make_profile)
    desc = "Operate ML training pipelines in Python."
    ops = score_job(make_job(title="Machine Learning Operations Engineer", skills=[], description=desc), profile)
    generic = score_job(make_job(title="Software Engineer", skills=[], description=desc), profile)
    assert ops["fit_breakdown"]["roleFamilyFit"] == 0.75
    assert not any("operations" in str(m).lower() for m in ops["fit_breakdown"]["mismatches"])
    assert ops["fit_score"] >= 70
    assert ops["fit_score"] > generic["fit_score"]


def test_nlp_engineer_gets_strong_profile_alignment(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="NLP Engineer",
            skills=[],
            description="Develop speech and language models with Python and TensorFlow.",
        ),
        profile,
    )
    assert result["fit_breakdown"]["roleFamilyFit"] == 1.0
    assert result["fit_breakdown"]["domainFit"] == 0.8
    assert set(result["fit_breakdown"]["matchedSkills"]) >= {"python", "tensorflow"}


def test_computer_vision_engineer_gets_strong_cv_alignment(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="Computer Vision Engineer",
            skills=[],
            description="Image segmentation using OpenCV, CNN and TensorFlow.",
        ),
        profile,
    )
    assert result["fit_breakdown"]["roleFamilyFit"] == 1.0
    assert result["fit_breakdown"]["domainFit"] == 0.65
    assert "opencv" in result["fit_breakdown"]["matchedSkills"]


def test_python_developer_gets_strong_python_credit(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="Python Developer",
            skills=[],
            description="Design and build services with Python, Flask and SQL.",
        ),
        profile,
    )
    assert result["fit_breakdown"]["roleFamilyFit"] == 1.0
    assert result["fit_score"] >= 70
    assert "python" in result["fit_breakdown"]["matchedSkills"]


def test_ai_product_manager_gets_no_engineering_credit(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="AI Product Manager",
            skills=[],
            description="Define an AI-powered product strategy and roadmap.",
        ),
        profile,
    )
    assert result["fit_breakdown"]["roleFamilyFit"] == 0.0
    assert result["fit_breakdown"]["mismatches"]
    assert result["fit_score"] < 30


@pytest.mark.parametrize(
    "title",
    ["Sales Engineer", "Solutions Engineer", "Account Executive", "Customer Success Manager", "Recruiter"],
)
def test_non_engineering_titles_remain_low_or_irrelevant(title, make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title=title,
            skills=[],
            description="Close deals and manage AI and machine learning customer relationships with Python.",
        ),
        profile,
    )
    assert result["fit_breakdown"]["roleFamilyFit"] == 0.0
    assert result["fit_score"] < 35


def test_generic_backend_engineer_not_inflated_as_ai_match(make_job, make_profile):
    profile = _ai_profile(make_profile)
    backend = score_job(
        make_job(title="Backend Engineer", skills=[],
                 description="Backend API services in Python."),
        profile,
    )
    ai = score_job(
        make_job(title="AI Platform Engineer", skills=[],
                 description="Backend API services in Python for ML models."),
        profile,
    )
    assert backend["fit_breakdown"]["roleFamilyFit"] < 0.75
    assert backend["fit_breakdown"]["roleFamilyFit"] == 0.55
    assert ai["fit_score"] > backend["fit_score"]


def test_skills_later_in_description_still_count(make_job, make_profile):
    profile = _ai_profile(make_profile)
    padded = "x" * 600 + "Strong Python and PyTorch skills are required for this role."
    result = score_job(make_job(title="AI Engineer", skills=[], description=padded), profile)
    assert "python" in result["fit_breakdown"]["matchedSkills"]
    assert "pytorch" in result["fit_breakdown"]["matchedSkills"]
    assert result["fit_breakdown"]["skillFit"] == 1.0


def test_foreign_remote_ml_job_does_not_override_geo(make_job, make_profile):
    profile = _ai_profile(make_profile)
    kw = dict(title="Machine Learning Engineer", skills=[], description="ML models with Python and TensorFlow.")
    global_job = score_job(make_job(**kw, location="Remote (Anywhere)"), profile)
    us_only = score_job(make_job(**kw, location="Remote (US)"), profile)
    assert global_job["fit_breakdown"]["geoEligible"] is True
    assert us_only["fit_breakdown"]["geoEligible"] is False
    assert us_only["fit_breakdown"]["foreignLocation"] is True
    assert any("Foreign" in m for m in us_only["fit_breakdown"]["mismatches"])
    assert us_only["fit_score"] < global_job["fit_score"]


def test_india_compatible_hybrid_ml_job_stays_eligible(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="Machine Learning Engineer",
            location="Bengaluru, India",
            description="Hybrid role. ML models with Python and TensorFlow.",
            workMode="hybrid",
        ),
        profile,
    )
    assert result["fit_breakdown"]["geoEligible"] is True
    assert result["fit_breakdown"]["foreignLocation"] is False
    assert result["fit_breakdown"]["locationFit"] == 1.0


def test_unknown_work_mode_ml_job_stays_deferred(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(
        make_job(
            title="ML Engineer",
            location=None,
            remote=False,
            description="Develop ML models with Python and TensorFlow.",
            workMode="unknown",
        ),
        profile,
    )
    assert result["fit_breakdown"]["locationFit"] == 0.5
    assert any("deferred" in n for n in result["notes"])


def test_score_is_deterministic(make_job, make_profile):
    profile = _ai_profile(make_profile)
    job = make_job(title="NLP Engineer", skills=[], description="Speech models with Python and PyTorch.")
    first = score_job(job, profile)
    second = score_job(job, profile)
    assert first == second


def test_fit_breakdown_exposes_structured_metadata(make_job, make_profile):
    profile = _ai_profile(make_profile)
    result = score_job(make_job(title="Computer Vision Engineer", skills=[],
                                description="Image models with OpenCV."), profile)
    bd = result["fit_breakdown"]
    for key in ("titleFit", "roleFamilyFit", "skillFit", "domainFit", "requirementFit",
                "locationFit", "matchedSkills", "matchedRoles", "mismatches",
                "negativeSignals", "foreignLocation", "geoEligible"):
        assert key in bd
    assert set(bd["weights"]) == {"role", "skill", "domain", "requirement", "location"}
    assert sum(bd["weights"].values()) == 100