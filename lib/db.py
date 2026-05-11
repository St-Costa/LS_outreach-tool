"""Layer SQLite: schema, migrazioni idempotenti e CRUD per tutte le entità.

Tutte le funzioni pubbliche aprono e chiudono la connessione tramite il context
manager `transaction()` (commit/rollback automatico). Schema e indici sono
definiti in `SCHEMA` (riga 13); `init_db()` applica anche le migrazioni legacy
(ALTER, collapse stati pipeline, seed Luca/Stefano/project_profile).

Vedi `docs/SCHEMA.md` per il modello dati completo.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "outreach.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT,
  building TEXT,
  address TEXT,
  city TEXT,
  province TEXT,
  region TEXT,
  lat REAL,
  lon REAL,
  email TEXT,
  website TEXT,
  social_instagram TEXT,
  social_facebook TEXT,
  social_linkedin TEXT,
  language TEXT,
  funding_type TEXT,
  angle TEXT,
  description TEXT,
  notes TEXT,
  deadline_text TEXT,
  deadline_date DATE,
  source TEXT,
  pipeline_status TEXT DEFAULT 'da_contattare',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  mission TEXT,
  offering TEXT,
  target_ideal TEXT,
  exclusions TEXT,
  differentiators TEXT,
  notes TEXT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS venue_tags (
  venue_id INTEGER REFERENCES venues(id) ON DELETE CASCADE,
  tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (venue_id, tag_id)
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  role TEXT,
  email TEXT,
  phone TEXT,
  social_linkedin TEXT,
  social_instagram TEXT,
  language_pref TEXT,
  suggested_tone TEXT,
  interests_json TEXT,
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS venue_contacts (
  venue_id INTEGER REFERENCES venues(id) ON DELETE CASCADE,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  PRIMARY KEY (venue_id, contact_id)
);

CREATE TABLE IF NOT EXISTS organizers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT,
  website TEXT,
  hq_city TEXT,
  hq_province TEXT,
  region TEXT,
  language TEXT,
  description TEXT,
  notes TEXT,
  social_linkedin TEXT,
  social_instagram TEXT,
  social_facebook TEXT,
  source TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS organizer_contacts (
  organizer_id INTEGER REFERENCES organizers(id) ON DELETE CASCADE,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  PRIMARY KEY (organizer_id, contact_id)
);

CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY,
  occurred_at TIMESTAMP NOT NULL,
  channel TEXT NOT NULL,
  direction TEXT NOT NULL,
  venue_id INTEGER REFERENCES venues(id),
  contact_id INTEGER REFERENCES contacts(id),
  type TEXT,
  subject TEXT,
  content TEXT NOT NULL,
  llm_draft TEXT,
  pipeline_status_after TEXT,
  is_draft INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS speakers (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  bio TEXT,
  skills_json TEXT,
  experiences_json TEXT,
  languages_json TEXT,
  role_in_pair TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovery_candidates (
  id INTEGER PRIMARY KEY,
  run_id TEXT,
  payload_json TEXT,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovery_runs (
  run_id TEXT PRIMARY KEY,
  scope TEXT,
  max_results INTEGER,
  status TEXT DEFAULT 'running',
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT,
  n_found INTEGER DEFAULT 0,
  log_json TEXT  -- lista di {ts, msg} per replay degli step
);

CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY,
  venue_id INTEGER REFERENCES venues(id) ON DELETE CASCADE,
  interaction_id INTEGER REFERENCES interactions(id) ON DELETE SET NULL,
  filename TEXT NOT NULL,
  mime TEXT,
  size INTEGER,
  path TEXT NOT NULL,
  kind TEXT,                -- slide|workshop|case_study|brochure|presentazione|documento|immagine|altro
  summary_json TEXT,        -- JSON: {title, kind, target_audience, key_topics, duration_minutes, summary, when_to_use}
  summary_manual TEXT,      -- override editabile dall'utente (markdown libero)
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interaction_attachments (
  interaction_id INTEGER NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
  attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  PRIMARY KEY (interaction_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  table_name TEXT NOT NULL,
  row_id INTEGER,
  op TEXT NOT NULL,        -- 'update' | 'delete'
  before_json TEXT,
  after_json TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  task TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cache_creation_tokens INTEGER DEFAULT 0,
  duration_ms INTEGER,
  error TEXT,
  meta_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_venues_status ON venues(pipeline_status);
CREATE INDEX IF NOT EXISTS idx_venues_region ON venues(region);
CREATE INDEX IF NOT EXISTS idx_interactions_venue ON interactions(venue_id);
CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id);
CREATE INDEX IF NOT EXISTS idx_interactions_occurred_at ON interactions(occurred_at);
CREATE INDEX IF NOT EXISTS idx_interactions_venue_dir_draft ON interactions(venue_id, direction, is_draft);
CREATE INDEX IF NOT EXISTS idx_organizers_name ON organizers(name);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task ON llm_calls(task);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_log_table_row ON audit_log(table_name, row_id);
CREATE INDEX IF NOT EXISTS idx_attachments_venue ON attachments(venue_id);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # detect_types disabilitato: sqlite3 di Python 3.12 ha un converter strict per TIMESTAMP
    # che esplode su ISO-8601 con 'T'. Gestiamo i timestamp come stringhe e parsiamo
    # esplicitamente quando serve (datetime.fromisoformat accetta entrambi i separatori).
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Evita SQLITE_BUSY su scritture concorrenti (multi-tab Streamlit): attende fino a 5s
    # che il write lock si liberi prima di alzare l'eccezione.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction():
    """Context manager: apre la connessione, commit su successo, rollback su eccezione, close garantito."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- Audit log helpers ----------------

def _audit_capture(conn: sqlite3.Connection, table_name: str, row_id: int) -> Optional[dict]:
    """Snapshot della riga prima della modifica. Ritorna dict (None se assente)."""
    try:
        row = conn.execute(f"SELECT * FROM {table_name} WHERE id=?", (row_id,)).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}
    except sqlite3.Error:
        return None


def _audit_record(
    conn: sqlite3.Connection,
    table_name: str,
    row_id: Optional[int],
    op: str,
    before: Optional[dict],
    after: Optional[dict],
) -> None:
    """Inserisce un record di audit nella stessa transazione del caller (atomico).
    Errori di scrittura audit non bloccano la transazione principale.
    """
    try:
        conn.execute(
            "INSERT INTO audit_log(table_name, row_id, op, before_json, after_json) VALUES (?,?,?,?,?)",
            (
                table_name,
                row_id,
                op,
                json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
                json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            ),
        )
    except sqlite3.Error:
        pass


def global_search(query: str, limit_per_table: int = 30) -> dict:
    """Ricerca full-text 'come grep' su venue, contatti, interactions e organizers.

    Ritorna un dict con le 4 liste; ogni hit è la riga completa.
    Il match è LIKE case-insensitive sui campi più rilevanti per ogni entità.
    """
    q = (query or "").strip()
    if not q:
        return {"venues": [], "contacts": [], "interactions": [], "organizers": []}
    like = f"%{q}%"
    with transaction() as conn:
        venues = conn.execute(
            """
            SELECT * FROM venues
            WHERE name LIKE ? OR notes LIKE ? OR description LIKE ? OR email LIKE ? OR website LIKE ? OR city LIKE ?
            ORDER BY name LIMIT ?
            """,
            (like, like, like, like, like, like, limit_per_table),
        ).fetchall()
        contacts = conn.execute(
            """
            SELECT * FROM contacts
            WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR role LIKE ? OR notes LIKE ?
            ORDER BY last_name, first_name LIMIT ?
            """,
            (like, like, like, like, like, limit_per_table),
        ).fetchall()
        interactions = conn.execute(
            """
            SELECT * FROM interactions
            WHERE subject LIKE ? OR content LIKE ?
            ORDER BY occurred_at DESC LIMIT ?
            """,
            (like, like, limit_per_table),
        ).fetchall()
        organizers = conn.execute(
            """
            SELECT * FROM organizers
            WHERE name LIKE ? OR description LIKE ? OR website LIKE ?
            ORDER BY name LIMIT ?
            """,
            (like, like, like, limit_per_table),
        ).fetchall()
    return {
        "venues": rows_to_dicts(venues),
        "contacts": rows_to_dicts(contacts),
        "interactions": rows_to_dicts(interactions),
        "organizers": rows_to_dicts(organizers),
    }


def list_audit_log(
    table_name: Optional[str] = None,
    row_id: Optional[int] = None,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM audit_log"
    where = []
    params: list[Any] = []
    if table_name:
        where.append("table_name=?")
        params.append(table_name)
    if row_id is not None:
        where.append("row_id=?")
        params.append(row_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with transaction() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def init_db() -> None:
    """Crea schema e applica migrazioni idempotenti.

    Esegue: CREATE TABLE IF NOT EXISTS dello schema, ALTER per le colonne
    aggiunte post-MVP (description, acceptance_score, organizer_id, log_json,
    is_draft), collapse degli stati pipeline legacy → nuovo set a 5 valori,
    one-shot per marcare draft pre-esistenti, seed di speakers (Luca, Stefano)
    e project_profile (singleton id=1).
    """
    with transaction() as conn:
        conn.executescript(SCHEMA)
        # Migration: add columns introduced after the first MVP
        try:
            conn.execute("ALTER TABLE venues ADD COLUMN description TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE venues ADD COLUMN acceptance_score INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE discovery_runs ADD COLUMN log_json TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE interactions ADD COLUMN is_draft INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE interactions ADD COLUMN speaker_choice TEXT")
        except sqlite3.OperationalError:
            pass
        # JSON con la lista di materiali "da creare prima di inviare" suggeriti dall'LLM
        # (proposte workshop, schede caso, deck custom). Spec strutturate: title, kind,
        # audience, content_outline, talking_points, estimated_pages, rationale.
        # Restano legate all'interaction perché sono coerenti col body di QUELLA mail.
        try:
            conn.execute(
                "ALTER TABLE interactions ADD COLUMN pending_attachment_specs_json TEXT"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE venues ADD COLUMN organizer_id INTEGER REFERENCES organizers(id) ON DELETE SET NULL"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_venues_organizer ON venues(organizer_id)")
        except sqlite3.OperationalError:
            pass
        # Migration: estendi attachments con i campi summary (riassunto LLM cached)
        for ddl in (
            "ALTER TABLE attachments ADD COLUMN kind TEXT",
            "ALTER TABLE attachments ADD COLUMN summary_json TEXT",
            "ALTER TABLE attachments ADD COLUMN summary_manual TEXT",
            "ALTER TABLE attachments ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # One-shot: marca come draft le prime mail auto-generate dalla discovery
        # che non sono mai state modificate a mano (heuristic: llm_draft NULL o == content).
        # Idempotente: usa un flag in `settings` per girare una sola volta.
        already = conn.execute(
            "SELECT value FROM settings WHERE key='migrated_discovery_drafts'"
        ).fetchone()
        if not already:
            conn.execute(
                """
                UPDATE interactions
                SET is_draft = 1
                WHERE direction = 'inviata'
                  AND type = 'prima_mail'
                  AND COALESCE(is_draft, 0) = 0
                  AND (llm_draft IS NULL OR llm_draft = content)
                  AND venue_id IN (SELECT id FROM venues WHERE source = 'llm-discovery')
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value, updated_at) "
                "VALUES('migrated_discovery_drafts', '1', CURRENT_TIMESTAMP)"
            )
        # Migration: collapse legacy pipeline states to the new simplified set
        legacy_map = {
            "risposta_ricevuta": "contattata",
            "meeting_fissato": "accettata",
            "presentazione_confermata": "accettata",
            "completata": "accettata",
            "nessuna_risposta": "ghostati",
        }
        for old, new in legacy_map.items():
            conn.execute(
                "UPDATE venues SET pipeline_status=? WHERE pipeline_status=?",
                (new, old),
            )
            conn.execute(
                "UPDATE interactions SET pipeline_status_after=? WHERE pipeline_status_after=?",
                (new, old),
            )
        # Seed speakers placeholder if missing.
        # Override opzionale via `data/speakers.seed.json`: lista di
        # {name, bio?, skills?, experiences?, languages?, role_in_pair?}.
        # Se il file è presente, viene usato come seed iniziale (solo per i name
        # ancora assenti nella tabella — INSERT OR IGNORE preserva eventuali edit).
        seed_path = DB_PATH.parent / "speakers.seed.json"
        seed_speakers: list[dict] = []
        if seed_path.exists():
            try:
                seed_speakers = json.loads(seed_path.read_text(encoding="utf-8")) or []
            except (json.JSONDecodeError, OSError):
                seed_speakers = []
        if not seed_speakers:
            seed_speakers = [
                {"name": "Luca", "languages": ["IT"]},
                {"name": "Stefano", "languages": ["IT"]},
            ]
        for sp in seed_speakers:
            name = (sp.get("name") or "").strip()
            if not name:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO speakers "
                "(name, bio, skills_json, experiences_json, languages_json, role_in_pair) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    name,
                    sp.get("bio") or "",
                    json.dumps(sp.get("skills") or [], ensure_ascii=False),
                    json.dumps(sp.get("experiences") or [], ensure_ascii=False),
                    json.dumps(sp.get("languages") or ["IT"], ensure_ascii=False),
                    sp.get("role_in_pair") or "",
                ),
            )
        # Seed empty project profile
        conn.execute(
            "INSERT OR IGNORE INTO project_profile (id, mission, offering, target_ideal, exclusions, differentiators, notes) "
            "VALUES (1, '', '', '', '', '', '')"
        )


# ---------------- Generic helpers ----------------

def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------- Venues ----------------

VENUE_FIELDS = [
    "name", "type", "building", "address", "city", "province", "region",
    "lat", "lon", "email", "website",
    "social_instagram", "social_facebook", "social_linkedin",
    "language", "funding_type", "angle", "description", "notes",
    "deadline_text", "deadline_date", "source", "pipeline_status",
    "acceptance_score", "organizer_id",
]


def insert_venue(data: dict) -> int:
    """Inserisce una venue accettando un dict con un sottoinsieme arbitrario di VENUE_FIELDS. Ritorna l'id."""
    fields = [f for f in VENUE_FIELDS if f in data]
    placeholders = ",".join("?" for _ in fields)
    cols = ",".join(fields)
    values = [data.get(f) for f in fields]
    with transaction() as conn:
        cur = conn.execute(f"INSERT INTO venues ({cols}) VALUES ({placeholders})", values)
        return cur.lastrowid


def update_venue(venue_id: int, data: dict) -> None:
    fields = [f for f in VENUE_FIELDS if f in data]
    if not fields:
        return
    sets = ",".join(f"{f}=?" for f in fields)
    values = [data.get(f) for f in fields] + [venue_id]
    with transaction() as conn:
        before = _audit_capture(conn, "venues", venue_id)
        conn.execute(f"UPDATE venues SET {sets} WHERE id=?", values)
        after = _audit_capture(conn, "venues", venue_id)
        _audit_record(conn, "venues", venue_id, "update", before, after)


def delete_venue(venue_id: int) -> None:
    # interactions.venue_id non ha ON DELETE CASCADE: senza pulizia manuale
    # il DELETE fallirebbe con FK violation se la venue ha interazioni.
    with transaction() as conn:
        before = _audit_capture(conn, "venues", venue_id)
        conn.execute("DELETE FROM interactions WHERE venue_id=?", (venue_id,))
        conn.execute("DELETE FROM venues WHERE id=?", (venue_id,))
        _audit_record(conn, "venues", venue_id, "delete", before, None)


def get_venue(venue_id: int) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM venues WHERE id=?", (venue_id,)).fetchone()
        return row_to_dict(row)


def get_venue_by_name(name: str) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM venues WHERE name=?", (name,)).fetchone()
        return row_to_dict(row)


def list_venues(filters: Optional[dict] = None) -> list[dict]:
    """Lista venue con filtri opzionali: type, region, pipeline_status, language, angle, city, organizer_id, orphan, search (LIKE su name/notes/email)."""
    filters = filters or {}
    where = []
    params: list[Any] = []
    for key in ("type", "region", "pipeline_status", "language", "angle", "city", "organizer_id"):
        if filters.get(key) is not None and filters.get(key) != "":
            where.append(f"{key}=?")
            params.append(filters[key])
    if filters.get("orphan"):
        where.append("organizer_id IS NULL")
    if filters.get("search"):
        where.append("(name LIKE ? OR notes LIKE ? OR email LIKE ?)")
        s = f"%{filters['search']}%"
        params.extend([s, s, s])
    sql = "SELECT * FROM venues"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with transaction() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def count_venues_by_status() -> dict[str, int]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT pipeline_status, COUNT(*) AS n FROM venues GROUP BY pipeline_status"
        ).fetchall()
        return {r["pipeline_status"]: r["n"] for r in rows}


# ---------------- Tags ----------------

def upsert_tag(name: str) -> int:
    name = name.strip().lower()
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        return row["id"]


def set_venue_tags(venue_id: int, tag_names: list[str]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM venue_tags WHERE venue_id=?", (venue_id,))
        for tn in tag_names:
            tn = tn.strip().lower()
            if not tn:
                continue
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tn,))
            tid = conn.execute("SELECT id FROM tags WHERE name=?", (tn,)).fetchone()["id"]
            conn.execute("INSERT OR IGNORE INTO venue_tags(venue_id, tag_id) VALUES (?,?)", (venue_id, tid))


def get_venue_tags(venue_id: int) -> list[str]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT t.name FROM tags t JOIN venue_tags vt ON vt.tag_id=t.id WHERE vt.venue_id=? ORDER BY t.name",
            (venue_id,),
        ).fetchall()
        return [r["name"] for r in rows]


def find_similar_venues(venue_id: int, limit: int = 3) -> list[dict]:
    """Return venues that share at least one tag with the given venue, ranked by overlap."""
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT v.*, COUNT(*) AS overlap
            FROM venues v
            JOIN venue_tags vt ON vt.venue_id = v.id
            WHERE vt.tag_id IN (SELECT tag_id FROM venue_tags WHERE venue_id=?)
              AND v.id != ?
            GROUP BY v.id
            ORDER BY overlap DESC, v.created_at DESC
            LIMIT ?
            """,
            (venue_id, venue_id, limit),
        ).fetchall()
        return rows_to_dicts(rows)


def find_similar_venues_extended(venue: dict, limit: int = 3) -> list[dict]:
    """Similarità basata su tag + metadati strutturati (type/city/region/angle/language/funding).

    Funziona anche quando i tag non sono popolati. Score = somma di match pesati.
    """
    if not venue.get("id"):
        return []
    target_tags = set(get_venue_tags(venue["id"]))
    candidates = list_venues()
    candidates = [c for c in candidates if c["id"] != venue["id"]]

    scored: list[tuple[int, dict]] = []
    for c in candidates:
        score = 0
        if venue.get("type") and c.get("type") == venue.get("type"):
            score += 5
        if venue.get("angle") and c.get("angle") == venue.get("angle"):
            score += 3
        if venue.get("city") and c.get("city") == venue.get("city"):
            score += 2
        if venue.get("region") and c.get("region") == venue.get("region"):
            score += 1
        if venue.get("language") and c.get("language") == venue.get("language"):
            score += 1
        if venue.get("funding_type") and c.get("funding_type") == venue.get("funding_type"):
            score += 2
        c_tags = set(get_venue_tags(c["id"])) if target_tags else set()
        if target_tags and c_tags:
            score += 3 * len(target_tags & c_tags)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [{"venue": c, "score": s} for s, c in scored[:limit]]


def get_interactions_for_contact(contact_id: int, limit: int = 20) -> list[dict]:
    """Tutte le interazioni di un contatto, qualsiasi venue, oldest first."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM interactions WHERE contact_id=? ORDER BY occurred_at ASC, id ASC LIMIT ?",
            (contact_id, limit),
        ).fetchall()
        return rows_to_dicts(rows)


# ---------------- Contacts ----------------

CONTACT_FIELDS = [
    "first_name", "last_name", "role", "email", "phone",
    "social_linkedin", "social_instagram", "language_pref",
    "suggested_tone", "interests_json", "notes",
]


def insert_contact(data: dict) -> int:
    fields = [f for f in CONTACT_FIELDS if f in data]
    cols = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    values = [data.get(f) for f in fields]
    with transaction() as conn:
        cur = conn.execute(f"INSERT INTO contacts ({cols}) VALUES ({placeholders})", values)
        return cur.lastrowid


def update_contact(contact_id: int, data: dict) -> None:
    fields = [f for f in CONTACT_FIELDS if f in data]
    if not fields:
        return
    sets = ",".join(f"{f}=?" for f in fields)
    values = [data.get(f) for f in fields] + [contact_id]
    with transaction() as conn:
        before = _audit_capture(conn, "contacts", contact_id)
        conn.execute(f"UPDATE contacts SET {sets} WHERE id=?", values)
        after = _audit_capture(conn, "contacts", contact_id)
        _audit_record(conn, "contacts", contact_id, "update", before, after)


def delete_contact(contact_id: int) -> None:
    # interactions.contact_id non ha ON DELETE CASCADE. Preserviamo lo storico
    # delle interazioni (resta legato alla venue) settando contact_id=NULL.
    # Le tabelle di link M:N (venue_contacts, organizer_contacts) hanno già CASCADE.
    with transaction() as conn:
        before = _audit_capture(conn, "contacts", contact_id)
        conn.execute("UPDATE interactions SET contact_id=NULL WHERE contact_id=?", (contact_id,))
        conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        _audit_record(conn, "contacts", contact_id, "delete", before, None)


def get_contact(contact_id: int) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        return row_to_dict(row)


def list_contacts(filters: Optional[dict] = None) -> list[dict]:
    filters = filters or {}
    where = []
    params: list[Any] = []
    if filters.get("search"):
        where.append("(first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR role LIKE ?)")
        s = f"%{filters['search']}%"
        params.extend([s, s, s, s])
    if filters.get("venue_id"):
        where.append("id IN (SELECT contact_id FROM venue_contacts WHERE venue_id=?)")
        params.append(filters["venue_id"])
    sql = "SELECT * FROM contacts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY last_name, first_name"
    with transaction() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def find_contact_by_email(email: str) -> Optional[dict]:
    """Match esatto case-insensitive sull'email. None se email vuota o nessun match.

    Usato per evitare duplicati quando l'analisi di outreach propone un contatto
    "nuovo" che però è già nel DB (es. perché trovato in passato da discovery).
    """
    if not (email or "").strip():
        return None
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE LOWER(email) = LOWER(?) LIMIT 1",
            (email.strip(),),
        ).fetchone()
        return row_to_dict(row)


def backfill_null_contact_for_venue(venue_id: int, contact_id: int) -> int:
    """Aggiorna tutte le interactions della venue con contact_id NULL al contact_id passato.

    Ritorna il numero di righe aggiornate. Idempotente: se non ci sono NULL, ritorna 0.
    Usato come 'pulizia storico' prima di switchare il referente: l'utente afferma
    di non aver mai fatto follow-up a contatti diversi sulla stessa venue, quindi
    le interazioni orfane appartengono per definizione al contatto attualmente in uso.
    """
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE interactions SET contact_id=? WHERE venue_id=? AND contact_id IS NULL",
            (contact_id, venue_id),
        )
        return cur.rowcount


def link_venue_contact(venue_id: int, contact_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO venue_contacts(venue_id, contact_id) VALUES (?,?)",
            (venue_id, contact_id),
        )


def unlink_venue_contact(venue_id: int, contact_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "DELETE FROM venue_contacts WHERE venue_id=? AND contact_id=?",
            (venue_id, contact_id),
        )


def get_contacts_for_venue(venue_id: int) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT c.* FROM contacts c JOIN venue_contacts vc ON vc.contact_id=c.id WHERE vc.venue_id=? ORDER BY c.last_name, c.first_name",
            (venue_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_venues_for_contact(contact_id: int) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT v.* FROM venues v JOIN venue_contacts vc ON vc.venue_id=v.id WHERE vc.contact_id=? ORDER BY v.name",
            (contact_id,),
        ).fetchall()
        return rows_to_dicts(rows)


# ---------------- Organizers ----------------

ORGANIZER_FIELDS = [
    "name", "type", "website", "hq_city", "hq_province", "region",
    "language", "description", "notes",
    "social_linkedin", "social_instagram", "social_facebook", "source",
]

ORGANIZER_TYPES = [
    "associazione", "azienda", "istituzione", "universita",
    "network", "hub", "altro",
]


def insert_organizer(data: dict) -> int:
    fields = [f for f in ORGANIZER_FIELDS if f in data]
    cols = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    values = [data.get(f) for f in fields]
    with transaction() as conn:
        cur = conn.execute(f"INSERT INTO organizers ({cols}) VALUES ({placeholders})", values)
        return cur.lastrowid


def update_organizer(organizer_id: int, data: dict) -> None:
    fields = [f for f in ORGANIZER_FIELDS if f in data]
    if not fields:
        return
    sets = ",".join(f"{f}=?" for f in fields)
    values = [data.get(f) for f in fields] + [organizer_id]
    with transaction() as conn:
        before = _audit_capture(conn, "organizers", organizer_id)
        conn.execute(f"UPDATE organizers SET {sets} WHERE id=?", values)
        after = _audit_capture(conn, "organizers", organizer_id)
        _audit_record(conn, "organizers", organizer_id, "update", before, after)


def delete_organizer(organizer_id: int) -> None:
    with transaction() as conn:
        before = _audit_capture(conn, "organizers", organizer_id)
        conn.execute("UPDATE venues SET organizer_id=NULL WHERE organizer_id=?", (organizer_id,))
        conn.execute("DELETE FROM organizer_contacts WHERE organizer_id=?", (organizer_id,))
        conn.execute("DELETE FROM organizers WHERE id=?", (organizer_id,))
        _audit_record(conn, "organizers", organizer_id, "delete", before, None)


def get_organizer(organizer_id: int) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM organizers WHERE id=?", (organizer_id,)).fetchone()
        return row_to_dict(row)


def get_organizer_by_name(name: str) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM organizers WHERE name=?", (name,)).fetchone()
        return row_to_dict(row)


def list_organizers(filters: Optional[dict] = None) -> list[dict]:
    filters = filters or {}
    where = []
    params: list[Any] = []
    for key in ("type", "region", "language"):
        if filters.get(key):
            where.append(f"{key}=?")
            params.append(filters[key])
    if filters.get("search"):
        where.append("(name LIKE ? OR notes LIKE ? OR description LIKE ?)")
        s = f"%{filters['search']}%"
        params.extend([s, s, s])
    sql = "SELECT * FROM organizers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    with transaction() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def count_venues_by_organizer() -> dict[int, int]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT organizer_id, COUNT(*) AS n FROM venues "
            "WHERE organizer_id IS NOT NULL GROUP BY organizer_id"
        ).fetchall()
        return {r["organizer_id"]: r["n"] for r in rows}


def link_organizer_contact(organizer_id: int, contact_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO organizer_contacts(organizer_id, contact_id) VALUES (?,?)",
            (organizer_id, contact_id),
        )


def unlink_organizer_contact(organizer_id: int, contact_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "DELETE FROM organizer_contacts WHERE organizer_id=? AND contact_id=?",
            (organizer_id, contact_id),
        )


def get_contacts_for_organizer(organizer_id: int) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT c.* FROM contacts c "
            "JOIN organizer_contacts oc ON oc.contact_id=c.id "
            "WHERE oc.organizer_id=? ORDER BY c.last_name, c.first_name",
            (organizer_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_organizers_for_contact(contact_id: int) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT o.* FROM organizers o "
            "JOIN organizer_contacts oc ON oc.organizer_id=o.id "
            "WHERE oc.contact_id=? ORDER BY o.name",
            (contact_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_venues_for_organizer(organizer_id: int) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM venues WHERE organizer_id=? ORDER BY name",
            (organizer_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def set_venue_organizer(venue_id: int, organizer_id: Optional[int]) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE venues SET organizer_id=? WHERE id=?",
            (organizer_id, venue_id),
        )


def get_organizer_for_venue(venue_id: int) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute(
            "SELECT o.* FROM organizers o "
            "JOIN venues v ON v.organizer_id=o.id WHERE v.id=?",
            (venue_id,),
        ).fetchone()
        return row_to_dict(row)


def list_orphan_venues(filters: Optional[dict] = None) -> list[dict]:
    f = dict(filters or {})
    f["orphan"] = True
    return list_venues(f)


# ---------------- Interactions ----------------

INTERACTION_FIELDS = [
    "occurred_at", "channel", "direction", "venue_id", "contact_id",
    "type", "subject", "content", "llm_draft", "pipeline_status_after",
    "is_draft", "speaker_choice", "pending_attachment_specs_json",
]


def insert_interaction(data: dict) -> int:
    """Inserisce un'interazione e, se `pipeline_status_after` è valorizzato, aggiorna `venues.pipeline_status` in modo atomico."""
    fields = [f for f in INTERACTION_FIELDS if f in data]
    cols = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    values = [data.get(f) for f in fields]
    with transaction() as conn:
        cur = conn.execute(f"INSERT INTO interactions ({cols}) VALUES ({placeholders})", values)
        # Auto-update venue pipeline status
        if data.get("venue_id") and data.get("pipeline_status_after"):
            conn.execute(
                "UPDATE venues SET pipeline_status=? WHERE id=?",
                (data["pipeline_status_after"], data["venue_id"]),
            )
        return cur.lastrowid


def update_interaction(interaction_id: int, data: dict) -> None:
    fields = [f for f in INTERACTION_FIELDS if f in data]
    if not fields:
        return
    sets = ",".join(f"{f}=?" for f in fields)
    values = [data.get(f) for f in fields] + [interaction_id]
    with transaction() as conn:
        conn.execute(f"UPDATE interactions SET {sets} WHERE id=?", values)


def get_pending_draft_for_venue(venue_id: int) -> Optional[dict]:
    """Returns the pending (unconfirmed) draft outgoing interaction for a venue, if any."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM interactions WHERE venue_id=? AND direction='inviata' AND is_draft=1 "
            "ORDER BY id DESC LIMIT 1",
            (venue_id,),
        ).fetchone()
        return row_to_dict(row)


def venues_with_pending_drafts() -> set[int]:
    """Set of venue ids that have at least one pending draft outgoing interaction."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT DISTINCT venue_id FROM interactions WHERE direction='inviata' AND is_draft=1 "
            "AND venue_id IS NOT NULL"
        ).fetchall()
        return {r["venue_id"] for r in rows}


def list_interactions(filters: Optional[dict] = None, limit: int = 200) -> list[dict]:
    filters = filters or {}
    where = []
    params: list[Any] = []
    for key in ("venue_id", "contact_id", "channel", "direction", "type"):
        if filters.get(key):
            where.append(f"{key}=?")
            params.append(filters[key])
    sql = "SELECT * FROM interactions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with transaction() as conn:
        return rows_to_dicts(conn.execute(sql, params).fetchall())


def count_outgoing_for_venue(venue_id: int) -> int:
    """Conta solo le mail effettivamente confermate (esclude i draft pending)."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM interactions "
            "WHERE venue_id=? AND direction='inviata' AND COALESCE(is_draft,0)=0",
            (venue_id,),
        ).fetchone()
        return row["n"]


def get_interactions_for_venue(venue_id: int) -> list[dict]:
    """All interactions for a venue, oldest first (chat-style)."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM interactions WHERE venue_id=? ORDER BY occurred_at ASC, id ASC",
            (venue_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_last_interaction_per_venue() -> dict[int, dict]:
    """Mapping venue_id → ultima interazione (qualsiasi direzione). Singola query."""
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT i.* FROM interactions i
            JOIN (
                SELECT venue_id, MAX(id) AS max_id
                FROM interactions
                WHERE venue_id IS NOT NULL
                GROUP BY venue_id
            ) m ON i.id = m.max_id
            """
        ).fetchall()
        return {r["venue_id"]: dict(r) for r in rows}


def get_last_interaction_for_venue(venue_id: int) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM interactions WHERE venue_id=? ORDER BY id DESC LIMIT 1",
            (venue_id,),
        ).fetchone()
        return row_to_dict(row)


def get_last_outgoing_interaction(venue_id: int) -> Optional[dict]:
    """Ultima mail outgoing CONFERMATA (esclude draft pending)."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM interactions WHERE venue_id=? AND direction='inviata' "
            "AND COALESCE(is_draft,0)=0 ORDER BY occurred_at DESC LIMIT 1",
            (venue_id,),
        ).fetchone()
        return row_to_dict(row)


def count_interactions_this_month() -> int:
    with transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM interactions WHERE strftime('%Y-%m', occurred_at) = strftime('%Y-%m', 'now')"
        ).fetchone()
        return row["n"]


# ---------------- Speakers ----------------

def get_speakers() -> list[dict]:
    with transaction() as conn:
        rows = conn.execute("SELECT * FROM speakers ORDER BY name").fetchall()
        return rows_to_dicts(rows)


def get_speaker(name: str) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM speakers WHERE name=?", (name,)).fetchone()
        return row_to_dict(row)


# ---------------- Project profile (single row, id=1) ----------------

PROJECT_PROFILE_FIELDS = ["mission", "offering", "target_ideal", "exclusions", "differentiators", "notes"]


def get_project_profile() -> dict:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM project_profile WHERE id=1").fetchone()
        return row_to_dict(row) or {f: "" for f in PROJECT_PROFILE_FIELDS}


def update_project_profile(data: dict) -> None:
    fields = [f for f in PROJECT_PROFILE_FIELDS if f in data]
    if not fields:
        return
    sets = ",".join(f"{f}=?" for f in fields)
    values = [data[f] for f in fields]
    with transaction() as conn:
        conn.execute(
            f"UPDATE project_profile SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            values,
        )


def project_profile_is_empty(profile: dict | None = None) -> bool:
    p = profile if profile is not None else get_project_profile()
    return not any((p.get(f) or "").strip() for f in PROJECT_PROFILE_FIELDS)


def update_speaker(name: str, data: dict) -> None:
    fields = ["bio", "skills_json", "experiences_json", "languages_json", "role_in_pair"]
    sets = ",".join(f"{f}=?" for f in fields if f in data)
    if not sets:
        return
    values = [data[f] for f in fields if f in data] + [name]
    with transaction() as conn:
        conn.execute(f"UPDATE speakers SET {sets} WHERE name=?", values)


# ---------------- Settings ----------------

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with transaction() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )


def delete_setting(key: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))


# ---------------- Discovery candidates ----------------

# ---------------- Discovery runs ----------------

def start_discovery_run(run_id: str, scope: str, max_results: int) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO discovery_runs(run_id, scope, max_results, status, started_at) "
            "VALUES (?, ?, ?, 'running', CURRENT_TIMESTAMP)",
            (run_id, scope, max_results),
        )


def complete_discovery_run(run_id: str, n_found: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE discovery_runs SET status='completed', completed_at=CURRENT_TIMESTAMP, n_found=? "
            "WHERE run_id=?",
            (n_found, run_id),
        )


def append_discovery_log(run_id: str, log_entries: list[dict]) -> None:
    """Salva (sovrascrive) il log della run come JSON. Lista di {ts: int_seconds, msg: str}."""
    with transaction() as conn:
        conn.execute(
            "UPDATE discovery_runs SET log_json=? WHERE run_id=?",
            (json.dumps(log_entries, ensure_ascii=False), run_id),
        )


def fail_discovery_run(run_id: str, error_message: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE discovery_runs SET status='errored', completed_at=CURRENT_TIMESTAMP, error_message=? "
            "WHERE run_id=?",
            (error_message[:2000], run_id),
        )


def cancel_discovery_run(run_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE discovery_runs SET status='canceled', completed_at=CURRENT_TIMESTAMP "
            "WHERE run_id=? AND status='running'",
            (run_id,),
        )


def get_discovery_run(run_id: str) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM discovery_runs WHERE run_id=?", (run_id,)).fetchone()
        return row_to_dict(row)


def insert_discovery_candidate(run_id: str, payload: dict) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO discovery_candidates(run_id, payload_json, status) VALUES(?,?,?)",
            (run_id, json.dumps(payload, ensure_ascii=False), "pending"),
        )
        return cur.lastrowid


def list_discovery_candidates(run_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM discovery_candidates"
    where = []
    params: list[Any] = []
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with transaction() as conn:
        rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload_json"])
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result


def get_mails_for_discovery_run(run_id: str) -> list[dict]:
    """Ritorna le mail (prima_mail) salvate per le venue create da un dato discovery run.

    Usa il marker '[Da discovery {run_id}]' nelle note della venue per ricostruire
    retroattivamente l'associazione, così funziona anche su dati pre-esistenti.
    """
    marker = f"[Da discovery {run_id}]"
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT v.id AS venue_id, v.name AS venue_name, v.email AS venue_email
            FROM venues v
            WHERE v.source = 'llm-discovery' AND v.notes LIKE ?
            ORDER BY v.created_at ASC
            """,
            (f"%{marker}%",),
        ).fetchall()
        venues_in_run = [dict(r) for r in rows]

        result = []
        for v in venues_in_run:
            inter = conn.execute(
                """
                SELECT * FROM interactions
                WHERE venue_id = ? AND direction = 'inviata' AND type = 'prima_mail'
                ORDER BY occurred_at ASC, id ASC LIMIT 1
                """,
                (v["venue_id"],),
            ).fetchone()
            if not inter:
                continue
            inter_d = dict(inter)
            # Recupera primo contatto collegato per il destinatario formattato
            contact = conn.execute(
                """
                SELECT c.* FROM contacts c
                JOIN venue_contacts vc ON vc.contact_id = c.id
                WHERE vc.venue_id = ? ORDER BY c.id ASC LIMIT 1
                """,
                (v["venue_id"],),
            ).fetchone()
            contact_d = dict(contact) if contact else {}
            recipient_name = " ".join(filter(None, [
                contact_d.get("first_name"), contact_d.get("last_name"),
            ])).strip()
            recipient_email = contact_d.get("email") or v["venue_email"] or "(email mancante)"
            result.append({
                "venue_name": v["venue_name"],
                "venue_id": v["venue_id"],
                "recipient": recipient_email,
                "recipient_name": recipient_name,
                "recipient_role": contact_d.get("role") or "",
                "channel": inter_d.get("channel") or "email",
                "subject": inter_d.get("subject") or "",
                "body": inter_d.get("content") or "",
                "is_draft": bool(inter_d.get("is_draft") or 0),
            })
        return result


def list_discovery_runs() -> list[dict]:
    """Lista runs combinando tabella discovery_runs (status) + candidati (count)."""
    with transaction() as conn:
        # Tutte le run note (sia da discovery_runs sia da candidates orfani)
        rows = conn.execute(
            """
            SELECT
              COALESCE(r.run_id, c.run_id) AS run_id,
              COALESCE(r.status, 'completed') AS status,
              r.scope,
              r.error_message,
              COALESCE(c.n, 0) AS n,
              COALESCE(c.accepted, 0) AS accepted,
              COALESCE(r.started_at, c.started_at) AS started_at,
              r.completed_at
            FROM discovery_runs r
            FULL OUTER JOIN (
              SELECT run_id,
                     COUNT(*) AS n,
                     SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) AS accepted,
                     MIN(created_at) AS started_at
              FROM discovery_candidates GROUP BY run_id
            ) c ON r.run_id = c.run_id
            ORDER BY started_at DESC
            """
        ).fetchall() if False else None  # SQLite non supporta FULL OUTER JOIN; uso UNION
        rows = conn.execute(
            """
            SELECT
              r.run_id AS run_id,
              r.status AS status,
              r.scope AS scope,
              r.max_results AS max_results,
              r.error_message AS error_message,
              r.log_json AS log_json,
              COALESCE(c.n, 0) AS n,
              COALESCE(c.accepted, 0) AS accepted,
              r.started_at AS started_at,
              r.completed_at AS completed_at
            FROM discovery_runs r
            LEFT JOIN (
              SELECT run_id,
                     COUNT(*) AS n,
                     SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) AS accepted
              FROM discovery_candidates GROUP BY run_id
            ) c ON r.run_id = c.run_id

            UNION

            SELECT
              c.run_id AS run_id,
              'completed' AS status,
              NULL AS scope,
              NULL AS max_results,
              NULL AS error_message,
              NULL AS log_json,
              c.n AS n,
              c.accepted AS accepted,
              c.started_at AS started_at,
              NULL AS completed_at
            FROM (
              SELECT run_id,
                     COUNT(*) AS n,
                     SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) AS accepted,
                     MIN(created_at) AS started_at
              FROM discovery_candidates GROUP BY run_id
            ) c
            WHERE c.run_id NOT IN (SELECT run_id FROM discovery_runs)

            ORDER BY started_at DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def update_discovery_candidate_status(candidate_id: int, status: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE discovery_candidates SET status=? WHERE id=?",
            (status, candidate_id),
        )


def delete_discovery_candidates(candidate_ids: list[int]) -> int:
    if not candidate_ids:
        return 0
    placeholders = ",".join("?" * len(candidate_ids))
    with transaction() as conn:
        cur = conn.execute(
            f"DELETE FROM discovery_candidates WHERE id IN ({placeholders})",
            tuple(candidate_ids),
        )
        return cur.rowcount or 0


# ---------------- LLM call telemetry ----------------

LLM_CALL_FIELDS = [
    "task", "model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
    "duration_ms", "error", "meta_json",
]


def insert_llm_call(data: dict) -> int:
    cols = ",".join(LLM_CALL_FIELDS)
    placeholders = ",".join(["?"] * len(LLM_CALL_FIELDS))
    values = [data.get(f) for f in LLM_CALL_FIELDS]
    with transaction() as conn:
        cur = conn.execute(f"INSERT INTO llm_calls({cols}) VALUES ({placeholders})", values)
        return cur.lastrowid


def list_llm_calls(limit: int = 200) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM llm_calls ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return rows_to_dicts(rows)


# ---------------- Attachments ----------------

ATTACHMENTS_DIR = DB_PATH.parent / "attachments"


def save_attachment(file_obj, venue_id: Optional[int] = None) -> dict:
    """Salva un file uploadato (Streamlit UploadedFile-like) su disco e registra
    una row in `attachments`. Ritorna il dict della row creata.

    Path: `data/attachments/<venue_id or 'shared'>/<timestamp>_<basename>`.
    `venue_id=None` (default) = libreria globale, riusabile su tutte le venue.
    Il summary LLM viene generato a parte via `update_attachment_summary()`.
    """
    import re as _re
    from datetime import datetime as _dt

    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    sub = str(venue_id) if venue_id else "shared"
    target_dir = ATTACHMENTS_DIR / sub
    target_dir.mkdir(parents=True, exist_ok=True)

    raw_name = getattr(file_obj, "name", "file")
    safe_name = _re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name)[:120] or "file"
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"{ts}_{safe_name}"

    data = file_obj.read() if hasattr(file_obj, "read") else bytes(file_obj)
    target.write_bytes(data)

    rec = {
        "venue_id": venue_id,
        "filename": raw_name,
        "mime": getattr(file_obj, "type", None),
        "size": len(data),
        "path": str(target),
    }
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO attachments(venue_id, filename, mime, size, path) VALUES (?,?,?,?,?)",
            (rec["venue_id"], rec["filename"], rec["mime"], rec["size"], rec["path"]),
        )
        rec["id"] = cur.lastrowid
    return rec


def update_attachment_summary(
    attachment_id: int,
    summary_json: Optional[dict] = None,
    summary_manual: Optional[str] = None,
    kind: Optional[str] = None,
) -> None:
    """Aggiorna i campi summary. Passa solo i campi da modificare."""
    sets: list[str] = []
    params: list[Any] = []
    if summary_json is not None:
        sets.append("summary_json=?")
        params.append(json.dumps(summary_json, ensure_ascii=False))
    if summary_manual is not None:
        sets.append("summary_manual=?")
        params.append(summary_manual)
    if kind is not None:
        sets.append("kind=?")
        params.append(kind)
    if not sets:
        return
    sets.append("updated_at=CURRENT_TIMESTAMP")
    params.append(attachment_id)
    with transaction() as conn:
        conn.execute(f"UPDATE attachments SET {','.join(sets)} WHERE id=?", params)


def get_attachment(attachment_id: int) -> Optional[dict]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return _hydrate_attachment(row_to_dict(row))


def list_attachments(venue_id: Optional[int] = None, include_shared: bool = True) -> list[dict]:
    """Lista allegati. Default: tutta la libreria (shared + per-venue).
    Se `venue_id` è dato e `include_shared=True` → mostra shared (venue_id IS NULL) + quelli della venue.
    Se `venue_id` è dato e `include_shared=False` → solo quelli della venue.
    """
    sql = "SELECT * FROM attachments"
    params: list[Any] = []
    if venue_id is None:
        pass  # tutto
    elif include_shared:
        sql += " WHERE venue_id IS NULL OR venue_id=?"
        params.append(venue_id)
    else:
        sql += " WHERE venue_id=?"
        params.append(venue_id)
    sql += " ORDER BY uploaded_at DESC"
    with transaction() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_hydrate_attachment(dict(r)) for r in rows]


def delete_attachment(attachment_id: int) -> None:
    """Cancella la row e (se esiste) il file su disco."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT path FROM attachments WHERE id=?", (attachment_id,)
        ).fetchone()
        conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
    if row and row["path"]:
        try:
            Path(row["path"]).unlink(missing_ok=True)
        except OSError:
            pass


def _hydrate_attachment(rec: Optional[dict]) -> Optional[dict]:
    """Parsa `summary_json` da stringa a dict come campo `summary` del record."""
    if not rec:
        return rec
    raw = rec.get("summary_json")
    if raw:
        try:
            rec["summary"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            rec["summary"] = None
    else:
        rec["summary"] = None
    return rec


# ---------------- Interaction ↔ Attachments link ----------------

def link_interaction_attachments(interaction_id: int, attachment_ids: list[int]) -> None:
    """Sostituisce l'insieme degli allegati legati all'interaction con quello dato (idempotente)."""
    with transaction() as conn:
        conn.execute("DELETE FROM interaction_attachments WHERE interaction_id=?", (interaction_id,))
        for aid in attachment_ids:
            conn.execute(
                "INSERT OR IGNORE INTO interaction_attachments(interaction_id, attachment_id) VALUES (?,?)",
                (interaction_id, aid),
            )


def get_attachments_for_interaction(interaction_id: int) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            "SELECT a.* FROM attachments a "
            "JOIN interaction_attachments ia ON ia.attachment_id=a.id "
            "WHERE ia.interaction_id=? ORDER BY a.uploaded_at",
            (interaction_id,),
        ).fetchall()
        return [_hydrate_attachment(dict(r)) for r in rows]


def get_attachments_sent_to_venue(venue_id: int) -> list[dict]:
    """Allegati già spediti alla venue (interaction confermate, esclude draft pending). Distinct."""
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT a.* FROM attachments a
            JOIN interaction_attachments ia ON ia.attachment_id=a.id
            JOIN interactions i ON i.id=ia.interaction_id
            WHERE i.venue_id=? AND i.direction='inviata' AND COALESCE(i.is_draft,0)=0
            ORDER BY a.uploaded_at DESC
            """,
            (venue_id,),
        ).fetchall()
        return [_hydrate_attachment(dict(r)) for r in rows]


def llm_calls_summary(days: int = 30) -> dict:
    """Aggregato per la pagina Costi: totali per task negli ultimi `days` giorni."""
    with transaction() as conn:
        rows = conn.execute(
            f"""
            SELECT task, model,
                   COUNT(*) AS n,
                   SUM(input_tokens) AS in_tok,
                   SUM(output_tokens) AS out_tok,
                   SUM(cache_read_tokens) AS cache_read_tok,
                   SUM(cache_creation_tokens) AS cache_creation_tok,
                   SUM(duration_ms) AS duration_ms_total,
                   SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS n_errors
            FROM llm_calls
            WHERE ts >= datetime('now', ?)
            GROUP BY task, model
            ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC
            """,
            (f'-{int(days)} days',),
        ).fetchall()
        return {
            "rows": rows_to_dicts(rows),
            "since_days": days,
        }
