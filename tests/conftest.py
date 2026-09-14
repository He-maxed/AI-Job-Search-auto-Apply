from __future__ import annotations

import copy

import pytest


BASE_JOB = {
    "id": "job-1",
    "title": "Senior Python Developer",
    "company": "Acme Inc",
    "description": "Build web services with Python and Django in a remote team.",
    "skills": ["python", "django"],
    "experienceLevel": "senior",
    "location": "Remote",
    "remote": True,
    "salaryMin": 140000,
    "salaryMax": 180000,
    "applyUrl": "https://acme.example/apply",
    "url": "https://acme.example/jobs/1",
}

BASE_PROFILE = {
    "personal": {"full_name": "Test User"},
    "locations": {
        "current": "Bengaluru",
        "preferred": ["Bengaluru"],
        "remote_ok": True,
        "willing_to_relocate": False,
    },
    "work_authorization": {"requires_sponsorship": False},
    "education": [],
    "experience": [{"role": "Software Engineer", "years": 6}],
    "projects": [],
    "skills": {"languages": ["python"], "frameworks": ["django"], "tools": []},
    "certifications": [],
    "achievements": [],
    "preferences": {
        "target_roles": ["Python Developer"],
        "excluded_roles": [],
        "excluded_companies": [],
        "excluded_keywords": [],
        "seniority": ["senior"],
        "salary": {"min": 100000, "target": 150000},
    },
    "application": {},
}


@pytest.fixture
def make_job():
    def _make(**overrides):
        job = copy.deepcopy(BASE_JOB)
        job.update(overrides)
        return job

    return _make


@pytest.fixture
def make_profile():
    def _make(**overrides):
        profile = copy.deepcopy(BASE_PROFILE)
        profile.update(overrides)
        return profile

    return _make