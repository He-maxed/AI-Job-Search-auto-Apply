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


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def job_source() -> str:
    return env("JOB_SOURCE", "jobgpt") or "jobgpt"


def llm_provider() -> str:
    return env("LLM_PROVIDER", "none") or "none"


def ollama_base_url() -> str:
    return env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    return env("OLLAMA_MODEL", "llama3.2")


def jobgpt_api_key() -> str:
    return env("JOBGPT_API_KEY")


def jobgpt_api_url() -> str:
    return env("JOBGPT_API_URL", "https://6figr.com").rstrip("/")
