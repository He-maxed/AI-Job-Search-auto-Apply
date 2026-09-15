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
  `lever` (public postings API), `ashby` (public posting API), `remotive` and
  `jobicy` (documented remote-only feeds, no key), `adzuna` (optional, free tier
  needs an account). Future: more legitimate sources.
- **Board discovery** — `job_agent/discovery`. Provider-agnostic layer that
  resolves company names into *verified board candidates* against each vendor's
  documented public API (Greenhouse/Lever/Ashby/SmartRecruiters), caches them in
  a gitignored catalog (`data/boards.json`), and hands board-scoped `JobSource`
  instances to the pipeline. No company list is hard-coded into the core.
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
   - `JOB_SOURCE=jobgpt`, `greenhouse`, `lever`, `ashby`, `remotive`, `jobicy`,
     or `adzuna`
   - `JOBGPT_API_KEY=...` if using JobGPT (generate at
     https://6figr.com/account → MCP Integrations; never commit it)
   - `GREENHOUSE_BOARD=<board token>` to search Greenhouse's public jobs API
     (token = the slug on `boards.greenhouse.io/<token>`; e.g. `stripe`)
   - `LEVER_COMPANY=<company slug>` to search Lever's public postings API
     (slug on `jobs.lever.co/<company>`; no auth required, e.g. `lever`)
   - `ASHBY_BOARD=<board>` to search Ashby's public posting API
     (slug on `jobs.ashbyhq.com/<board>`; no auth required)
   - `remotive` / `jobicy` need no credential (remote-only feeds). Adzuna needs
     `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` (free tier) and defaults to country `in`
   - `JOB_SOURCES=greenhouse,lever,ashby` (optional) to run several sources in
     one pipeline; without it, `JOB_SOURCE` selects a single default source
   - `LLM_PROVIDER=none` (deterministic) or `ollama` for local AI features
3. Run:

```powershell
python -m job_agent run
# or select the source on the command line:
python -m job_agent run --source greenhouse
python -m job_agent run --source ashby
# or run several sources into ONE unified pool:
python -m job_agent run --sources greenhouse,lever,ashby,remotive,jobicy
python -m job_agent run --all-sources
```

### Job search (shortlist)

The one command to actually use the product. Searches every verified board in
the discovery catalog, fits each job against the profile with the existing
pipeline (discover → normalize → dedupe → work mode → geography → relevance →
optional LLM enrichment → deterministic score → rank), and prints a shortlist:

```powershell
python -m job_agent search
python -m job_agent search --limit 10     # default is 10
python -m job_agent search --include-foreign   # also show foreign postings (clearly labelled)
python -m job_agent search --json         # machine-readable JSON instead of the table
```

Ordering: India-compatible remote → India-compatible hybrid → explicit global
remote → unknown geography last. Foreign and on-site jobs are hidden by default
(they are never presented as India-compatible); pass `--include-foreign` to see
foreign postings with an explicit "not India-eligible" label. Work without an
LLM; Ollama only enriches candidates in eligible regions and a failure never
aborts the search.

### Board discovery (many companies from a few names)

`run` searches boards you configure. To discover *which* companies even have a
public Greenhouse/Lever/Ashby/SmartRecruiters board, resolve company names
against each vendor's documented public API:

```powershell
python -m job_agent discover --company "Acme Inc" --company "Some Corp"
python -m job_agent discover --input companies.json    # JSON list or {"companies": [...]}
python -m job_agent discover --list                    # show what is already verified
```

Verified candidates are stored in the gitignored catalog `data/boards.json`
(sequential, rate-limited, capped with `--probe-limit`; a 429 throttles that
ATS; verified boards are reused for 24h without re-probing). Then search all of
them at once, each board isolated and its failure contained:

```powershell
python -m job_agent run --discover
# optionally combined with one explicitly configured source:
python -m job_agent run --discover --sources remotive,jobicy
```

Sources are used only for personal tooling. Remotive requires linking back and
crediting it as the source whenever listings are surfaced; the feeds may lag
live postings by about a day. Nothing scrapes, and nothing is auto-submitted.

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

Application packet for one stored job (job → tailored application packet):

```powershell
python -m job_agent apply-prep --job-id <stored-job-id>
# optional: --out <dir> --resume-format {txt|md} --llm provider --max-tokens N
#           --db-path path --profile-path path
```

`apply-prep` writes a job-specific packet under `application/<job-id>/`:
`resume.<fmt>`, `cover_letter.txt`, `answers.json`, and `job.json`. The resume
is drafted per job: the LLM is tried first, and if no safe, provenance-valid
draft comes back (missing/unavailable provider, malformed JSON, or an invented
claim), a deterministic fallback draft assembled strictly from profile facts is
used instead — never invented content. Cover letter and application answers are
generated deterministically (no LLM required) from profile + job + scoring
evidence; uncertain fields (salary expectations, notice period, work
authorization, expected start date, legal/demographic declarations) are left
blank and flagged `requires_user_input`, never guessed. Exit code is 0 on a
full packet (resume included), 1 if resume could not be produced, 2 for an
unknown job id. Nothing is submitted — the packet is a reviewable draft.

OpenCode is the development agent only. The Python app is standalone-runnable
and does not depend on OpenCode's model, config, or MCP servers.

## Implemented

- Provider-neutral `JobSource` / normalized `Job` + `JobQuery` with registry
- `jobgpt` adapter (optional), `greenhouse` adapter (public job-board API, no
  auth), `lever` adapter (public postings API, no auth), `ashby` adapter
  (public posting API, no auth, structured remote/hybrid/on-site work modes),
  `remotive` and `jobicy` adapters (documented remote-only feeds, no key), and
  `adzuna` adapter (optional; free tier needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`;
  affiliate redirect apply URLs)
- Multi-source aggregation: run one, several, or all registered sources into a
  single pool; a failing source is isolated and reported while the others keep
  their results
- Board discovery (`discover`): provider-agnostic layer that resolves company
  names into verified board candidates against each vendor's documented public
  API, then searches all verified boards at once (`run --discover`) with
  per-board failure isolation and 24h freshness caching
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
- `apply-prep`: one command turns a stored job into a reviewable application
  packet — job-specific resume (LLM draft or deterministic profile-facts
  fallback, both provenance-valid and versioned in SQLite), a deterministic
  cover letter, safe answers.json (provenance + `requires_user_input` flags),
  and job.json with the scoring evidence — no auto-submission
- Deterministic fit score 0–100 with tiers A/B/C/D
- `search` command: one command turns the discovery catalog into a ranked,
  human-readable shortlist (rank, title, company, location, work mode,
  geography/eligibility, fit, tier, relevance, short why, apply URL, source);
  foreign and on-site jobs are hidden by default, never relabelled, and a
  missing apply URL is shown as "unavailable" instead of being invented
- SQLite memory under `data/job_agent.db` (gitignored)
- Approval packet preview only — no submit
- pytest suite covering scoring, memory, job-source, LLM, and analysis layers

## Not built yet

- Additional job source adapters (an RSS/other-legitimate list, Recruitee,
  SmartRecruiters adapter for discovered boards, …)
- PDF/DOCX export of tailored resumes; richer cover-letter formats
- Human APPROVE → submit wiring
- Outcome analytics