"""Impostazioni — API key, import iniziale, backup DB."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st

from lib import claude, db, importer, ui
from lib.settings import api_key_status, get_api_key, has_api_key, save_api_key

st.set_page_config(page_title="Impostazioni", layout="wide")
ui.apply_global_style()
db.init_db()

BASE_DIR = Path(__file__).resolve().parent.parent

st.title("Impostazioni")

# ---------- API key ----------
st.header("API Key Anthropic")
st.caption("Inserita qui, viene cifrata con Fernet e salvata nel DB locale. La master key è in `~/.config/outreach/master.key` (chmod 600).")

_status = api_key_status()
if _status == "ok":
    st.write("Stato attuale: **presente**")
elif _status == "corrupt":
    st.error(
        "Stato attuale: **non decifrabile**. Il token cifrato è nel DB ma la master key "
        "(`~/.config/outreach/master.key`) è cambiata o stata persa. Re-inserisci la key "
        "qui sotto e premi Salva (vedi `docs/OPERATIONS.md` per il recovery)."
    )
else:
    st.write("Stato attuale: **assente**")

with st.form("api_key_form"):
    new_key = st.text_input(
        "Anthropic API key",
        type="password",
        placeholder="sk-ant-...",
        help="Genera la key da console.anthropic.com",
    )
    col1, col2, col3 = st.columns([1, 1, 4])
    save_btn = col1.form_submit_button("Salva")
    test_btn = col2.form_submit_button("Test connessione")
    delete_btn = col3.form_submit_button("Rimuovi key")

    if save_btn and new_key:
        save_api_key(new_key)
        st.success("API key salvata.")
        st.rerun()
    if test_btn:
        if not has_api_key() and new_key:
            save_api_key(new_key)
        with st.spinner("Test in corso..."):
            ok, msg = claude.test_connection()
            (st.success if ok else st.error)(msg)
    if delete_btn:
        save_api_key("")
        st.success("API key rimossa.")
        st.rerun()

st.divider()

# ---------- Import venue iniziali ----------
st.header("Importa venue iniziali")
st.caption("Legge `venue 1.md` e `venue 2.md` da `data/source/` (fallback: cartella radice). Idempotente: salta venue già presenti per nome.")

found_files = importer.find_default_files(BASE_DIR)
if found_files:
    st.write("File trovati:")
    for f in found_files:
        st.write(f"- `{f.relative_to(BASE_DIR)}`")
else:
    st.info("Nessun file `venue 1.md` / `venue 2.md` trovato in `data/source/` o nella radice `" + str(BASE_DIR) + "`")

if st.button("Importa ora", disabled=not found_files):
    with st.spinner("Parsing in corso..."):
        result = importer.import_files(found_files)
    st.success(f"Importate {len(result['inserted'])} venue. Saltate (già presenti): {len(result['skipped'])}.")
    if result["errors"]:
        st.error("Errori:\n" + "\n".join(result["errors"]))
    if result["inserted"]:
        with st.expander("Dettaglio inserite"):
            for n in result["inserted"]:
                st.write(f"- {n}")
    if result["skipped"]:
        with st.expander("Dettaglio saltate"):
            for n in result["skipped"]:
                st.write(f"- {n}")

st.divider()

# ---------- Database ----------
st.header("Database")
db_path = db.DB_PATH
size_kb = db_path.stat().st_size // 1024 if db_path.exists() else 0
st.write(f"Percorso: `{db_path}`")
st.write(f"Dimensione: {size_kb} KB")

backup_dir = BASE_DIR / "data" / "backups"

col1, col2 = st.columns(2)
if col1.button("Backup ora"):
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"outreach_{ts}.db"
    shutil.copy2(db_path, target)
    st.success(f"Backup salvato: `{target.name}`")

backups = sorted(backup_dir.glob("*.db"), reverse=True) if backup_dir.exists() else []
if backups:
    with col2:
        st.caption(f"Backup esistenti: {len(backups)}")
        for b in backups[:5]:
            st.write(f"- `{b.name}` ({b.stat().st_size // 1024} KB)")

st.divider()

# ---------- Export CSV ----------
st.header("Export CSV")
st.caption("Esporta lo stato corrente del DB in CSV (UTF-8 con BOM, compatibile Excel).")

ec1, ec2, ec3 = st.columns(3)
ec1.download_button(
    "⬇ Venue",
    data=ui.rows_to_csv_bytes(
        db.list_venues(),
        columns=[
            "id", "name", "type", "city", "province", "region", "language", "angle",
            "pipeline_status", "acceptance_score", "email", "website",
            "deadline_text", "deadline_date", "organizer_id", "source",
            "lat", "lon", "notes",
        ],
    ),
    file_name="venues.csv",
    mime="text/csv",
    use_container_width=True,
)
ec2.download_button(
    "⬇ Contatti",
    data=ui.rows_to_csv_bytes(
        db.list_contacts(),
        columns=[
            "id", "first_name", "last_name", "role", "email", "phone",
            "language_pref", "social_linkedin", "social_instagram",
            "suggested_tone", "notes",
        ],
    ),
    file_name="contatti.csv",
    mime="text/csv",
    use_container_width=True,
)
ec3.download_button(
    "⬇ Interazioni",
    data=ui.rows_to_csv_bytes(
        db.list_interactions({}, limit=100000),
        columns=[
            "id", "occurred_at", "channel", "direction", "venue_id", "contact_id",
            "type", "subject", "content", "pipeline_status_after", "is_draft",
            "speaker_choice",
        ],
    ),
    file_name="interazioni.csv",
    mime="text/csv",
    use_container_width=True,
)

st.caption("Calendario delle deadline (.ics): import in Google Calendar / Apple Calendar / Outlook.")
_venues_with_deadline = [v for v in db.list_venues() if v.get("deadline_date")]
st.download_button(
    f"⬇ Deadlines (.ics) — {len(_venues_with_deadline)} eventi",
    data=ui.venues_to_ics(_venues_with_deadline),
    file_name="outreach_deadlines.ics",
    mime="text/calendar",
    disabled=(len(_venues_with_deadline) == 0),
)

st.divider()

# ---------- Audit log ----------
st.header("Audit log")
st.caption("Storico delle modifiche a venue/contatti/enti (update + delete). Solo lettura.")

with st.expander("Mostra ultime 100 modifiche"):
    audit_rows = db.list_audit_log(limit=100)
    if not audit_rows:
        st.info("Nessuna modifica tracciata finora.")
    else:
        import json as _json
        for r in audit_rows:
            ts = r["ts"]
            label = f"`{ts}` · **{r['op']}** su {r['table_name']}#{r['row_id']}"
            with st.container():
                st.markdown(label)
                col_b, col_a = st.columns(2)
                if r["before_json"]:
                    with col_b:
                        st.caption("before")
                        try:
                            st.code(_json.dumps(_json.loads(r["before_json"]), indent=2, ensure_ascii=False), language="json")
                        except Exception:
                            st.code(r["before_json"])
                if r["after_json"]:
                    with col_a:
                        st.caption("after")
                        try:
                            st.code(_json.dumps(_json.loads(r["after_json"]), indent=2, ensure_ascii=False), language="json")
                        except Exception:
                            st.code(r["after_json"])

st.divider()

# ---------- Reset DB (zona pericolosa) ----------
with st.expander("Zona pericolosa — Reset database"):
    st.warning("Cancella tutte le venue, contatti, interazioni. I profili speaker vengono ricreati vuoti.")
    confirm = st.text_input('Per confermare digita esattamente: RESET')
    if st.button("Reset definitivo", disabled=(confirm != "RESET")):
        if db_path.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(db_path, backup_dir / f"outreach_pre-reset_{ts}.db")
            db_path.unlink()
        db.init_db()
        st.success("Database resettato. Backup pre-reset salvato in `data/backups/`.")
        st.rerun()
