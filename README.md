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
  Current adapter: `jobgpt` (optional). Future: Greenhouse, Lever, RSS, other
  legitimate sources.
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
   - `JOB_SOURCE=jobgpt` (the only adapter shipped today)
   - `JOBGPT_API_KEY=...` if using JobGPT (generate at
     https://6figr.com/account → MCP Integrations; never commit it)
   - `LLM_PROVIDER=none` (deterministic) or `ollama` for local AI features
3. Run:

```powershell
python -m job_agent run
```

OpenCode is the development agent only. The Python app is standalone-runnable
and does not depend on OpenCode's model, config, or MCP servers.

## Implemented

- Provider-neutral `JobSource` / normalized `Job` + `JobQuery` with registry
- `jobgpt` adapter (optional; can be removed by setting `JOB_SOURCE` elsewhere)
- Provider-neutral `LLMProvider` with `none` default and `ollama` optional
- Deterministic fit score 0–100 with tiers A/B/C/D
- SQLite memory under `data/job_agent.db` (gitignored)
- Approval packet preview only — no submit
- pytest suite covering scoring, memory, job-source, and LLM layers

## Not built yet

- Additional job source adapters (Greenhouse, Lever, RSS, …)
- Tailored resumes, JD summarization, cover-letter drafting (LLM features)
- Human APPROVE → submit wiring
- Outcome analytics