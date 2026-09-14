from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_agent.config import DATA_DIR, DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'jobgpt',
    title TEXT,
    company TEXT,
    url TEXT,
    location TEXT,
    remote INTEGER,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    job_id TEXT PRIMARY KEY,
    fit_score INTEGER NOT NULL,
    tier TEXT NOT NULL,
    priority REAL NOT NULL,
    explanation_json TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    jobgpt_application_id TEXT,
    status TEXT NOT NULL,
    resume_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER,
    job_id TEXT,
    event_type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    job_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    company TEXT,
    status TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recruiters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    name TEXT,
    email TEXT,
    linkedin_url TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    name TEXT,
    email TEXT,
    linkedin_url TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    UNIQUE(job_id, version),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


MERGE_KEYS = (
    "title",
    "company",
    "url",
    "applyUrl",
    "location",
    "description",
    "experienceLevel",
    "postedAt",
)
LIST_KEYS = ("skills",)
NUM_KEYS = ("salaryMin", "salaryMax")


def merge_job_payload(stored: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Merge a fresh fetch into the stored payload without losing useful data.

    Fresh non-empty values win; stored values are kept when the fresh fetch
    provides nothing (e.g. a truncated description or a run without LLM
    enrichment). This keeps first-seen data and prior analysis intact.
    """
    out = dict(fresh)
    for key in MERGE_KEYS:
        if not out.get(key) and stored.get(key):
            out[key] = stored[key]
    for key in LIST_KEYS:
        if not out.get(key) and stored.get(key):
            out[key] = list(stored[key])
    for key in NUM_KEYS:
        if out.get(key) is None and stored.get(key) is not None:
            out[key] = stored[key]
    if stored.get("remote") and not out.get("remote"):
        out["remote"] = True
    return out


class Memory:
    def __init__(self, path: Path | None = None):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or DB_PATH
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_job(self, job: dict[str, Any], source: str = "jobgpt") -> None:
        now = utcnow()
        job_id = str(job.get("id") or "")
        if not job_id:
            raise ValueError("Job is missing id")
        existing = self.conn.execute(
            "SELECT first_seen_at, raw_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        payload = json.dumps(job, ensure_ascii=False)
        if existing:
            stored = json.loads(existing["raw_json"])
            merged = merge_job_payload(stored, job)
            payload = json.dumps(merged, ensure_ascii=False)
            self.conn.execute(
                """
                UPDATE jobs SET title=?, company=?, url=?, location=?, remote=?, raw_json=?, last_seen_at=?
                WHERE id=?
                """,
                (
                    merged.get("title"),
                    merged.get("company"),
                    merged.get("url") or merged.get("applyUrl"),
                    merged.get("location"),
                    1 if merged.get("remote") else 0,
                    payload,
                    now,
                    job_id,
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO jobs (id, source, title, company, url, location, remote, raw_json, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    source,
                    job.get("title"),
                    job.get("company"),
                    job.get("url") or job.get("applyUrl"),
                    job.get("location"),
                    1 if job.get("remote") else 0,
                    payload,
                    now,
                    now,
                ),
            )
        self.conn.commit()

    def save_score(self, job_id: str, result: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO scores (job_id, fit_score, tier, priority, explanation_json, scored_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                fit_score=excluded.fit_score,
                tier=excluded.tier,
                priority=excluded.priority,
                explanation_json=excluded.explanation_json,
                scored_at=excluded.scored_at
            """,
            (
                job_id,
                result["fit_score"],
                result["tier"],
                result["priority"],
                json.dumps(result, ensure_ascii=False),
                utcnow(),
            ),
        )
        self.conn.commit()

    def known_job_ids(self) -> set[str]:
        rows = self.conn.execute("SELECT id FROM jobs").fetchall()
        return {row["id"] for row in rows}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT raw_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return json.loads(row["raw_json"])

    def get_decision(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT explanation_json FROM scores WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["explanation_json"])

    def save_resume_draft(
        self,
        job_id: str,
        draft: dict[str, Any],
        provider: str,
        model: str | None = None,
    ) -> int:
        existing = self.conn.execute(
            "SELECT MAX(version) AS v FROM resume_drafts WHERE job_id = ?", (job_id,)
        ).fetchone()
        version = (existing["v"] or 0) + 1
        provenance = draft.get("source_claims") or []
        self.conn.execute(
            """
            INSERT INTO resume_drafts (job_id, version, provider, model, created_at, draft_json, provenance_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                version,
                provider,
                model,
                utcnow(),
                json.dumps(draft, ensure_ascii=False),
                json.dumps(provenance, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return version

    def list_resume_drafts(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT version, provider, model, created_at, draft_json FROM resume_drafts "
            "WHERE job_id = ? ORDER BY version",
            (job_id,),
        ).fetchall()
        return [
            {
                "version": row["version"],
                "provider": row["provider"],
                "model": row["model"],
                "created_at": row["created_at"],
                "draft": json.loads(row["draft_json"]),
            }
            for row in rows
        ]

    def pick_best_job(self) -> dict[str, Any] | None:
        """Highest-priority scored job eligible for tailoring (tier A/B, remote/hybrid)."""
        from job_agent.workmode import PRIMARY_MODES

        rows = self.conn.execute(
            """
            SELECT s.job_id AS job_id, s.fit_score AS fit_score, s.priority AS priority,
                   s.explanation_json AS explanation_json, j.raw_json AS raw_json
            FROM scores s JOIN jobs j ON j.id = s.job_id
            ORDER BY s.fit_score DESC, s.priority DESC
            """
        ).fetchall()
        for row in rows:
            decision = json.loads(row["explanation_json"])
            if decision.get("tier") not in ("A", "B"):
                continue
            job = json.loads(row["raw_json"])
            if (job.get("workMode") or job.get("work_mode")) not in PRIMARY_MODES:
                continue
            return {"job_id": row["job_id"], "job": job, "decision": decision}
        return None

    def close(self) -> None:
        self.conn.close()
