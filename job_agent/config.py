from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile" / "profile.json"
PROFILE_EXAMPLE_PATH = ROOT / "profile" / "profile.example.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "job_agent.db"
DISCOVERY_CATALOG_PATH = DATA_DIR / "boards.json"


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


def job_sources() -> list[str]:
    """Configured source list. JOB_SOURCES (comma-separated) wins; otherwise the
    single JOB_SOURCE value is used so a plain setup behaves exactly as before."""
    raw = env("JOB_SOURCES")
    if raw:
        return [name.strip() for name in raw.split(",") if name.strip()]
    single = job_source()
    return [single] if single else []


def llm_provider() -> str:
    return env("LLM_PROVIDER", "none") or "none"


def ollama_base_url() -> str:
    return env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    return env("OLLAMA_MODEL", "llama3.2")


def ollama_timeout() -> float:
    try:
        return float(env("OLLAMA_TIMEOUT", "180"))
    except ValueError:
        return 180.0


def ollama_num_gpu() -> int:
    """Layers to offload to the GPU. -1 means all layers (GPU priority always)."""
    try:
        return int(env("OLLAMA_NUM_GPU", "-1"))
    except ValueError:
        return -1


def jobgpt_api_key() -> str:
    return env("JOBGPT_API_KEY")


def jobgpt_api_url() -> str:
    return env("JOBGPT_API_URL", "https://6figr.com").rstrip("/")


def greenhouse_board() -> str:
    return env("GREENHOUSE_BOARD")


def greenhouse_api_url() -> str:
    return env("GREENHOUSE_API_URL", "https://boards-api.greenhouse.io/v1").rstrip("/")


def lever_company() -> str:
    return env("LEVER_COMPANY")


def lever_api_url() -> str:
    return env("LEVER_API_URL", "https://api.lever.co/v0").rstrip("/")


def ashby_board() -> str:
    return env("ASHBY_BOARD")


def ashby_api_url() -> str:
    return env("ASHBY_API_URL", "https://api.ashbyhq.com/posting-api").rstrip("/")


def remotive_api_url() -> str:
    return env("REMOTIVE_API_URL", "https://remotive.com/api").rstrip("/")


def jobicy_api_url() -> str:
    return env("JOBICY_API_URL", "https://jobicy.com/api/v2").rstrip("/")


def adzuna_app_id() -> str:
    return env("ADZUNA_APP_ID")


def adzuna_app_key() -> str:
    return env("ADZUNA_APP_KEY")


def adzuna_country() -> str:
    return env("ADZUNA_COUNTRY", "in").strip()

def adzuna_api_url() -> str:
    return env("ADZUNA_API_URL", "https://api.adzuna.com/v1/api").rstrip("/")


def smartrecruiters_api_url() -> str:
    return env("SMARTRECRUITERS_API_URL", "https://api.smartrecruiters.com/v1").rstrip("/")
