# Deploy

Target: a public URL your professor can open, that works on first click.

The repo is already committed locally. Neither `gh` nor `docker` is installed
on this machine, so these three steps are yours to run.

---

## 1. Push to GitHub

Create an empty repo (no README, no .gitignore — this repo has both), then:

```bash
cd "E:\IIMU\Agentic AI\ticket-agent"
git remote add origin https://github.com/<you>/meridian-triage-agent.git
git branch -M main
git push -u origin main
```

Public or private both work; Render can read a private repo once you
authorise it.

## 2. Deploy on Render

1. render.com → **New** → **Web Service** → connect the repo.
2. Render reads `render.yaml` and fills everything in: Docker runtime, health
   check at `/healthz`, free plan, a 1 GB disk mounted at `/app/data`.
3. **Leave `ANTHROPIC_API_KEY` unset for now.** The service starts in demo
   mode and works immediately. Add the key later if you want the live model.
4. Deploy. First build takes roughly 3–5 minutes.

The database is built into the image, so the corpus is there on first load —
no seeding step, no key, no empty screen.

### If you add the API key

Set it in Render → Environment. The banner disappears and the real model
runs. Two things to know:

- Every triage call costs money. The free plan has no spend cap; set one on
  the Anthropic console, not here.
- Free-plan services sleep after 15 minutes idle and take ~30 seconds to wake.
  For a live demo, open the link a minute before you present.

## 3. Check it before you submit

```bash
curl https://<your-service>.onrender.com/healthz
```

Should return `{"status":"ok","tickets":1}`. Then open the URL and walk the
60 seconds in the README: start the agent, open a held row, try to approve it
without a reason, approve a clean one instead.

Put the live URL at the top of the README before you submit.

---

## Alternatives, if Render gives trouble

**Railway** — same Dockerfile. New Project → Deploy from GitHub repo. Set
`PORT` if it does not detect it. Add a volume at `/app/data`.

**Fly.io** — `fly launch` reads the Dockerfile; accept the defaults and say
yes to a volume at `/app/data`.

**Local fallback** — if nothing deploys in time, record the walkthrough
instead of skipping it:

```bash
uvicorn src.ticket_agent.web.app:app --host 0.0.0.0 --port 8000
```

A recorded walkthrough with the HOLD stamp visible beats a dead link. Say in
the submission that it runs locally and why.

---

## What persists, and what does not

Decisions (approvals, edits, rejections) are written to SQLite on the mounted
disk and survive restarts. **Without a disk they do not** — a redeploy resets
to the seeded corpus. `render.yaml` declares the disk; if you deploy
somewhere without one, say so rather than implying the data persists.
