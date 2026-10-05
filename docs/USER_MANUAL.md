# job_agent — User Manual

A personal job-hunting intelligence layer. It discovers jobs from legitimate ATS
public APIs, analyzes them, scores them against **your** factual profile,
generates a tailored application packet (resume, cover letter, answers), and
opens an assistant that helps you fill the final application form — it **never
submits anything** for you.

This manual is the complete tutorial: setup, configuration, every command, the
full workflows, scoring model, storage, troubleshooting.

---

## 1. Feature overview

**Discovery & aggregation**
- Adapters for 7 job sources behind one interface: `jobgpt` (optional),
  `greenhouse`, `lever`, `ashby`, `remotive`, `jobicy` (no key needed), `adzuna`
  (free tier).
- Run one, several, or all sources into a single pool; a failing source is
  isolated while the others keep their results.
- `discover`: resolve *company names* into verified board candidates against
  each vendor's public API (Greenhouse/Lever/Ashby/SmartRecruiters), cache them
  in `data/boards.json`, then search all verified boards at once with per-board
  isolation and 24 h freshness caching.

**Pipeline & scoring**
- End-to-end pipeline: profile → query → sources → normalize → dedupe by stable
  ID across sources → SQLite persistence → work-mode gate → optional LLM
  enrichment → **deterministic fit score (0–100, tiers A/B/C/D)** → ranking.
- India-focused geography: jobs are grouped as India-compatible, global remote,
  unknown, or foreign; foreign and on-site jobs are hidden by default and never
  relabelled.
- Deterministic guardrails (hard blockers, salary, excluded companies/roles/
  keywords, work authorization) are authoritative and never overridden by LLM
  output.

**LLM features (local, optional, GPU-accelerated)**
- Provider-neutral `LLMProvider` layer: `none` (deterministic) or `ollama`
  (local). Everything keeps working when no LLM is available.
- `analyze`: strict, structured job-description extraction (facts only).
- `tailor`: job-specific resume draft with full provenance; strict validator
  rejects invented employers, titles, degrees, skills, metrics. Recovers from
  prose-wrapped or mildly-off JSON with one repair retry; drafts are versioned
  in SQLite.
- GPU priority on every request (`num_gpu=-1`); `OLLAMA_NUM_GPU` env for the
  server.

**Application workflow (human-in-the-loop, never auto-submits)**
- `apply-prep`: one stored job → full packet in `application/<job-id>/`
  (`resume.txt|md`, `cover_letter.txt`, `answers.json`, `job.json`). Resume is
  LLM drafted when safe, otherwise a deterministic profile-facts draft; unknown
  answers are left blank and flagged `requires_user_input`.
- `apply`: browser assistant (Playwright + installed Chrome) that fills only
  provenance-backed fields, selects your packet's resume/cover letter, and
  stops at a review screen. CAPTCHA/MFA/OTP = immediate manual stop. No submit
  action, no `--auto-submit`, no config that auto-submits.

## 2. Quick start (under 5 minutes)

```powershell
cd C:\Users\Admin\job_agent
# 1. Create your profile (facts only) and config
copy profile\profile.example.json profile\profile.json
copy .env.example .env
# 2. Edit profile\profile.json with your real facts
# 3. Edit .env: JOB_SOURCE + LLM_PROVIDER + OLLAMA_MODEL (see §5)
# 4. Run:
python -m job_agent search --include-foreign
```

## 3. Prerequisites

- **Python 3.11+** on `PATH`. The core app is pure standard library — zero pip
  dependencies are required to run everything except `apply`.
- For `apply`: `pip install -e ".[assist]"` (installs Playwright; it drives your
  installed Chrome, nothing else to download).
- Optional but recommended: **Ollama** with a local model for AI features, e.g.
  `llama3.1:8b-instruct-q4_K_M`. See §8 for GPU setup.
- Internet access to the selected ATS public APIs.

## 4. Project layout

```
job_agent/
├── .env                     # your config (never committed)
├── profile/profile.json     # your facts (never committed)
├── profile/profile.example.json
├── data/job_agent.db        # SQLite memory (auto-created, gitignored)
├── data/boards.json         # verified board catalog (gitignored)
├── application/<job-id>/    # generated application packets (gitignored)
├── job_agent/               # the package
│   ├── jobs/sources/        # the 7 source adapters
│   ├── discovery/           # board discovery layer
│   ├── llm/                 # LLM provider layer (none, ollama)
│   ├── analysis/            # strict job analysis
│   ├── resume/              # tailoring (prompt, parse, service)
│   ├── score.py             # deterministic fit model
│   ├── pipeline.py          # end-to-end pipeline
│   ├── search.py            # shortlist
│   ├── applyprep.py         # application packet
│   ├── applyassist.py       # browser assistant
│   ├── browser.py           # safe browser surface
│   └── memory.py            # SQLite storage
└── tests/                   # pytest suite
```

## 5. Configuration

### 5.1 `profile/profile.json` — your facts

Fill this with **facts only**. The stricter the facts, the better the scoring
and the safer the drafts. Key sections:

| Section | What it holds |
|---|---|
| `personal` | name, email, phone, headline, links (LinkedIn/GitHub/portfolio/other) |
| `locations` | current city, `willing_to_relocate`, preferred cities, `remote_ok` |
| `work_authorization` | countries you are authorized in, `requires_sponsorship` (true/false/null), notes |
| `notice_period_days` | e.g. `30` |
| `education` | degree, field, institution, start/end dates, location |
| `experience` | role (exact), company (exact), dates, summary, tools |
| `projects` | name, summary, technologies |
| `skills` | categories: `languages`, `frameworks`, `ml`, `tools`, `other` |
| `certifications` | exact titles |
| `achievements` | notable wins; entries containing the word "Publication" are the only allowed publications |
| `preferences` | `target_roles`, `excluded_roles`, `excluded_companies`, `excluded_keywords`, `employment_types`, `seniority`, `salary` (currency/min/target) |
| `application` | free-form notes |

Rules the tools rely on:
- Experience/project **names are structural identity** — they are copied into
  drafts verbatim; the model may never paraphrase or invent them.
- Only skills/technologies/certifications that appear **verbatim** in this file
  may enter a resume draft.

### 5.2 `.env` — runtime configuration

| Variable | Meaning | Default |
|---|---|---|
| `JOB_SOURCE` | single selected source: `jobgpt`, `greenhouse`, `lever`, `ashby`, `remotive`, `jobicy`, `adzuna` | `jobgpt` |
| `JOB_SOURCES` | comma-separated list to run several at once (wins over `JOB_SOURCE`) | — |
| `JOBGPT_API_KEY` / `JOBGPT_API_URL` | optional JobGPT credential | — / `https://6figr.com` |
| `GREENHOUSE_BOARD` | greenhouse board slug, e.g. `stripe`, `grafanalabs` | — |
| `LEVER_COMPANY` | lever company slug, e.g. `lever` | — |
| `ASHBY_BOARD` | Ashby board slug | — |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` / `ADZUNA_COUNTRY` | free-tier Adzuna credentials | country `in` |
| `LLM_PROVIDER` | `none` or `ollama` | `none` |
| `OLLAMA_BASE_URL` | where the Ollama daemon listens | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | model name — must match `ollama ls` output | `llama3.2` |
| `OLLAMA_TIMEOUT` | seconds per request | `180` |
| `OLLAMA_NUM_GPU` | layers to offload to GPU; `-1` = all layers (GPU priority) | `-1` |

Example:

```ini
JOB_SOURCE=greenhouse
GREENHOUSE_BOARD=stripe
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M
OLLAMA_TIMEOUT=600
```

## 6. Command reference

All commands run as `python -m job_agent <command> [flags]`.

### `run` — discover, score, record
Fetches from the selected source(s) and stores jobs + decisions in SQLite.

```powershell
python -m job_agent run
python -m job_agent run --source ashby
python -m job_agent run --sources greenhouse,lever,ashby,remotive,jobicy
python -m job_agent run --all-sources
python -m job_agent run --discover            # every verified board in the catalog
python -m job_agent run --limit 20            # capped at 50
```

Exit codes: `0` success · `1` every source failed · `2` nothing configured.

### `search` — the shortlist you actually use
Fits jobs from the discovery catalog + stored data against your profile and
prints a ranked shortlist.

```powershell
python -m job_agent search                # top 10 eligible
python -m job_agent search --limit 30
python -m job_agent search --include-foreign   # show foreign/on-site, labelled
python -m job_agent search --json              # machine-readable output
```

Output per job: rank, title, company, location, work mode, geography
eligibility, fit score, tier + verdict, relevance, one-line `Why`, apply URL,
source. Foreign jobs are hidden unless `--include-foreign`.

### `discover` — turn company names into verified boards
```powershell
python -m job_agent discover --company "Acme Inc" --company "Some Corp"
python -m job_agent discover --input companies.json   # list or {"companies":[...]}
python -m job_agent discover --ats greenhouse,lever,ashby,smartrecruiters
python -m job_agent discover --probe-limit 20
python -m job_agent discover --list                   # show the catalog
python -m job_agent discover --fresh                  # bypass 24h cache
```

### `analyze` — strict structured analysis of a posting
```powershell
python -m job_agent analyze --text "Backend engineer, Python and SQL, 5+ years, remote"
Get-Content job.txt | python -m job_agent analyze
python -m job_agent analyze --text "..." --score   # also run the deterministic scorer
```

### `tailor` — job-specific resume draft (draft only)
```powershell
python -m job_agent tailor --job-id <stored-job-id>
python -m job_agent tailor --best                 # best eligible A/B remote/hybrid job
python -m job_agent tailor --best --max-tokens 1600 --llm ollama
```

Requires the job to be **tier A or B and remote/hybrid**. Prints the JSON draft
and stores a new version. Strict provenance validation: invented anything =
rejected (with one automatic repair retry).

### `apply-prep` — build the application packet
```powershell
python -m job_agent apply-prep --job-id <stored-job-id>
python -m job_agent apply-prep --job-id <id> --resume-format md --out application
```

Writes `application/<job-id>/` with `resume.txt|md`, `cover_letter.txt`,
`answers.json`, `job.json`. Exit: `0` full packet · `1` resume not produced ·
`2` unknown job.

### `apply` — human-in-the-loop assistant (never submits)
```powershell
# needs the packet first:
python -m job_agent apply-prep --job-id <stored-job-id>
python -m job_agent apply --job-id <stored-job-id>
python -m job_agent apply --job-id <id> --headless   # automated validation
```

Opens the apply URL in Chrome, fills only provenance-backed fields, attaches the
packet resume/cover letter where the site allows, prints a review screen, and
stops before any submit. CAPTCHA/MFA/OTP stops automation immediately. If no
browser can run, it prints the URL, packet path, and manual instructions.

## 7. End-to-end workflows

### Workflow A — be aware of new fitting jobs (daily, 30 s)
```powershell
python -m job_agent run --source greenhouse
python -m job_agent search
```
`run` refreshes storage; `search` prints the shortlist. A tier-A/B remote/hybrid
job is your "apply" target.

### Workflow B — apply to one job end-to-end
1. Identify the job id from `search` output (or your source, e.g.
   `greenhouse:stripe:6123456789`).
2. Build the packet:
   ```powershell
   python -m job_agent apply-prep --job-id greenhouse:stripe:6123456789
   ```
3. Review `application/greenhouse-stripe-6123456789/` — fill the fields printed
   under "Fields requiring your input".
4. Open the assistant:
   ```powershell
   python -m job_agent apply --job-id greenhouse:stripe:6123456789
   ```
5. Finish the form and **submit by hand**. The assistant cannot submit.

### Workflow C — discover new companies then search everything
```powershell
python -m job_agent discover --company "Acme" --company "Some Corp"
python -m job_agent run --discover
python -m job_agent search --limit 25
```

### Workflow D — understand a posting before deciding
```powershell
Get-Content posting.txt | python -m job_agent analyze --score
```

## 8. Local AI + GPU setup (Ollama)

1. Install **Ollama** (official installer; includes CUDA and Vulkan compute
   backends for Windows).
2. Pull a model: `ollama pull llama3.1:8b-instruct-q4_K_M`.
3. `.env`: `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=llama3.1:8b-instruct-q4_K_M`.
4. The app already sends `num_gpu=-1` on every request. To make the **server**
   always prefer the GPU, set in your user/system environment **before**
   starting `ollama serve`:
   ```
   OLLAMA_NUM_GPU=-1
   OLLAMA_INTEL_GPU=1      # only if you use an Intel XPU/iGPU
   ```
5. Verify with: `ollama ps` — the model row's `PROCESSOR` column should show
   `GPU` (not `100% CPU`).

If GPU usage is `0%`, your Ollama install is almost certainly CPU-only/broken:
the official installer contains `ggml-cuda.dll` under
`lib\ollama\cuda_v12\`; if it's missing, reinstall the current official Ollama.

## 9. How scoring works (deterministic)

- Inputs: profile facts + normalized posting (+ optional LLM enrichment, which
  is advisory only).
- **Geography** — India-compatible → global remote → unknown → foreign. The
  shortlist never presents foreign/on-site as India-eligible.
- **Work modes** — remote / hybrid (primary), on-site, unknown. Only A/B tier
  remote/hybrid jobs are eligible for tailored resumes.
- **Fit score 0–100 with tiers**:
  - `A` strong match, no blockers
  - `B` eligible, apply
  - `C` weaker match
  - `D` blocked — hard blockers or explicit exclusions
- **Hard blockers** (never overridden): compensation below your minimum,
  authorization conflicts, excluded companies, excluded role/keyword matches,
  seniority/experience gaps.
- `tailor --best` picks the highest-priority A/B-tier remote/hybrid job.

## 10. Storage & data

| Path | Contents |
|---|---|
| `data/job_agent.db` | SQLite: `jobs`, `scores`, `applications`, `application_events`, `interviews`, `recruiters`, `referrals`, `resume_versions`, `resume_drafts` (versioned) |
| `data/boards.json` | verified board catalog (24 h freshness) |
| `application/<job-id>/` | per-job packet: resume, cover letter, answers, job.json |
| `profile/profile.json` | your facts |
| `.env` | configuration |

Nothing is stored outside `profile/`, `data/`, and `application/` unless you set
custom `--db-path` / `--out`.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `No LLM provider is configured or available.` | `.env` has `LLM_PROVIDER=ollama`; Ollama daemon is running (`ollama ps`); `OLLAMA_MODEL` matches `ollama ls` |
| `model output is not valid JSON...` (in `tailor`) | The model was sloppy; one repair retry already happened. Re-run, or use `apply-prep` which falls back to a deterministic profile-facts resume |
| `Resume draft rejected: ... does not belong to experience[i].*` | Provenance violation — the tool refuses invented facts. `apply-prep` still produces a safe packet via the deterministic fallback |
| `ollama ps` shows `100% CPU` / GPU 0% | Reinstall official Ollama; set `OLLAMA_NUM_GPU=-1`; restart the server (`ollama ps` to verify) |
| `No jobs matched your search this time.` | Boards carried nothing in your target roles/regions; try `--include-foreign`, more sources, or `discover` more companies |
| `No stored job with id '...'` | Job came from a source that isn't selected; run with that source (`run --source ...`) so it's persisted |
| `No application packet found ...` | Run `apply-prep --job-id <id>` first |
| `apply` falls back to manual instructions | Browser couldn't start (Chrome / Playwright). Follow the printed URL + packet; run `pip install -e ".[assist]"` |
| `python` resolves to the wrong version | Ensure the environment variable `PATH` points at the Python with your tools (e.g. use `py -3.13`); tests need `pip install pytest` |

## 12. Safety and responsible use

- **Nothing submits an application.** No path, flag, or config auto-submits.
- **No fabrication.** Drafts are provenance-validated; invented facts are
  rejected before storage.
- **No bypass.** CAPTCHA, MFA, OTP, bot checks cause a manual stop.
- Sources are public APIs used for personal tooling. Nothing scrapes.
  Remotive requires crediting it as the source when you surface its listings.
- Profile, database, and packets are private to your machine and are
  gitignored.

## 13. Status

Implemented: 7 source adapters, discovery catalog, full pipeline, deterministic
A–D scoring, structured analysis, provenance-backed tailoring (with repair
retry and deterministic fallback), application packets, and the human-in-the-loop
assistant. 472 tests pass.

Not built: automated submission (never planned), PDF/DOCX resume export, extra
source adapters, outcome analytics.