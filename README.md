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
  Current adapters: `jobgpt` (optional), `greenhouse` (public job-board API),
  `lever` (public postings API).
  Future: RSS, other legitimate sources.
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
   - `JOB_SOURCE=jobgpt`, `greenhouse`, or `lever`
   - `JOBGPT_API_KEY=...` if using JobGPT (generate at
     https://6figr.com/account → MCP Integrations; never commit it)
   - `GREENHOUSE_BOARD=<board token>` to search Greenhouse's public jobs API
     (token = the slug on `boards.greenhouse.io/<token>`; e.g. `stripe`)
   - `LEVER_COMPANY=<company slug>` to search Lever's public postings API
     (slug on `jobs.lever.co/<company>`; no auth required, e.g. `lever`)
   - `JOB_SOURCES=greenhouse,lever` (optional) to run several sources in one
     pipeline; without it, `JOB_SOURCE` selects a single default source
   - `LLM_PROVIDER=none` (deterministic) or `ollama` for local AI features
3. Run:

```powershell
python -m job_agent run
# or select the source on the command line:
python -m job_agent run --source greenhouse
python -m job_agent run --source lever
# or run several sources into ONE unified pool:
python -m job_agent run --sources greenhouse,lever
python -m job_agent run --all-sources
```

Optional LLM feature — structured job analysis via the configured provider:

```powershell
python -m job_agent analyze --text "Backend engineer, Python and SQL, 5+ years, remote"
# or:  Get-Content job.txt | python -m job_agent analyze
# add --score to also run the deterministic scorer on the analysis-enriched job
```

When `LLM_PROVIDER=ollama`, analysis runs against a local Ollama model during
either `analyze` or `run`. When no LLM is available the app keeps working
deterministically — analysis never falls back silently to another provider, and
the LLM may only extract facts explicitly stated in the posting.

Job-specific resume tailoring (draft only, no files, no auto-submit):

```powershell
python -m job_agent tailor --job-id <stored-job-id>
# or pick the best eligible (A/B tier, remote/hybrid) stored job:
python -m job_agent tailor --best
# optional: --llm provider-name --max-tokens N --db-path path --profile-path path
```

Tailoring produces a structured JSON draft with full provenance. Every claim
(highlight, project summary, achievement, publication) points back to the
profile fact it was derived from; the strict validator rejects invented
employers, titles, degrees, skills, certifications, publications, and metrics
before anything is stored. Dates and structural identity fields (roles,
companies, degrees, institution, project names) are always taken from the
profile, never from the model. Drafts are versioned in SQLite
(`resume_drafts`): each run for a job creates the next version.

During `run`, optional LLM analysis enriches each discovered job (skills,
salary, location, work mode) and that enrichment feeds the deterministic
scorer. Deterministic guardrails stay authoritative: hard blockers, salary
constraints, excluded companies/keywords, authorization requirements, and
skill matching are never silently overridden by LLM output. If the LLM is
unconfigured or fails, discovery, storage, scoring, and ranking all still work.

OpenCode is the development agent only. The Python app is standalone-runnable
and does not depend on OpenCode's model, config, or MCP servers.

## Implemented

- Provider-neutral `JobSource` / normalized `Job` + `JobQuery` with registry
- `jobgpt` adapter (optional), `greenhouse` adapter (public job-board API,
  no auth required), and `lever` adapter (public postings API, no auth)
- Multi-source aggregation: run one, several, or all registered sources into a
  single pool; a failing source is isolated and reported while the others keep
  their results
- End-to-end discovery pipeline: profile → query → one or more sources →
  normalize → dedup by stable ID across sources (source namespace is part of
  the identity; posts are merged only on identical IDs or identical canonical
  URLs) → SQLite persistence (first-seen preserved, stored data merged
  on re-fetch) → work-mode gate → optional LLM enrichment → deterministic
  scoring → fit ranking
- Provider-neutral `LLMProvider` with `none` default and `ollama` optional
- Structured job-description analysis (`analyze`) with a strict schema — only
  explicitly-stated facts; deterministic scoring can consume the enriched job
- Job-specific resume tailoring (`tailor`) with a strict, provenance-backed
  draft schema — only profile facts may enter a draft, gaps are computed
  deterministically, versions are stored in SQLite, and drafts are only
  produced for A/B tier remote/hybrid jobs
- Deterministic fit score 0–100 with tiers A/B/C/D
- SQLite memory under `data/job_agent.db` (gitignored)
- Approval packet preview only — no submit
- pytest suite covering scoring, memory, job-source, LLM, and analysis layers

## Not built yet

- Additional job source adapters (RSS, …)
- Cover letters, other drafting formats, PDF/DOCX export of tailored resumes
- Human APPROVE → submit wiring
- Outcome analytics