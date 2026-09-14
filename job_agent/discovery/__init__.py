"""Provider-agnostic board discovery: catalog model, probing, and wiring.

Discovery turns a list of company names into *verified board candidates*
(Greenhouse/Lever/Ashby/SmartRecruiters slugs confirmed against each vendor's
documented public API). The catalog is a plain gitignored JSON file so the core
pipeline never hard-codes a company list.
"""

from job_agent.config import DISCOVERY_CATALOG_PATH
from job_agent.discovery.model import (
    DISCOVERY_ATSS,
    BoardCandidate,
    Catalog,
    is_fresh,
    slug_variants,
)
from job_agent.discovery.probe import probe_company_boards
from job_agent.discovery.wiring import sources_from_catalog


def load_catalog(path=None):
    """Load (or create empty) the board catalog at ``path`` (default: data/boards.json)."""
    return Catalog.load(path or DISCOVERY_CATALOG_PATH)


__all__ = [
    "BoardCandidate",
    "Catalog",
    "DISCOVERY_ATSS",
    "is_fresh",
    "load_catalog",
    "probe_company_boards",
    "slug_variants",
    "sources_from_catalog",
]