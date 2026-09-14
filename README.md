# job_agent

Provider-agnostic personal job-hunting intelligence layer. Job sources and AI/LLM
providers plug in behind small interfaces; the deterministic core (scoring,
memory, approval workflow) never requires a paid API, JobGPT, or OpenCode.

Auto-apply is **disabled**. Nothing here submits an application, spends credits,
fabricates experience, or bypasses any security control.

## Architecture

Two independent provider layers, selected by configuration:

- **Job source** — `job_agent/jobs`. Interface `JobSource` emits a normalized
  `Job` model from a `JobQuery`. Adapters live under `job_agent/jobs/sources/`.
  Current adapters: `jobgpt` (optional), `greenhouse` (public job-board API).
  Future: Lever, RSS, other legitimate sources.
- **LLM provider** — `job_agent/llm`. Interface `LLMProvider` (`complete(...)`).
  Default `none` (deterministic-only). First-class optional local provider:
  `ollama`. OpenAI-compatible/Gemini/Anthropic can be added later behind the
  same interface.

Flow: DISCOVER → NORMALIZE → DEDUPLICATE → SCORE → RANK → APPROVAL QUEUE →
HUMAN APPROVES → (prepare/submit where legitimate) → RECORD RESULT.
Never auto-submit.

Deterministic functionality (ingestion, normalization, dedup, scoring, SQLite,
approval preparation) never requires an LLM. If the configured LLM is
unavailable, AI-dependent features report **"LLM provider unavailable"**.

## Setup

```powershell
cd C:\Users\Admin\job_agent
copy .env.example .env
copy profile\profile.example.json profile\profile.json
```

1. Fill `profile/profile.json` with **facts only**. Do not invent jobs, metrics,
   skills, or dates.
2. Configure `.env`:
   - `JOB_SOURCE=jobgpt` or `greenhouse`
   - `JOBGPT_API_KEY=...` if using JobGPT (generate at
     https://6figr.com/account → MCP Integrations; never commit it)
   - `GREENHOUSE_BOARD=<board token>` to search Greenhouse's public jobs API
     (token = the slug on `boards.greenhouse.io/<token>`; e.g. `stripe`)
   - `LLM_PROVIDER=none` (deterministic) or `ollama` for local AI features
3. Run:

```powershell
python -m job_agent run
# or select the source on the command line:
python -m job_agent run --source greenhouse
```

Optional LLM feature — structured job analysis via the configured provider:

```powershell
python -m job_agent analyze --text "Backend engineer, Python and SQL, 5+ years, remote"
# or:  Get-Content job.txt | python -m job_agent analyze
# add --score to also run the deterministic scorer on the analysis-enriched job
```

When `LLM_PROVIDER=ollama`, analysis runs against a local Ollama model. When no
LLM is available the command reports **"LLM provider unavailable"** and the app
keeps working deterministically — analysis never falls back silently to another
provider, and the LLM may only extract facts explicitly stated in the posting.

OpenCode is the development agent only. The Python app is standalone-runnable
and does not depend on OpenCode's model, config, or MCP servers.

## Implemented

- Provider-neutral `JobSource` / normalized `Job` + `JobQuery` with registry
- `jobgpt` adapter (optional) and `greenhouse` adapter (public job-board API,
  no auth required)
- Provider-neutral `LLMProvider` with `none` default and `ollama` optional
- Structured job-description analysis (`analyze`) with a strict schema — only
  explicitly-stated facts; deterministic scoring can consume the enriched job
- Deterministic fit score 0–100 with tiers A/B/C/D
- SQLite memory under `data/job_agent.db` (gitignored)
- Approval packet preview only — no submit
- pytest suite covering scoring, memory, job-source, LLM, and analysis layers

## Not built yet

- Additional job source adapters (Lever, RSS, …)
- Resume tailoring, cover letters, application drafting
- Human APPROVE → submit wiring
- Outcome analytics