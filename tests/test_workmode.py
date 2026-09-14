from __future__ import annotations

from job_agent.workmode import classify_work_mode


def job(**overrides):
    base = {
        "title": "Platform Engineer",
        "location": "",
        "description": "",
        "remote": False,
    }
    base.update(overrides)
    return base


def test_remote_location_is_remote():
    assert classify_work_mode(job(location="Remote")) == "remote"
    assert classify_work_mode(job(location="Remote (US)")) == "remote"


def test_remote_in_description_is_remote():
    assert classify_work_mode(job(description="Fully remote role across EU timezones.")) == "remote"


def test_hybrid_passes_as_hybrid():
    assert classify_work_mode(job(description="Hybrid role, 2 days per week in office")) == "hybrid"
    assert classify_work_mode(job(location="Hybrid - New York")) == "hybrid"


def test_on_site_is_excluded():
    assert classify_work_mode(job(description="On-site position in Bangalore office")) == "on_site"
    assert classify_work_mode(job(location="New Delhi, India", description="onsite role")) == "on_site"


def test_unknown_when_unspecified():
    assert (
        classify_work_mode(job(location="Delhi, India", description="Join a growing team."))
        == "unknown"
    )
    assert classify_work_mode(job()) == "unknown"


def test_source_remote_flag_establishes_remote():
    assert classify_work_mode(job(remote=True)) == "remote"


def test_negated_remote_is_not_remote():
    assert classify_work_mode(job(description="This is an on-site role, not remote.")) == "on_site"
    assert classify_work_mode(job(description="No remote work available.")) == "unknown"


def test_priorities_hybrid_over_remote_over_onsite():
    assert classify_work_mode(job(description="Remote-hybrid team")) == "hybrid"
    assert classify_work_mode(job(location="Remote", description="On-site equipment provided")) == "remote"