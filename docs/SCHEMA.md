# Schema database

> Database SQLite singolo file: `data/outreach.db` (path definito in [lib/db.py:11](../lib/db.py#L11)).
> Schema completo in [lib/db.py:13-167](../lib/db.py#L13-L167). Foreign keys abilitate via `PRAGMA foreign_keys = ON`.

---

## Diagramma relazioni (essenziale)

```
organizers ─< (1:N) ── venues ─< (M:N) ── contacts
                        │            │
                        │            └─< (M:N) ── organizers (via organizer_contacts)
                        ├─< (M:N) ── tags (via venue_tags)
                        └─< (1:N) ── interactions >── contacts

speakers (singleton: Luca, Stefano)
project_profile (singleton id=1)
discovery_runs ─< (1:N) ── discovery_candidates
settings (key/value)
```

---

## Tabelle

### `venues`
Le venue B2B (club, fiere, hub, atenei...). Cuore del sistema.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL | Identificatore "umano", usato per dedup in import |
| `type` | TEXT | Es. `service_club`, `fiera`, `coworking`, `universita`, `hub_innovazione` |
| `building` | TEXT | Edificio ospitante (es. "NOI Techpark") |
| `address`, `city`, `province`, `region` | TEXT | |
| `lat`, `lon` | REAL | Popolate da `lib/geocode.py` (cache hardcoded + Nominatim) |
| `email` | TEXT | Email di contatto venue (può essere `None` se solo via contact) |
| `website` | TEXT | |
| `social_instagram`, `social_facebook`, `social_linkedin` | TEXT | |
| `language` | TEXT | `IT`, `DE`, `EN`, `IT/DE` |
| `funding_type` | TEXT | `pubblico`, `privato`, `associazione`, `cooperativa` |
| `angle` | TEXT | Vedi [ANGLES](#angoli-angles) |
| `description` | TEXT | (aggiunto post-MVP via ALTER) |
| `notes` | TEXT | Free-text. Marker `[Da discovery {run_id}]` lega a run |
| `deadline_text` | TEXT | Es. "Call for speakers chiude 30 giugno" |
| `deadline_date` | DATE | ISO date parsato dal testo |
| `source` | TEXT | `import-md`, `manual`, `llm-discovery` |
| `pipeline_status` | TEXT | Default `da_contattare`. Vedi [PIPELINE_STATES](#stati-pipeline) |
| `acceptance_score` | INTEGER | (aggiunto post-MVP) Score 0–100 da LLM |
| `organizer_id` | INTEGER FK → organizers | NULL se venue indipendente. ON DELETE SET NULL |
| `created_at` | TIMESTAMP | Default `CURRENT_TIMESTAMP` |

**Indici**: `idx_venues_status`, `idx_venues_region`, `idx_venues_organizer`.

---

### `contacts`
Persone con cui si comunica. Many-to-many con `venues` e `organizers`.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `first_name`, `last_name`, `role` | TEXT | |
| `email`, `phone` | TEXT | |
| `social_linkedin`, `social_instagram` | TEXT | |
| `language_pref` | TEXT | |
| `suggested_tone` | TEXT | Suggerito da LLM: `formale`, `cordiale`, `informale`, `tecnico` |
| `interests_json` | TEXT | JSON array di topic |
| `notes` | TEXT | |
| `created_at` | TIMESTAMP | |

### `venue_contacts` — junction venues ↔ contacts
| `venue_id` | FK CASCADE |
| `contact_id` | FK CASCADE |
| PK = `(venue_id, contact_id)` |

---

### `organizers`
Enti madre (Rotary Italia, Lions, ateneo, network) che ospitano più venue. **Aggiunto post-spec**.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL | |
| `type` | TEXT | Vedi [`ORGANIZER_TYPES`](../lib/db.py#L562) — `associazione`, `azienda`, `istituzione`, `universita`, `network`, `hub`, `altro` |
| `website` | TEXT | |
| `hq_city`, `hq_province`, `region` | TEXT | Sede principale |
| `language` | TEXT | |
| `description`, `notes` | TEXT | |
| `social_linkedin`, `social_instagram`, `social_facebook` | TEXT | |
| `source` | TEXT | |
| `created_at` | TIMESTAMP | |

**Indice**: `idx_organizers_name`.

### `organizer_contacts` — junction organizers ↔ contacts
| `organizer_id` | FK CASCADE |
| `contact_id` | FK CASCADE |
| PK = `(organizer_id, contact_id)` |

---

### `interactions`
Log di ogni mail/messaggio inviato o ricevuto. Chat history per venue.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `occurred_at` | TIMESTAMP NOT NULL | Quando l'interazione è avvenuta (può differire da `created_at`) |
| `channel` | TEXT NOT NULL | Vedi [CHANNELS](#canali-channels) |
| `direction` | TEXT NOT NULL | `inviata` o `ricevuta` |
| `venue_id` | FK → venues | |
| `contact_id` | FK → contacts | |
| `type` | TEXT | Vedi [INTERACTION_TYPES](#tipi-interazione). Derivato auto da `direction + prior_outgoing_count` ([pipeline.derive_interaction_type](../lib/pipeline.py#L86)) |
| `subject` | TEXT | Oggetto mail |
| `content` | TEXT NOT NULL | Testo finale (la versione effettivamente inviata o ricevuta) |
| `llm_draft` | TEXT | Draft originale LLM se diverso dal `content` (audit trail) |
| `pipeline_status_after` | TEXT | Stato pipeline della venue dopo l'interazione. `insert_interaction` aggiorna `venues.pipeline_status` automaticamente |
| `is_draft` | INTEGER | (post-MVP) `1` = pending non confermato dall'utente; `0` = confermato/inviato. Default `0` |
| `created_at` | TIMESTAMP | |

**Indici**: `idx_interactions_venue`, `idx_interactions_contact`.

**Pattern critici**:
- `count_outgoing_for_venue()` esclude `is_draft=1` → conta solo le mail confermate.
- `get_pending_draft_for_venue()` ritorna l'unico draft pending outgoing per venue.

---

### `speakers`
Profili speaker (seed iniziale: Luca, Stefano). Singleton per nome.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE NOT NULL | `Luca` o `Stefano` |
| `bio` | TEXT | |
| `skills_json`, `experiences_json`, `languages_json` | TEXT | JSON array |
| `role_in_pair` | TEXT | Ruolo nel duo (es. "lead AI", "lead storytelling") |

---

### `project_profile`
Singleton (id=1) con la strategia globale del progetto. Iniettato nei prompt LLM.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK CHECK (id=1) | Sempre 1 |
| `mission` | TEXT | |
| `offering` | TEXT | |
| `target_ideal` | TEXT | |
| `exclusions` | TEXT | Venue da NON contattare |
| `differentiators` | TEXT | |
| `notes` | TEXT | |
| `updated_at` | TIMESTAMP | |

---

### `tags` + `venue_tags`
Tag liberi per cluster venue. Assegnati da LLM o manuali. Usati da `find_similar_venues()`.

`tags(id PK, name UNIQUE NOT NULL)` — nomi sempre lowercase.
`venue_tags(venue_id FK CASCADE, tag_id FK CASCADE)` — PK composta.

---

### `discovery_runs`
Una riga per ogni run della discovery LLM. Persistente (sopravvive a riavvii Streamlit).

| Campo | Tipo | Note |
|---|---|---|
| `run_id` | TEXT PK | UUID generato da `5_Discovery.py` |
| `scope` | TEXT | Es. "Trentino-Alto Adige", "Italia", scope custom |
| `max_results` | INTEGER | |
| `status` | TEXT | `running` (default), `completed`, `errored`, `canceled` |
| `started_at`, `completed_at` | TIMESTAMP | |
| `error_message` | TEXT | Troncato a 2000 char |
| `n_found` | INTEGER | Default 0 |
| `log_json` | TEXT | (post-MVP) JSON list `[{ts: int_seconds, msg: str}]` per replay step UI |

### `discovery_candidates`
Venue candidate trovate da una run, in attesa di accept/reject.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `run_id` | TEXT | Lega a `discovery_runs.run_id` (no FK formale per supportare run orfane legacy) |
| `payload_json` | TEXT | JSON con tutti i dati venue + contatto + fit score generati dall'LLM |
| `status` | TEXT | `pending` (default), `accepted` |
| `created_at` | TIMESTAMP | |

**Trick**: `list_discovery_runs()` ([db.py:1071](../lib/db.py#L1071)) fa UNION tra `discovery_runs` e candidates orfani per gestire dati pre-tabella `discovery_runs`.

---

### `settings`
Key-value store. Usato per: API key cifrata Fernet, flag di migrazione one-shot.

| Campo | Tipo | Note |
|---|---|---|
| `key` | TEXT PK | |
| `value` | TEXT | |
| `updated_at` | TIMESTAMP | |

**Chiavi note**:
- `anthropic_api_key_enc` — API key Anthropic cifrata (vedi [lib/settings.py](../lib/settings.py))
- `migrated_discovery_drafts` — flag idempotenza migrazione one-shot

---

### `llm_calls`
Log di ogni chiamata Anthropic: token e durata per task/modello. Alimenta la pagina **Costi LLM**.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TIMESTAMP | |
| `task` | TEXT | Es. `draft_first_email`, `discover_venues`, `analyze_response` |
| `model` | TEXT | `claude-sonnet-4-6` o `claude-haiku-4-5-20251001` |
| `input_tokens`, `output_tokens` | INTEGER | |
| `cache_read_tokens`, `cache_creation_tokens` | INTEGER | Per il calcolo del cache-hit rate |
| `duration_ms` | INTEGER | |
| `error` | TEXT | Messaggio errore se la call è fallita |
| `meta_json` | TEXT | Metadati extra per task |

---

### `attachments` + `interaction_attachments`
Allegati (PDF, slide) collegati a una venue o a una singola interazione, con riassunto LLM opzionale
(`summary_json`) o manuale (`summary_manual`). `interaction_attachments` è la junction M:N verso `interactions`.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `venue_id`, `interaction_id` | INTEGER FK | Nullable, un allegato può non essere legato a un'interazione specifica |
| `filename`, `mime`, `size`, `path` | — | Metadati file, storage su disco in `data/attachments/` |
| `kind` | TEXT | Tipo allegato |
| `summary_json` / `summary_manual` | TEXT | Riassunto LLM (`summarize_attachment`) o inserito a mano |

---

### `audit_log`
Log generico before/after per operazioni di scrittura sensibili (JSON diff), usato per troubleshooting
e recovery manuale — non esposto in UI.

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TIMESTAMP | |
| `table_name`, `row_id`, `op` | — | Tabella/riga/operazione interessate |
| `before_json` / `after_json` | TEXT | Stato prima/dopo |

---

## Stati pipeline
Definiti in [lib/pipeline.py:4-43](../lib/pipeline.py#L4-L43).

| Stato (DB value) | Label UI | Colore | Note |
|---|---|---|---|
| `da_contattare` | ⚪ Da contattare | bianco `#FFFFFF` | Default per nuove venue |
| `contattata` | ⏳ Contattata | blu `#3B82F6` | In attesa di risposta |
| `accettata` | 🟢 Accettata | verde `#00E676` | |
| `interessati_futuro` | 💡 Interessati (altre venue) | ambra `#F59E0B` | Interesse espresso ma non per questa call/edizione |
| `rifiutata` | 🔴 Rifiutata | rosso `#FF1744` | |
| `ghostati` | 👻 Ghostati | grigio `#71717A` | Nessuna risposta dopo follow-up |

**Stati legacy mappati** in `init_db()` ([db.py:247-262](../lib/db.py#L247-L262)) e `normalize_state()`:

| Vecchio | Nuovo |
|---|---|
| `risposta_ricevuta` | `contattata` |
| `meeting_fissato` | `accettata` |
| `presentazione_confermata` | `accettata` |
| `completata` | `accettata` |
| `nessuna_risposta` | `ghostati` |

**Stato effettivo** (`derive_effective_state` in [pipeline.py:124](../lib/pipeline.py#L124)) sovrascrive lo stato manuale se l'ultima interazione è outgoing → forza `contattata` (eccetto `ghostati` che resta).

---

## Canali (`CHANNELS`)
Definiti in [lib/pipeline.py:54-66](../lib/pipeline.py#L54-L66).

| Value | Label |
|---|---|
| `email` | Email |
| `ig_dm` | DM Instagram |
| `li_dm` | DM LinkedIn |
| `fb_dm` | DM Facebook |
| `phone` | Telefono |
| `in_person` | Di persona |
| `altro` | Altro |

---

## Tipi interazione (`INTERACTION_TYPES`)
Definiti in [lib/pipeline.py:69-83](../lib/pipeline.py#L69-L83). **Derivati automaticamente** in `derive_interaction_type()`, non scelti dall'utente.

| Value | Label | Quando |
|---|---|---|
| `prima_mail` | Prima mail | `direction=inviata` & 0 outgoing precedenti |
| `follow_up_1` | Follow-up 1 | 1 outgoing precedente |
| `follow_up_2` | Follow-up 2 | 2 outgoing precedenti |
| `follow_up_3` | Follow-up 3 | 3 outgoing precedenti |
| `follow_up_n` | Follow-up | 4+ outgoing precedenti |
| `risposta` | Risposta | `direction=ricevuta` |
| `risposta_automatica` | Risposta automatica | (manuale) |
| `altro` | Altro | (manuale) |

---

## Angoli (`ANGLES`)
Definiti in [lib/pipeline.py:110-121](../lib/pipeline.py#L110-L121).

| Value | Label |
|---|---|
| `storytelling_puro` | Storytelling puro |
| `ai_storytelling` | AI + Storytelling |
| `ai_puro` | AI puro |
| `collaborazione` | Collaborazione |
| `misto` | Misto |

---

## Migrazioni applicate in `init_db()`
Tutte idempotenti (try/except su `OperationalError`). Vedi [lib/db.py:194-274](../lib/db.py#L194-L274).

1. `ALTER TABLE venues ADD COLUMN description TEXT`
2. `ALTER TABLE venues ADD COLUMN acceptance_score INTEGER`
3. `ALTER TABLE discovery_runs ADD COLUMN log_json TEXT`
4. `ALTER TABLE interactions ADD COLUMN is_draft INTEGER DEFAULT 0`
5. `ALTER TABLE venues ADD COLUMN organizer_id INTEGER REFERENCES organizers(id) ON DELETE SET NULL`
6. `CREATE INDEX idx_venues_organizer`
7. **One-shot migration**: marca `is_draft=1` le prime mail auto-generate dalla discovery mai modificate (heuristic: `llm_draft IS NULL OR llm_draft = content`). Idempotente via flag `settings.migrated_discovery_drafts`.
8. **Collapse legacy pipeline states** → vedi tabella sopra.

## Seed iniziale
- `speakers`: insert OR IGNORE di `Luca` e `Stefano` (placeholder bio vuoto).
- `project_profile`: insert OR IGNORE riga singleton id=1 con campi vuoti.

---

## Convenzioni codice
- Tutti gli accessi DB passano dal context manager [`transaction()`](../lib/db.py#L181) → commit auto, rollback su eccezione, close garantito.
- I JSON sono memorizzati come TEXT con `ensure_ascii=False` (preserva UTF-8 italiano).
- I timestamp sono **stringhe** (no `detect_types`): SQLite Python 3.12 ha un converter `TIMESTAMP` strict che esplode su ISO-8601 con `T`. Si parsa esplicitamente con `datetime.fromisoformat()` quando serve. Vedi commento [db.py:172-174](../lib/db.py#L172-L174).
