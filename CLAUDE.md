# CLAUDE.md

Quick start per Claude Code in questo repository. Per dettagli, vedi i link a `docs/`.

## Cos'è

Tool desktop **single-user** per outreach B2B di Luca Nesler & Stefano (speaker/formatori).
Stack: **Streamlit + SQLite + Claude Sonnet 4.6 + Folium**. Lingua interfaccia/codice: **italiano**.
Il tool **non invia email**: drafta, Stefano copia/invia da Aruba, poi reincolla risposte.

Stato: MVP completo (9/9 punti spec aprile 2026). Fase 2 = 0/4 (vedi [docs/legacy/SPEC_2026-04.md](docs/legacy/SPEC_2026-04.md)).

## Stack (versioni da [requirements.txt](requirements.txt))

- Python 3.12, venv locale in `venv/`
- `streamlit>=1.36` · `anthropic>=0.40` · `folium>=0.16` · `streamlit-folium>=0.20`
- `pandas>=2.0` · `requests>=2.31` · `python-dateutil>=2.8` · `cryptography>=42.0`
- Modello LLM: `claude-sonnet-4-6` ([lib/claude.py:16](lib/claude.py#L16))
- Web search tool: `web_search_20250305` (versione scelta per evitare bug `pause_turn`)

## Comandi

| Comando | Cosa fa |
|---|---|
| `./launch.sh` | Background daemon su porta 8501, log in `data/streamlit.log`, apre browser |
| `./run.sh` | Foreground (per dev / log live) |
| `./stop.sh` | `pkill -f 'streamlit run app.py'` |
| `python tests/test_importer.py` | Smoke test importer (no pytest, eseguibile come script) |
| `source venv/bin/activate && pip install -U -r requirements.txt` | Aggiornamento deps |

## Struttura cartelle

| Path | Cosa |
|---|---|
| [`app.py`](app.py) | Homepage Streamlit (KPI pipeline, deadline, follow-up dovuti) |
| [`pages/`](pages/) | Pagine Streamlit (numerate per ordine sidebar). `3_Outreach.py` è hidden, raggiunto via `session_state["draft_venue_id"]` |
| [`lib/`](lib/) | Layer dominio: `db`, `claude`, `prompts`, `importer`, `pipeline`, `geocode`, `settings`, `ui` |
| [`tests/`](tests/) | Smoke test (importer, organizers) |
| [`data/outreach.db`](data/) | SQLite (gitignored) |
| [`data/source/`](data/source/) | Markdown sorgente (`vanue 1.md`, `vanue 2.md`) per import iniziale |
| [`data/backups/`](data/) | Backup DB (gitignored) |
| `data/streamlit.log` | Log run background (gitignored) |
| `email_guidelines.md` | Linee guida email lette **fresh ad ogni call LLM** ([lib/prompts.py](lib/prompts.py)) |
| [`docs/`](docs/) | Documentazione (questo file, ARCHITECTURE, SCHEMA, OPERATIONS) |
| [`docs/legacy/SPEC_2026-04.md`](docs/legacy/SPEC_2026-04.md) | Spec iniziale archiviato (aprile 2026) |
| `~/.config/outreach/master.key` | Master key Fernet per cifrare API key (NON in repo) |
| [`.claude/settings.local.json`](.claude/settings.local.json) | Settings Claude Code (permissions allowlist) |

## Schema DB (sintesi)

13 tabelle, FK abilitate. **Dettagli completi in [docs/SCHEMA.md](docs/SCHEMA.md).**

```
organizers ─< venues >─ contacts (M:N venue_contacts, M:N organizer_contacts)
                │
                ├─< venue_tags >─ tags
                └─< interactions >─ contacts

speakers (singleton: Luca, Stefano)
project_profile (singleton id=1)
discovery_runs ─< discovery_candidates
settings (key/value: API key cifrata Fernet)
```

Stati pipeline (ridotti da 8 a 5 vs spec): `da_contattare, contattata, accettata, rifiutata, ghostati`.

## Convenzioni codice

- **Italiano** (commenti, variabili, UI, prompt)
- `from __future__ import annotations` in tutti i file Python
- Type hints PEP 484 completi
- `@dataclass` ok, **no pydantic**
- DB access: sempre via context manager [`db.transaction()`](lib/db.py#L181)
- Logging: `st.success/error/toast` (UI) + file `data/streamlit.log`. **No `logging` library**
- Errori: `try/except` espliciti con messaggi user-facing in `st.error`
- Niente test framework: `tests/test_*.py` sono script eseguibili

## Gotchas critici

1. **API key**: cifrata Fernet in tabella `settings`, master key in `~/.config/outreach/master.key` (perms 0o600). **NO `.env`, NO env var, NO `st.secrets`**. Vedi [lib/settings.py](lib/settings.py). Recovery se persa → vedi [docs/OPERATIONS.md](docs/OPERATIONS.md).

2. **Pipeline a 5 stati** (non 8 come spec): `da_contattare, contattata, accettata, rifiutata, ghostati`. Stati legacy mappati automaticamente in `init_db()` ([lib/db.py:247](lib/db.py#L247)) e `pipeline.normalize_state()`.

3. **Modello LLM e prompt caching**: `claude-sonnet-4-6`. System block ha `cache_control: ephemeral` sull'ultimo blocco (speakers) → cache hit entro ~5 min ([lib/claude.py:189-203](lib/claude.py#L189-L203)). Tutte le call usano `output_config.format=json_schema` per output strutturato.

4. **Discovery web search**: tool `web_search_20250305` (NON le versioni più nuove con `dynamic filtering` per evitare bug `container_id` su `pause_turn`). Loop `pause_turn` fino a 10 round, max 24k token per round, fino a 300 search totali. Vedi [lib/claude.py:534-614](lib/claude.py#L534-L614).

5. **`is_draft` flag su `interactions`**: i draft pending non confermati vanno **esclusi** da: count outgoing, history nel context LLM, last_outgoing per follow-up. Pattern: `COALESCE(is_draft, 0) = 0` in SQL, filtro `not (it["direction"] == "inviata" and it["is_draft"])` in Python. Senza questo l'LLM crede che la mail sia stata inviata.

6. **Streamlit pages**: ordine numerico nei filename pilota la sidebar. `3_Outreach.py` è "hidden" via CSS in [lib/ui.py](lib/ui.py) — si apre solo settando `st.session_state["draft_venue_id"]` da `1_Venue.py`.

7. **`.streamlit/` è ignorato da git** (incluso `config.toml` non solo `secrets.toml`). Se devi modificare config Streamlit, ricordati che non viene versionato.

8. **Linee guida email**: lette **fresh ad ogni call LLM** da `email_guidelines.md` (vedi `prompts.email_drafting_guidelines()`). Modificare il file ha effetto immediato senza restart.

9. **Timestamp SQLite**: gestiti come stringhe (`detect_types` disabilitato) perché Python 3.12 ha un converter `TIMESTAMP` strict che esplode su ISO-8601 con `T`. Parse esplicito con `datetime.fromisoformat()`. Vedi [lib/db.py:172-174](lib/db.py#L172-L174).

10. **`organizers` aggiunti post-spec**: rappresentano enti madre (Rotary, Lions, atenei) che ospitano più venue. Le venue figlie hanno `organizer_id` FK; il dossier LLM include "venue sorelle" via `_organizer_context_for_venue()` ([lib/claude.py:292](lib/claude.py#L292)).

11. **Marker `[Da discovery {run_id}]` nelle note venue**: lega le venue create al run di origine senza FK formale. Usato da `get_mails_for_discovery_run()` per export bulk. Non rimuovere il marker.

12. **Geocoding**: cache hardcoded `CITY_COORDS` (~120 città IT) in [lib/geocode.py](lib/geocode.py); fallback Nominatim (rate limit 1 req/s, può timeout). Per geocodificare in batch: `geocode.autocoord_all_venues()`.

## Dove cercare cosa

| Domanda | Doc |
|---|---|
| Schema DB completo, FK, migrazioni, stati pipeline | [docs/SCHEMA.md](docs/SCHEMA.md) |
| Mappa moduli, flussi end-to-end, decisioni di design | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Backup, restore, troubleshooting, master key | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Spec funzionale originale (archiviato) | [docs/legacy/SPEC_2026-04.md](docs/legacy/SPEC_2026-04.md) |
| Permissions Claude Code | [.claude/settings.local.json](.claude/settings.local.json) |

## Note operative per Claude

- **Prima di proporre modifiche allo schema**: leggere `docs/SCHEMA.md` per il contesto delle migrazioni legacy.
- **Prima di toccare il flusso draft email**: capire il pattern `is_draft` (gotcha #5) e i blocchi context in `_build_draft_context_blocks` ([lib/claude.py:256](lib/claude.py#L256)).
- **Per testare modifiche** che toccano DB: `python tests/test_importer.py` usa un DB temporaneo `/tmp/outreach_test.db`, non sporca il database reale.
- **Mai committare**: la master key, file in `data/backups/`, `data/outreach.db`, `data/streamlit.log`.
- **Quando modifichi pagine Streamlit**: il browser richiede un refresh manuale o un `st.rerun()` esplicito; il server fa hot-reload sui file Python ma session_state persiste.
