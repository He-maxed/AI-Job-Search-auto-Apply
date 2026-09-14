from __future__ import annotations

import pytest

from job_agent.memory import Memory


@pytest.fixture
def db(tmp_path):
    memory = Memory(tmp_path / "test.db")
    yield memory
    memory.close()


def test_upsert_inserts_new_job(db):
    db.upsert_job(
        {
            "id": "j1",
            "title": "Engineer",
            "company": "Co",
            "url": "u",
            "location": "Remote",
            "remote": True,
        }
    )
    assert db.known_job_ids() == {"j1"}
    row = db.conn.execute("SELECT * FROM jobs WHERE id = 'j1'").fetchone()
    assert row["title"] == "Engineer"
    assert row["remote"] == 1
    assert row["first_seen_at"] == row["last_seen_at"]


def test_upsert_is_idempotent_and_preserves_first_seen(db):
    db.upsert_job({"id": "j1", "title": "First", "company": "Co"})
    first = db.conn.execute("SELECT first_seen_at FROM jobs WHERE id='j1'").fetchone()[0]
    db.upsert_job({"id": "j1", "title": "Second", "company": "Co", "location": "Remote"})
    row = db.conn.execute("SELECT * FROM jobs WHERE id='j1'").fetchone()
    assert row["title"] == "Second"
    assert row["first_seen_at"] == first


def test_upsert_requires_id(db):
    with pytest.raises(ValueError):
        db.upsert_job({"title": "no id"})


def test_save_score_inserts_then_updates(db):
    db.upsert_job({"id": "j1"})
    db.save_score("j1", {"fit_score": 80, "tier": "B", "priority": 0.42})
    db.save_score("j1", {"fit_score": 96, "tier": "A", "priority": 0.9})
    row = db.conn.execute("SELECT * FROM scores WHERE job_id='j1'").fetchone()
    assert row["fit_score"] == 96
    assert row["tier"] == "A"
    assert row["priority"] == pytest.approx(0.9)


def test_known_job_ids_empty_then_populated(tmp_path):
    memory = Memory(tmp_path / "other.db")
    assert memory.known_job_ids() == set()
    memory.upsert_job({"id": "a"})
    memory.upsert_job({"id": "b"})
    assert memory.known_job_ids() == {"a", "b"}
    memory.close()


def test_schema_tables_exist(db):
    tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for want in (
        "jobs",
        "scores",
        "applications",
        "application_events",
        "resume_versions",
        "interviews",
        "recruiters",
        "referrals",
    ):
        assert want in tables