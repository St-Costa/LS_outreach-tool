# Architettura

## Filosofia

Tool **locale, single-user, single-machine**. Niente cloud, niente auth, niente multi-tenant: l'unico utente è Stefano sul suo desktop Ubuntu.

Il tool **non invia email**: produce draft strutturati che Stefano copia/incolla nel client di posta (Aruba) o nel canale social, e poi reincolla la versione effettivamente inviata + le risposte ricevute. Il sistema è quindi un **CRM intelligente offline**, non un mailer.

L'intelligenza vive nei prompt: dossier ricchi (storico cross-venue, ente madre, venue sorelle, similar history) vengono iniettati ad ogni chiamata LLM perché Sonnet decida tono, angolo, speaker, canale.

---

## Diagramma livelli

```
┌─────────────────────────────────────────────────────┐
│  app.py + pages/  (Streamlit UI, italiano)          │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ lib/claude.py│ │  lib/db.py   │ │lib/geocode.py│
│  (LLM calls) │ │  (SQLite)    │ │  (Nominatim) │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
  Anthropic API   data/outreach.db  cache hardcoded
  (Sonnet 4.6)    + WAL/journal      + Nominatim API
  + web_search    
       │
       ▼
  prompts.py + project_profile + speakers
  (cache_control on speakers block)
```

Altri moduli secondari:
- [lib/importer.py](../lib/importer.py) → parsing markdown sorgente in `data/source/`
- [lib/pipeline.py](../lib/pipeline.py) → costanti/derivazioni stati e canali (senza I/O)
- [lib/settings.py](../lib/settings.py) → encryption Fernet API key
- [lib/prompts.py](../lib/prompts.py) → template prompt LLM
- [lib/ui.py](../lib/ui.py) → CSS globale Streamlit

---

## Mappa moduli `lib/`

| Modulo | Ruolo | Funzioni pubbliche principali |
|---|---|---|
| [`db.py`](../lib/db.py) | Layer SQLite: schema, migrazioni, CRUD | `init_db`, `transaction`, `insert_venue`, `list_venues`, `find_similar_venues_extended`, `insert_interaction`, `get_interactions_for_venue`, `start_discovery_run` |
| [`claude.py`](../lib/claude.py) | Wrapper Anthropic SDK + prompt assembly | `draft_first_email`, `refine_first_email`, `draft_follow_up`, `analyze_response`, `discover_venues`, `enrich_venue`, `suggest_channel`, `test_connection` |
| [`prompts.py`](../lib/prompts.py) | Template prompt + dossier blocks | `SYSTEM_PROMPT`, `email_drafting_guidelines` (legge fresh da `email_guidelines.md`), `project_profile_block`, `speakers_block`, `venue_block`, `contact_block`, `*_history_block` |
| [`importer.py`](../lib/importer.py) | Parser markdown venue iniziali | `find_default_files`, `import_files`, `_split_sections`, `_detect_*` |
| [`pipeline.py`](../lib/pipeline.py) | Costanti + derivazioni stati/canali (no I/O) | `PIPELINE_STATES`, `CHANNELS`, `INTERACTION_TYPES`, `ANGLES`, `derive_interaction_type`, `derive_effective_state`, `normalize_state` |
| [`geocode.py`](../lib/geocode.py) | Geocodifica Nominatim + cache città | `forward_geocode`, `autocoord_all_venues`, `CITY_COORDS` |
| [`settings.py`](../lib/settings.py) | Encryption Fernet API key | `save_api_key`, `get_api_key`, `has_api_key` |
| [`ui.py`](../lib/ui.py) | CSS globale Streamlit | `apply_global_style` |

**Modello LLM**: `claude-sonnet-4-6` ([claude.py:16](../lib/claude.py#L16)).
Tutte le chiamate usano `output_config.format=json_schema` per output strutturato.
Il blocco `speakers` nel system ha `cache_control: ephemeral` → cache hit entro ~5 min.

---

## Mappa pagine `pages/`

| File | Pagina | Ruolo |
|---|---|---|
| [`app.py`](../app.py) | Home | KPI dashboard pipeline, deadline imminenti, follow-up dovuti |
| [`pages/0_Profilo.py`](../pages/0_Profilo.py) | Profilo | Strategia progetto + bio/skills speaker (Luca, Stefano) in tab |
| [`pages/1_Venue.py`](../pages/1_Venue.py) | Venue | Kanban per stato pipeline; edit mode con conversazione, arricchimento LLM, link Ente, tag |
| [`pages/2_Contatti.py`](../pages/2_Contatti.py) | Contatti | CRUD persone con ricerca, link a venue/enti |
| [`pages/3_Outreach.py`](../pages/3_Outreach.py) | Outreach (hidden) | Chat per venue: storico, draft LLM, refine, conferma, paste risposte. Raggiungibile solo via session_state da Venue |
| [`pages/4_Enti.py`](../pages/4_Enti.py) | Enti | CRUD organizers + venue sorelle + contatti |
| [`pages/5_Discovery.py`](../pages/5_Discovery.py) | Discovery | LLM + web search per nuove venue. Run history persistente con log replay |
| [`pages/6_Mappa.py`](../pages/6_Mappa.py) | Mappa | Folium con marker colorati per stato pipeline + filtri |
| [`pages/99_⚙_Impostazioni.py`](../pages/99_⚙_Impostazioni.py) | Impostazioni | API key (save/test/delete), import iniziali, backup DB, export mail markdown |

L'ordine numerico nei nomi file pilota la sidebar Streamlit. `3_Outreach.py` è nascosto da CSS in `lib/ui.py` (raggiunto via `st.session_state["draft_venue_id"]` da Venue).

---

## Flussi end-to-end

### 1. Discovery → accettazione candidate → venue creata

```
[5_Discovery.py]
   │
   ├─► utente seleziona scope (es. "Trentino-Alto Adige") + max_results (8)
   │
   ▼
db.start_discovery_run(run_id, scope, max_results)        ─► discovery_runs (status=running)
   │
   ▼
claude.discover_venues(scope, max_results, on_progress=cb)
   │
   ├─► dossier = [_build_venue_dossier(v) for v in db.list_venues()[:120]]
   ├─► system_blocks (cache_control su speakers)
   ├─► tool web_search_20250305 (max_uses ≈ max_results × 15, cap 300)
   ├─► thinking adaptive + effort=high
   └─► loop pause_turn fino a 10 round (24k token per round)
        │
        ├─► on_progress("🔎 Query: «...»") → db.append_discovery_log()
        │
        ▼
   JSON: lista venue con name, type, city, region, language, angle,
         contact, email, website, fit_score, deadline, ...
   │
   ▼
db.insert_discovery_candidate(run_id, payload)            ─► discovery_candidates
db.complete_discovery_run(run_id, n_found)                ─► status=completed
   │
   ▼
[5_Discovery.py: render run history]
   │
   ├─► utente click "Accetta" su candidate
   │
   ▼
db.insert_venue(source='llm-discovery', notes='[Da discovery {run_id}]', ...)
db.insert_contact(...)  +  db.link_venue_contact(...)
db.insert_interaction(type='prima_mail', is_draft=1, llm_draft=body, content=body, ...)
db.update_discovery_candidate_status(candidate_id, 'accepted')
```

### 2. Draft prima mail → revisione → conferma → tracking risposta

```
[1_Venue.py] click "Apri conversazione" → st.session_state["draft_venue_id"] = id
   │
   ▼
[3_Outreach.py]
   │
   ├─► db.get_interactions_for_venue(venue_id)  → render chat
   ├─► db.get_pending_draft_for_venue(venue_id) → mostra il draft pending se esiste
   │
   ├─► [Se nessun draft] click "Genera draft"
   │       │
   │       ▼
   │   claude.draft_first_email(venue, contact)
   │       │
   │       ├─► dossier blocks: venue + contact + same_venue_history +
   │       │   similar_history (find_similar_venues_extended) +
   │       │   organizer + sibling_venues + cross_contact_history
   │       ├─► email_drafting_guidelines() (fresh da email_guidelines.md)
   │       └─► JSON: {subject, body, channel_suggestion, speaker_choice, tone, language, rationale}
   │
   ├─► [Refine] utente scrive feedback in linguaggio naturale
   │       │
   │       ▼
   │   claude.refine_first_email(venue, contact, refinement_history)
   │       │
   │       └─► JSON stesso schema, applica feedback letterale
   │
   ├─► [Conferma] click "Salva come inviato"
   │       │
   │       ▼
   │   db.update_interaction(id, {is_draft: 0})
   │   db.insert_interaction(pipeline_status_after='contattata', ...)
   │   ─► trigger: venues.pipeline_status='contattata' (auto in insert_interaction)
   │
   ▼
[Stefano apre Aruba webmail, copia/invia, attende risposta]
   │
   ▼
[3_Outreach.py: paste risposta] textarea + click "Salva risposta"
   │
   ▼
claude.analyze_response(venue, response_text, history)
   │
   └─► JSON: {sentiment, is_meeting_proposal, is_rejection, suggested_status, key_info, notes}
       │
       ▼ (mappato via pipeline.normalize_state)
   db.insert_interaction(direction='ricevuta', pipeline_status_after=normalized, ...)
```

### 3. Follow-up suggerito (timing + draft)

```
[app.py: home] _due_actions() mostra venue con last_outgoing > 7 giorni
   │
   ▼
[3_Outreach.py] click "Suggerisci follow-up"
   │
   ▼
last_int = db.get_last_outgoing_interaction(venue_id)
days_since = (today - last_int.occurred_at).days
   │
   ▼
claude.draft_follow_up(venue, contact, last_interaction, response, days_since)
   │
   └─► JSON: {timing_suggestion_days, should_send, subject, body, channel_suggestion, rationale}
       │
       ▼
   Se should_send=False → mostra solo rationale (es. "aspetta ancora N giorni")
   Se should_send=True  → stesso flusso 2 (draft → conferma → invia → tracking)
       │
       ▼
   db.insert_interaction(type=derive_interaction_type('inviata', prior_count), ...)
   ─► tipo deriva auto: prima_mail → follow_up_1 → follow_up_2 → ...
```

---

## Decisioni di design

### Encryption Fernet della API key
**Perché**: il tool è desktop, gira in `cwd` arbitrario, non c'è una shell di processo definita per `.env`. Una master key in `~/.config/outreach/master.key` (perms 0o600) è più sicura di un `.env` dimenticato in cartelle sincronizzate, ed evita che la key finisca in dump del DB.
**Trade-off**: se l'utente perde la master key, deve reincollare la API key in UI.

### Pipeline a 6 stati invece di 8
Lo spec aprile 2026 prevedeva 8 stati granulari (`risposta_ricevuta`, `meeting_fissato`, `presentazione_confermata`, `completata`, ...). Riduzione a 6 stati (`da_contattare, contattata, accettata, interessati_futuro, rifiutata, ghostati`): la granularità extra non veniva usata in pratica perché il vero "esito" è quasi sempre binario (accettato / rifiutato / ghosting), e il dettaglio meeting/presentazione è già nel campo `notes` o nelle interazioni. `interessati_futuro` è l'unica eccezione tenuta separata: segnala interesse espresso ma non per l'occasione contattata (es. un ente disponibile per un'edizione futura). Migrazione legacy automatica in `init_db()`.

### `organizers` aggiunti post-spec
Lo spec trattava ogni venue come isolata. Nella pratica molte venue sono **club locali di un ente madre** (Rotary 2060, Lions Distretto 108, ateneo con più dipartimenti). Il modello `organizers` permette di:
- Riconoscere venue sorelle e usarne lo storico nei dossier (`_organizer_context_for_venue`)
- Centralizzare info ente (sito, contatti centrali) senza duplicarle su ogni venue
- Filtrare "venue orfane" (`list_orphan_venues`) per pulizie e organizzazione

### `discovery_runs` persistenti
Una run può durare minuti (web search × 100+ query, fino a 10 round `pause_turn`). La persistenza in DB:
- Sopravvive a riavvii Streamlit (utile durante refactoring UI)
- Permette di rivedere log step-by-step (`log_json`)
- Lega le venue create al run di origine via marker `[Da discovery {run_id}]` in `notes` (vedi `get_mails_for_discovery_run`)

### `is_draft` flag su `interactions`
Le prime mail generate da discovery vengono inserite come draft pending. Distinzione critica:
- `count_outgoing_for_venue()` esclude `is_draft=1` → la pipeline non avanza finché Stefano non conferma
- `_build_draft_context_blocks()` esclude i draft dal context → l'LLM non crede che la mail sia già stata inviata
- Migrazione one-shot all'init: marca i draft pre-esistenti (vedi `init_db` flag `migrated_discovery_drafts`)

### SQLite vs Postgres
Volumi attesi <1k venue, <10k interazioni. SQLite gestisce questo carico con margine; backup = `cp` di un file. Niente network, niente daemon, niente downtime per upgrade.

### No invio email integrato
**Perché** (citando spec aprile §3): "Il tool drafta il testo, Stefano lo copia e lo invia da Aruba webmail o dal canale social appropriato". Motivi:
1. Aruba SMTP è instabile e gli SDK Python richiedono credenziali a parte
2. Inviare da un canale "automatico" perde tono personale
3. Stefano è già loggato in Aruba/LinkedIn/Instagram nel browser

### Logging via `st.*` invece di `logging`
Non c'è uno strato non-UI da loggare separatamente: ogni operazione è triggerata da un click in Streamlit. `st.success`, `st.error`, `st.toast` vanno direttamente all'utente. Per debug post-mortem c'è il file `data/streamlit.log` (catturato da `nohup` in `launch.sh`).

### Geocoding con cache hardcoded
[`lib/geocode.py`](../lib/geocode.py) ha un dict `CITY_COORDS` con ~120 città italiane pre-cablato. Motivi:
1. Nominatim è rate-limited (1 req/s) e a volte timeout
2. La maggior parte delle venue sono in città capoluogo già note
3. Fallback a Nominatim solo per città non in cache → riduce traffico esterno e flakiness

### Web search tool: `web_search_20250305` (non l'ultima versione)
Versione scelta esplicitamente per evitare il bug del `container_id` pendente con `pause_turn` nella variante con `dynamic filtering` → vedi commento [claude.py:534-536](../lib/claude.py#L534-L536).

---

## Convenzioni codice

| Aspetto | Scelta |
|---|---|
| Lingua | **Italiano** (commenti, variabili, UI, prompt) |
| Type hints | PEP 484 completo, `from __future__ import annotations` in tutti i file |
| Strutture dati | `@dataclass` quando serve (es. `ParsedVenue` in importer); **no pydantic** |
| Logging | `st.success/error/toast` (UI) + file `data/streamlit.log` (run loggato da `launch.sh`) |
| DB access | Sempre via context manager `transaction()` |
| Errori | `try/except` espliciti con messaggi user-facing in `st.error` |
| Naming | `snake_case` funzioni/variabili, `PascalCase` classi, `CONSTANT_CASE` costanti |
| Test | Solo smoke (`tests/test_importer.py` + `tests/test_organizers.py`), no pytest, eseguibile come script |

---

## Cosa manca (Fase 2 dello spec — non implementato)

| § | Funzionalità | Stato |
|---|---|---|
| 2.1 | Ingestion slide PPTX/PDF | ❌ |
| 2.2 | Dashboard metriche interne (UI) | ⚠️ metriche già consumate dall'LLM nei dossier ma non esposte in UI |
| 2.3 | Discovery schedulata + alert deadline | ❌ |
| 2.4 | Backup automatico schedulato | ⚠️ backup manuale esiste, scheduler no |

Vedi spec originale archiviata: [`docs/legacy/SPEC_2026-04.md`](legacy/SPEC_2026-04.md).
