from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from job_agent.config import DISCOVERY_CATALOG_PATH

# ATS providers the probe knows how to verify against their public APIs.
DISCOVERY_ATSS = ("greenhouse", "lever", "ashby", "smartrecruiters")

DEFAULT_CACHE_TTL = timedelta(hours=24)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug_variants(name: str) -> list[str]:
    """Deterministic board-slug guesses for a company name.

    Only two conservative transformations (compact and kebab) — anything more
    is guessing. The probe verifies every guess against the vendor API, so a
    wrong guess is a 404, never fabricated data.
    """
    cleaned = re.sub(r"[^\w]+", " ", name.strip()).strip().lower()
    if not cleaned:
        return []
    compact = cleaned.replace(" ", "")
    kebab = re.sub(r"\s+", "-", cleaned)
    seen: list[str] = []
    for variant in (compact, kebab):
        if variant and variant not in seen:
            seen.append(variant)
    return seen


def is_fresh(
    verified_at: str | None,
    ttl: timedelta | None = DEFAULT_CACHE_TTL,
    now: str | None = None,
) -> bool:
    if not verified_at:
        return False
    try:
        stamp = datetime.fromisoformat(verified_at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    reference = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return stamp >= reference - (ttl or timedelta(0))


@dataclass
class BoardCandidate:
    """One verified (or candidate) company board on one ATS."""

    company: str
    ats: str
    slug: str
    url: str = ""
    verified_at: str | None = None
    enabled: bool = True
    note: str = ""
    error: str | None = None

    def key(self) -> str:
        return f"{self.ats}:{self.slug}"

    @property
    def verified(self) -> bool:
        return bool(self.verified_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "ats": self.ats,
            "slug": self.slug,
            "url": self.url,
            "verified_at": self.verified_at,
            "enabled": self.enabled,
            "note": self.note,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BoardCandidate":
        return cls(
            company=str(data.get("company") or ""),
            ats=str(data.get("ats") or ""),
            slug=str(data.get("slug") or ""),
            url=str(data.get("url") or ""),
            verified_at=data.get("verified_at"),
            enabled=bool(data.get("enabled", True)),
            note=str(data.get("note") or ""),
            error=data.get("error"),
        )


@dataclass
class Catalog:
    """Persisted board catalog. Never part of the seeded repo; lives in data/."""

    path: Path = field(default_factory=lambda: DISCOVERY_CATALOG_PATH)
    candidates: list[BoardCandidate] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Catalog":
        catalog = cls(path=Path(path) if path else DISCOVERY_CATALOG_PATH)
        if catalog.path.exists():
            try:
                data = json.loads(catalog.path.read_text(encoding="utf-8"))
                for item in data.get("candidates", []):
                    catalog.candidates.append(BoardCandidate.from_dict(item))
            except (ValueError, OSError) as exc:
                raise ValueError(f"unreadable discovery catalog {catalog.path}: {exc}") from exc
        return catalog

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add(self, candidate: BoardCandidate) -> BoardCandidate:
        """Merge by (ats, slug), keeping the newest verification and prior notes."""
        for index, existing in enumerate(self.candidates):
            if existing.key() != candidate.key():
                continue
            merged = BoardCandidate(
                company=candidate.company or existing.company,
                ats=candidate.ats,
                slug=candidate.slug,
                url=candidate.url or existing.url,
                verified_at=candidate.verified_at or existing.verified_at,
                enabled=candidate.enabled if candidate.verified_at else existing.enabled,
                note=candidate.note or existing.note,
                error=candidate.error or existing.error,
            )
            self.candidates[index] = merged
            return merged
        self.candidates.append(candidate)
        return candidate

    def verified(self) -> list[BoardCandidate]:
        return [candidate for candidate in self.candidates if candidate.verified]

    def verified_enabled(self) -> list[BoardCandidate]:
        return [candidate for candidate in self.candidates if candidate.verified and candidate.enabled]

    def fresh(self, ttl: timedelta | None = DEFAULT_CACHE_TTL) -> list[BoardCandidate]:
        return [c for c in self.verified() if is_fresh(c.verified_at, ttl)]

    def have_fresh(self, ats: str, slug: str, ttl: timedelta | None = DEFAULT_CACHE_TTL) -> bool:
        return any(
            candidate.ats == ats
            and candidate.slug == slug
            and candidate.enabled
            and is_fresh(candidate.verified_at, ttl)
            for candidate in self.candidates
        )