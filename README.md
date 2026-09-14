# job_agent

Personal intelligence layer around [JobGPT](https://github.com/6figr-com/jobgpt-mcp-server). JobGPT finds and (later) applies. This repo decides **whether this person should spend an application**.

Auto-apply is **disabled**. `python -m job_agent run` only searches, scores, and stores results.

## Setup

```powershell
cd C:\Users\Admin\job_agent
copy .env.example .env
copy profile\profile.example.json profile\profile.json
```

1. Fill `profile/profile.json` with **facts only**. Do not invent jobs, metrics, skills, or dates.
2. Create a [6figr](https://6figr.com/account) account → MCP Integrations → API key.
3. Put the key in `.env` as `JOBGPT_API_KEY`. Never commit `.env`.
4. Run:

```powershell
python -m job_agent run
```

Optional Cursor MCP (key stays in your user config, not this repo):

```json
{
  "mcpServers": {
    "jobgpt": {
      "type": "http",
      "url": "https://mcp.6figr.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_KEY"
      }
    }
  }
}
```

JobGPT credits: new accounts get a small free auto-apply allowance. Search may work without buying credits; auto-apply and their resume AI consume credits. We will not call those until you explicitly enable apply.

## What exists now

- Local profile template
- JobGPT REST client (`search_jobs`, `get_credits`, `get_job`)
- Deterministic fit score 0–100 and tiers A/B/C/D
- SQLite memory under `data/job_agent.db` (gitignored)
- Approval packet **preview** only — no submit

## Not built yet

- Tailored resumes
- Human APPROVE → `apply_to_job`
- Outcome analytics
- Local LLM / GPU
