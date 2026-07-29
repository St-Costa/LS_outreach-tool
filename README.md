<p align="center">
  <img src="assets/logo.svg" alt="L&S Outreach Tool" width="360">
</p>

<h1 align="center">Outreach Intelligence Tool</h1>

<p align="center">
  <em>An LLM-native CRM for high-touch, low-volume B2B outreach.</em><br>
  Built for two professional speakers who need every cold email to read like a human wrote it.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Streamlit" src="https://img.shields.io/badge/Streamlit-1.36+-FF4B4B?logo=streamlit&logoColor=white">
  <img alt="Claude" src="https://img.shields.io/badge/Claude-Sonnet%204.6-D97757?logo=anthropic&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-single%20file-003B57?logo=sqlite&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/status-in%20production-2ea44f">
</p>

---

## Why I built this

I do B2B outreach with Luca Nesler: we pitch ourselves as speakers and trainers to service clubs, trade fairs, innovation hubs, universities and local business
networks across Italy. The shape of that problem does not fit any CRM on the market:

| | Typical sales CRM | This use case |
|---|---|---|
| **Volume** | tens of thousands of leads | a few hundred venues |
| **Personalization** | templates + merge tags | every email must prove we understood *that* specific club |
| **Cycle length** | days to weeks | weeks to months, with planned follow-ups |
| **Channels** | email + maybe LinkedIn | email, Instagram DM, LinkedIn DM, phone, in person |
| **What compounds** | lead volume | *institutional memory* — what worked at the last Rotary club |

HubSpot, Pipedrive and Lemlist are built for SaaS sales motions with industrial scripting. For two
people selling their own voice at 3–4 events a month, they are the wrong machine at the wrong scale —
and their tone is wrong in a way that burns the relationship on the first send.

So the interesting problem wasn't "build a CRM." It was: **how do you get an LLM to write in Italian
without sounding like an LLM?** Generic AI-slop email is worse than no email at all — it costs you the
prospect permanently. That constraint drove nearly every design decision below.

## What it does

A local desktop app that acts as a **copilot, not an autopilot**. It never sends anything.

1. **Discovers** new venues via Claude with live web search, scoped by region and filtered against a
   project profile — returning structured candidates with contact, channel and fit score.
2. **Assembles a dossier** before every email: project profile, speaker bios, the venue's full history,
   the parent organization and its sibling venues, and *what was written to similar venues before*.
3. **Drafts** the email against a 750-line style guide, then accepts natural-language refinement
   ("more informal", "drop the reference to X", "cut it 30%").
4. **Hands off to the human.** You copy the draft into Aruba webmail / LinkedIn / Instagram and send it
   yourself. You then paste back what you actually sent — which may differ from the draft, and both are kept.
5. **Analyzes replies** you paste in — sentiment, meeting signals, rejection signals — and advances the
   pipeline state automatically.
6. **Times follow-ups**, including telling you *not* to follow up yet.

## Screenshots

> These are real working sessions, not mockups. Prospect names are redacted where they appear.

<table>
<tr>
<td width="50%"><img src="assets/screenshots/home.png" alt="Dashboard"><br><sub><b>Dashboard</b> — pipeline KPIs at a glance, plus a banner surfacing overdue follow-ups ranked by staleness.</sub></td>
<td width="50%"><img src="assets/screenshots/venue.png" alt="Venue kanban"><br><sub><b>Venues</b> — kanban by pipeline state, with per-venue fit score and one-click entry into the conversation.</sub></td>
</tr>
<tr>
<td><img src="assets/screenshots/discovery.png" alt="Discovery run log"><br><sub><b>Discovery</b> — a completed run, replayed. Every web search the model issued is logged to the DB, so a run that took 11 minutes stays auditable afterwards.</sub></td>
<td><img src="assets/screenshots/mappa.jpg" alt="Map"><br><sub><b>Map</b> — venues geocoded and colored by pipeline state, filterable by type, state and angle.</sub></td>
</tr>
<tr>
<td><img src="assets/screenshots/costi.png" alt="LLM cost tracking"><br><sub><b>LLM economics</b> — token and spend accounting per task and model, showing where Sonnet is worth it and where Haiku is enough. Prompt caching holds the cache-hit rate at 63%.</sub></td>
<td><img src="assets/screenshots/statistiche.png" alt="Statistics"><br><sub><b>Statistics</b> — aggregate pipeline and interaction metrics, broken down per speaker.</sub></td>
</tr>
</table>

## Design decisions worth defending

**The intelligence lives in the prompts, not in a model.** No fine-tuning, no vector DB, no proprietary
ML pipeline. The leverage comes entirely from the dossier assembled before each call — in particular the
*cross-venue* history. If I've already written to three Rotary clubs in the region, the model gets to see
what landed and what didn't. The system gets better the more it's used, without any training loop.

**Anti-AI-slop is treated as an engineering problem.** `email_guidelines.md` is the most carefully
maintained file in the repo: a ~40-word literal blacklist, a "Rule Zero" forbidding the model from
inventing, inferring or "making more specific" any fact not literally present in the profiles, and
explicit bans on self-congratulatory and fake-adoption-frequency patterns. It's read *fresh on every
call*, so tuning the prompt never requires a restart or a deploy.

**A draft is not a sent email.** Every outbound interaction carries an `is_draft` flag. Drafts are
excluded from outbound counts, from the LLM's context, and from follow-up timing. Without it the system
generated follow-ups saying "further to my email last week" about emails that only ever existed locally —
a small flag that prevents a genuinely embarrassing failure mode.

**No email integration, deliberately.** The manual copy/paste is a five-second friction that buys total
channel flexibility and, more importantly, guarantees a human reads every message before a prospect does.
The expensive error here is sending one bad email, and human friction prevents it better than any classifier.

**Local-first, single-user, single file.** SQLite, no auth, no cloud, no multi-tenancy, no daemon.
Backup is `cp`. The expected ceiling is <1k venues and <10k interactions; the architecture is sized
honestly for that rather than for an imagined scale.

**The API key is encrypted at rest.** Fernet-encrypted in the DB, master key in `~/.config/` at `0600` —
not `.env`, not an env var, not `st.secrets`, so it can't leak through a shared DB dump or a synced folder.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  app.py + pages/     Streamlit UI (11 pages)         │
└───────────────────────┬──────────────────────────────┘
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   ┌────────────┐ ┌───────────┐ ┌────────────┐
   │lib/claude  │ │  lib/db   │ │lib/geocode │
   │ LLM calls  │ │  SQLite   │ │ Nominatim  │
   └─────┬──────┘ └─────┬─────┘ └─────┬──────┘
         ▼              ▼             ▼
  Anthropic API   outreach.db    ~120-city cache
  Sonnet 4.6      17 tables      + OSM fallback
  + web search    FK enforced
         │
         ▼
  lib/prompts  ·  project profile  ·  speaker bios
  ·  email_guidelines.md (read fresh per call)
```

**Stack:** Python 3.12 · Streamlit · SQLite · Anthropic API (`claude-sonnet-4-6` for drafting and
discovery, `claude-haiku-4-5` for short classification tasks) · Folium · Fernet.
All LLM calls use structured output via `output_config.format=json_schema`; system blocks carry two
`cache_control` breakpoints for prompt caching.

**Scale:** ~11k lines of Python, 17 SQLite tables, 11 Streamlit pages, 10 domain modules.
Average cost of a drafted first email: **$0.02–0.05**.

> **A note on language:** the UI, the code comments, the variable names and the prompts are all in
> Italian. That's deliberate — the product writes Italian business correspondence, and an
> internationalization layer would have been pure overhead for a single-user tool.

## Running it

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -U -r requirements.txt

./launch.sh      # background daemon on :8501, opens the browser
./run.sh         # foreground (dev / live logs)
./stop.sh        # stop the daemon
```

Set the Anthropic API key from the **Impostazioni** page in the app — it's encrypted with Fernet, with
the master key stored at `~/.config/outreach/master.key`.

```bash
python tests/test_importer.py     # smoke tests are plain executable scripts,
python tests/test_organizers.py   # each against a temporary DB — no pytest,
python tests/test_pipeline.py     # no fixtures, no framework
python tests/test_settings.py
```

## Documentation

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Working reference: stack, conventions, and the critical gotchas |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, end-to-end flows, design rationale |
| [docs/SCHEMA.md](docs/SCHEMA.md) | Full DB schema, foreign keys, migrations, pipeline states |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Backup, restore, troubleshooting, master key recovery |
| [descrizione_progetto.md](descrizione_progetto.md) | Long-form project description (Italian) |

---

<p align="center"><sub>Built by <a href="https://github.com/St-Costa">Stefano Costa</a> · a working tool, not a demo</sub></p>
