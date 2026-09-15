from __future__ import annotations

import pytest

from job_agent.relevance import (
    CANDIDATE_CATEGORIES,
    IRRELEVANT_CATEGORY,
    POSSIBLE_CATEGORY,
    STRONG_CATEGORY,
    classify_location,
    classify_relevance,
)


def make_job(**overrides):
    base = {
        "title": "AI Engineer",
        "location": "Remote",
        "remote": True,
        "description": "Build ML systems.",
    }
    base.update(overrides)
    return base


def make_profile(target_roles=None):
    return {
        "personal": {},
        "preferences": {
            "target_roles": target_roles
            or ["AI Engineer", "Machine Learning Engineer", "ML Engineer", "Python Developer"]
        },
    }


def category(job, profile=None):
    return classify_relevance(job, profile or make_profile()).category


def test_candidate_categories_constants():
    assert CANDIDATE_CATEGORIES == {STRONG_CATEGORY, POSSIBLE_CATEGORY}
    assert IRRELEVANT_CATEGORY not in CANDIDATE_CATEGORIES


@pytest.mark.parametrize(
    "title",
    [
        "AI Engineer",
        "Senior Machine Learning Engineer",
        "Machine Learning Operations Engineer",
        "ML Engineer",
        "NLP Engineer",
        "Computer Vision Engineer",
        "Senior Python Developer",
        "Python Backend Engineer",
        "Backend Engineer",
        "Software Engineer",
        "Full Stack AI Engineer",
        "Deep Learning Engineer",
        "GenAI Engineer",
        "MLOps Engineer",
    ],
)
def test_target_engineering_titles_are_candidates(title):
    assert category(make_job(title=title)) in CANDIDATE_CATEGORIES


@pytest.mark.parametrize(
    "title",
    [
        "Account Executive",
        "Account Manager",
        "Recruiter",
        "Recruiting Manager",
        "Recruiting Coordinator",
        "Customer Success Manager",
        "Marketing Manager",
        "Legal Counsel",
        "Finance Manager",
        "Sales Engineer",
        "Solutions Engineer",
        "Salesforce Developer",
        "HR Generalist",
        "Business Development Representative",
        "Support Engineer",
        "Presales Engineer",
        "Sales Development Representative",
        "Data Analyst",
    ],
)
def test_non_target_titles_irrelevant_even_with_ai_keywords(title):
    job = make_job(title=title, description="AI and machine learning everywhere, apply now!")
    assert category(job) == IRRELEVANT_CATEGORY


def test_engineering_family_without_signal_is_possible():
    assert category(make_job(title="Platform Engineer", description="Join our AI products team")) == POSSIBLE_CATEGORY
    assert category(make_job(title="Web Developer")) == POSSIBLE_CATEGORY
    assert category(make_job(title="API Platform Engineer")) == POSSIBLE_CATEGORY


def test_target_role_phrase_is_strong_and_reasoned():
    job = make_job(title="Senior Python Developer")
    result = classify_relevance(job, make_profile(target_roles=["Python Developer"]))
    assert result.category == STRONG_CATEGORY
    assert result.positive == ["Python Developer"]


def test_stack_signal_inside_engineering_title_is_strong():
    job = make_job(title="AI Product Manager")
    assert category(job) == IRRELEVANT_CATEGORY
    job = make_job(title="AI Engineer")
    assert category(job) == STRONG_CATEGORY


def test_irrelevant_title_without_any_signal():
    assert category(make_job(title="Toastmaster")) == IRRELEVANT_CATEGORY


def test_missing_title_is_irrelevant():
    assert category(make_job(title="")) == IRRELEVANT_CATEGORY
    assert classify_relevance(make_job(title="   "), make_profile()).category == IRRELEVANT_CATEGORY


@pytest.mark.parametrize(
    "location,description,expected",
    [
        ("Remote", None, "unknown"),
        ("Remote (Anywhere)", None, "remote_global"),
        ("Remote", "Work from anywhere in the world.", "remote_global"),
        (None, "Remote-friendly role.", "unknown"),
        ("", None, "unknown"),
        ("Somewhere else", None, "unknown"),
        ("Remote", "Candidate must be based in the US only.", "foreign"),
        ("Remote (US Only)", None, "foreign"),
        ("New York, NY", None, "foreign"),
        ("London, UK", None, "foreign"),
        ("Berlin, Germany", None, "foreign"),
        ("Hyderabad, India", "Hybrid, 2 days per week.", "india_compatible"),
        ("Remote | Bengaluru, India | Dresden, Germany", None, "india_compatible"),
        ("Bengaluru, India", None, "india_compatible"),
        ("Remote — India", None, "india_compatible"),
        ("Remote — Anywhere in India", None, "india_compatible"),
        ("Hybrid — Bengaluru", None, "india_compatible"),
        ("Remote — India / UK", None, "india_compatible"),
        ("Remote — Worldwide", None, "remote_global"),
        ("Remote — Global", None, "remote_global"),
        ("Remote — US only", None, "foreign"),
        ("Remote — United States", None, "foreign"),
        ("Remote — UK / EU", None, "foreign"),
        ("Remote — Europe", None, "foreign"),
    ],
)
def test_location_categories(location, description, expected):
    job = make_job(location=location, description=description)
    assert classify_location(job) == expected