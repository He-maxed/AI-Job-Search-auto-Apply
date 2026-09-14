from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile" / "profile.json"
PROFILE_EXAMPLE_PATH = ROOT / "profile" / "profile.example.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "job_agent.db"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def jobgpt_api_key() -> str:
    return os.environ.get("JOBGPT_API_KEY", "").strip()


def jobgpt_api_url() -> str:
    return os.environ.get("JOBGPT_API_URL", "https://6figr.com").rstrip("/")
