"""Venue — Kanban board per stato."""
from __future__ import annotations

import streamlit as st

from lib import claude, db, pipeline, ui

st.set_page_config(page_title="Venue", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Venue")


# ============== EDIT MODE: dettaglio singola venue ==============
edit_id = st.session_state.get("venue_edit_id")
if edit_id:
    venue = db.get_venue(edit_id)
    if not venue:
        st.error("Venue non trovata.")
        st.session_state.pop("venue_edit_id", None)
        st.rerun()

    if st.button("← Torna alla bacheca"):
        st.session_state.pop("venue_edit_id", None)
        st.rerun()

    st.subheader(venue["name"])
    current_org = db.get_organizer_for_venue(venue["id"])
    if current_org:
        bc1, bc2 = st.columns([5, 1])
        bc1.markdown(f"🏛 **Ente:** {current_org['name']}" + (f" ({current_org['type']})" if current_org.get("type") else ""))
        if bc2.button("Apri Ente", key="btn_open_org"):
            st.session_state["selected_organizer_id"] = current_org["id"]
            st.switch_page("pages/4_Enti.py")
    if venue.get("description"):
        st.markdown(f"_{venue['description']}_")

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    if action_col1.button("Apri conversazione", key="btn_open_chat", type="primary"):
        st.session_state["draft_venue_id"] = venue["id"]
        st.session_state.pop("venue_edit_id", None)
        st.switch_page("pages/3_Outreach.py")
    if action_col2.button("Arricchisci con LLM", key="btn_enrich"):
        with st.spinner("LLM al lavoro..."):
            try:
                enriched = claude.enrich_venue(venue)
                update_data = {k: v for k, v in enriched.items() if v and k in {
                    "type", "city", "province", "region", "language", "angle",
                    "funding_type", "website", "address",
                }}
                if update_data:
                    db.update_venue(venue["id"], update_data)
                if enriched.get("tags"):
                    db.set_venue_tags(venue["id"], enriched["tags"])
                st.success("Venue arricchita.")
                st.rerun()
            except Exception as e:
                st.error(f"Errore: {e}")
    if action_col3.button("Suggerisci canale", key="btn_channel"):
        with st.spinner("LLM al lavoro..."):
            try:
                contacts = db.get_contacts_for_venue(venue["id"])
                contact = contacts[0] if contacts else None
                res = claude.suggest_channel(venue, contact)
                st.info(
                    f"**Canale primario:** {res.get('primary_channel')}\n\n"
                    f"**Fallback:** {res.get('fallback_channel') or '—'}\n\n"
                    f"**Motivazione:** {res.get('rationale')}"
                )
            except Exception as e:
                st.error(f"Errore: {e}")
    if action_col4.button("Elimina venue", key="btn_delete"):
        st.session_state["confirm_delete_venue"] = venue["id"]

    if st.session_state.get("confirm_delete_venue") == venue["id"]:
        st.warning("Confermi eliminazione? Verranno rimosse anche le interazioni collegate.")
        cc1, cc2 = st.columns([1, 5])
        if cc1.button("Sì, elimina", key="btn_delete_yes"):
            db.delete_venue(venue["id"])
            st.session_state.pop("confirm_delete_venue", None)
            st.session_state.pop("venue_edit_id", None)
            st.success("Eliminata.")
            st.rerun()
        if cc2.button("Annulla", key="btn_delete_no"):
            st.session_state.pop("confirm_delete_venue", None)
            st.rerun()

    # Inline-create Ente (fuori dal form: Streamlit non permette nested forms)
    with st.expander("➕ Crea nuovo Ente (verrà preselezionato qui sotto)"):
        with st.form(f"inline_new_org_{venue['id']}"):
            ic1, ic2 = st.columns(2)
            ino_name = ic1.text_input("Nome Ente *", key=f"ino_name_{venue['id']}")
            ino_type = ic2.selectbox("Tipo", options=[""] + db.ORGANIZER_TYPES, key=f"ino_type_{venue['id']}")
            ino_city = ic1.text_input("HQ Città", key=f"ino_city_{venue['id']}")
            ino_region = ic2.text_input("Regione", value=venue.get("region") or "", key=f"ino_region_{venue['id']}")
            if st.form_submit_button("Crea Ente"):
                if not (ino_name or "").strip():
                    st.error("Nome obbligatorio.")
                else:
                    new_org_id = db.insert_organizer({
                        "name": ino_name.strip(),
                        "type": ino_type or None,
                        "hq_city": ino_city or None,
                        "region": ino_region or None,
                        "source": "manual",
                    })
                    st.session_state[f"pending_org_id_{venue['id']}"] = new_org_id
                    st.success(f"Ente «{ino_name.strip()}» creato. Salva il form qui sotto per assegnarlo.")
                    st.rerun()

    # Edit form
    organizers_all = db.list_organizers()
    org_options = [None] + [o["id"] for o in organizers_all]
    pending_org_id = st.session_state.get(f"pending_org_id_{venue['id']}")
    default_org = pending_org_id if pending_org_id in org_options else venue.get("organizer_id")
    org_index = org_options.index(default_org) if default_org in org_options else 0

    def _org_label(i):
        if i is None:
            return "— nessuno —"
        o = next((o for o in organizers_all if o["id"] == i), None)
        if not o:
            return f"id {i}"
        suffix = f" ({o['type']})" if o.get("type") else ""
        return o["name"] + suffix

    with st.form(f"edit_venue_{venue['id']}"):
        c1, c2 = st.columns(2)
        ev_name = c1.text_input("Nome", value=venue["name"])
        ev_email = c2.text_input("Email", value=venue.get("email") or "")
        ev_type = c1.text_input("Tipo", value=venue.get("type") or "")
        ev_building = c2.text_input("Edificio/struttura", value=venue.get("building") or "")
        ev_address = c1.text_input("Indirizzo", value=venue.get("address") or "")
        ev_city = c2.text_input("Città", value=venue.get("city") or "")
        ev_province = c1.text_input("Provincia (sigla)", value=venue.get("province") or "")
        ev_region = c2.text_input("Regione", value=venue.get("region") or "")
        ev_website = c1.text_input("Sito", value=venue.get("website") or "")
        ev_lang = c2.selectbox("Lingua", ["IT", "EN", "DE", "IT/DE"],
                                 index=["IT", "EN", "DE", "IT/DE"].index(venue.get("language") or "IT"))
        ev_funding = c1.selectbox("Tipo finanziamento", ["", "pubblico", "privato", "associazione", "cooperativa"],
                                   index=["", "pubblico", "privato", "associazione", "cooperativa"].index(venue.get("funding_type") or ""))
        angle_options = [""] + pipeline.ANGLES
        ev_angle = c2.selectbox("Angolo", angle_options,
                                  index=angle_options.index(venue.get("angle") or ""),
                                  format_func=lambda x: "" if not x else pipeline.ANGLE_LABELS.get(x, x))
        current_state = pipeline.normalize_state(venue.get("pipeline_status"))
        ev_status = c1.selectbox("Stato pipeline", pipeline.PIPELINE_STATES,
                                   index=pipeline.PIPELINE_STATES.index(current_state),
                                   format_func=lambda x: pipeline.PIPELINE_LABELS.get(x, x))
        ev_deadline_text = c2.text_input("Deadline (testo)", value=venue.get("deadline_text") or "")
        score_options = [None, 1, 2, 3]
        score_labels = {None: "? (non valutato)", 1: "1 — Probabilmente no", 2: "2 — Forse", 3: "3 — Probabilmente sì"}
        current_score = venue.get("acceptance_score") if venue.get("acceptance_score") in (1, 2, 3) else None
        ev_score = c1.selectbox(
            "Voto compatibilità",
            options=score_options,
            index=score_options.index(current_score),
            format_func=lambda x: score_labels.get(x, "?"),
        )
        ev_ig = c2.text_input("Instagram", value=venue.get("social_instagram") or "")
        ev_li = c1.text_input("LinkedIn", value=venue.get("social_linkedin") or "")
        ev_fb = c2.text_input("Facebook", value=venue.get("social_facebook") or "")
        ev_organizer_id = c1.selectbox(
            "Ente (organizzatore)",
            options=org_options,
            index=org_index,
            format_func=_org_label,
        )

        ev_description = st.text_area(
            "Descrizione (cosa fa, scala, formato eventi/corsi)",
            value=venue.get("description") or "",
            height=120,
        )
        ev_notes = st.text_area("Note", value=venue.get("notes") or "", height=240)

        tags_current = db.get_venue_tags(venue["id"])
        ev_tags_text = st.text_input("Tag (separati da virgola)", value=", ".join(tags_current))

        if st.form_submit_button("Salva modifiche"):
            err = ui.validate_email_or_blank(ev_email) or ui.validate_website_or_blank(ev_website)
            if err:
                st.error(err)
                st.stop()
            db.update_venue(venue["id"], {
                "name": ev_name.strip(),
                "email": ev_email or None,
                "type": ev_type or None,
                "building": ev_building or None,
                "address": ev_address or None,
                "city": ev_city or None,
                "province": ev_province or None,
                "region": ev_region or None,
                "website": ev_website or None,
                "language": ev_lang,
                "funding_type": ev_funding or None,
                "angle": ev_angle or None,
                "pipeline_status": ev_status,
                "deadline_text": ev_deadline_text or None,
                "social_instagram": ev_ig or None,
                "social_linkedin": ev_li or None,
                "social_facebook": ev_fb or None,
                "description": ev_description or None,
                "notes": ev_notes or None,
                "acceptance_score": ev_score,
                "organizer_id": ev_organizer_id,
            })
            new_tags = [t.strip() for t in ev_tags_text.split(",") if t.strip()]
            db.set_venue_tags(venue["id"], new_tags)
            st.session_state.pop(f"pending_org_id_{venue['id']}", None)
            st.success("Modifiche salvate.")
            st.rerun()

    # ----- Allegati -----
    st.subheader("Allegati")
    attached = db.list_attachments_for_venue(venue["id"])
    if attached:
        for a in attached:
            ac1, ac2, ac3 = st.columns([5, 2, 1])
            ac1.write(f"📎 **{a['filename']}** · {(a['size'] or 0) // 1024} KB · `{a.get('mime') or 'unknown'}`")
            try:
                from pathlib import Path as _P
                fpath = _P(a["path"])
                if fpath.exists():
                    ac2.download_button(
                        "Scarica",
                        data=fpath.read_bytes(),
                        file_name=a["filename"],
                        mime=a.get("mime") or "application/octet-stream",
                        key=f"dl_{a['id']}",
                        use_container_width=True,
                    )
                else:
                    ac2.caption("File mancante")
            except Exception:
                ac2.caption("Errore")
            if ac3.button("Elimina", key=f"del_att_{a['id']}", use_container_width=True):
                db.delete_attachment(a["id"])
                st.rerun()
    else:
        st.caption("Nessun allegato.")

    new_att = st.file_uploader(
        "Aggiungi allegato",
        accept_multiple_files=False,
        key=f"upl_att_{venue['id']}",
        help="Salvato in `data/attachments/<venue_id>/`. Max ~200 MB di default Streamlit.",
    )
    if new_att is not None and st.button("Carica allegato", key=f"btn_upl_{venue['id']}"):
        try:
            db.save_attachment(venue["id"], new_att)
            st.success(f"Caricato: {new_att.name}")
            st.rerun()
        except Exception as e:
            st.error(f"Errore upload: {e}")

    # Contacts linked
    st.subheader("Contatti collegati")
    contacts = db.get_contacts_for_venue(venue["id"])
    if contacts:
        for c in contacts:
            cc1, cc2, cc3 = st.columns([4, 4, 1])
            full_name = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip() or "(senza nome)"
            cc1.write(f"**{full_name}** — {c.get('role') or ''}")
            cc2.write(c.get("email") or "")
            if cc3.button("Scollega", key=f"unlink_{c['id']}"):
                db.unlink_venue_contact(venue["id"], c["id"])
                st.rerun()
    else:
        st.caption("Nessun contatto collegato.")

    st.stop()


# ============== KANBAN BOARD ==============

# Default column visibility
DEFAULT_VISIBLE = ["da_contattare", "contattata", "interessati_futuro"]
if "venue_visible_cols" not in st.session_state:
    st.session_state["venue_visible_cols"] = list(DEFAULT_VISIBLE)

# Top controls: filtri visibilità + export CSV + nuova venue
top_l, top_m, top_r = st.columns([4, 1, 1])
with top_l:
    visible = st.multiselect(
        "Colonne visibili",
        options=pipeline.PIPELINE_STATES,
        default=st.session_state["venue_visible_cols"],
        format_func=lambda s: pipeline.PIPELINE_LABELS[s],
        key="venue_visible_cols",
        label_visibility="collapsed",
    )
with top_m:
    _venues_for_export = db.list_venues()
    st.download_button(
        "⬇ CSV venue",
        data=ui.rows_to_csv_bytes(
            _venues_for_export,
            columns=[
                "id", "name", "type", "city", "province", "region", "language",
                "angle", "pipeline_status", "acceptance_score", "email", "website",
                "deadline_text", "deadline_date", "organizer_id", "source",
                "lat", "lon", "notes",
            ],
        ),
        file_name="venues.csv",
        mime="text/csv",
        use_container_width=True,
        help="Esporta tutte le venue in CSV (UTF-8 con BOM, compatibile Excel).",
    )
with top_r:
    if st.button("➕ Nuova venue", use_container_width=True):
        st.session_state["show_new_venue_form"] = True

if st.session_state.get("show_new_venue_form"):
    with st.form("new_venue_form"):
        st.subheader("Nuova venue")
        c1, c2 = st.columns(2)
        nv_name = c1.text_input("Nome *")
        nv_email = c2.text_input("Email")
        nv_type = c1.selectbox("Tipo", ["", "service_club", "associazione", "fiera", "hub_innovazione",
                                          "banca", "universita", "tedx", "ente_camerale", "ente_pubblico",
                                          "agenzia", "evento_startup", "evento", "ente_formativo", "altro"])
        nv_city = c2.text_input("Città")
        nv_region = c1.text_input("Regione")
        nv_lang = c2.selectbox("Lingua", ["IT", "EN", "DE", "IT/DE"])
        nv_angle = c1.selectbox("Angolo", [""] + pipeline.ANGLES,
                                 format_func=lambda x: "" if not x else pipeline.ANGLE_LABELS.get(x, x))
        nv_website = c2.text_input("Sito web")
        nv_organizers = db.list_organizers()
        nv_org_options = [None] + [o["id"] for o in nv_organizers]
        nv_org_id = c1.selectbox(
            "Ente (opzionale)",
            options=nv_org_options,
            format_func=lambda i: "— nessuno —" if i is None
                else next(o["name"] for o in nv_organizers if o["id"] == i),
        )
        nv_notes = st.text_area("Note", height=120)
        col_save, col_cancel = st.columns(2)
        if col_save.form_submit_button("Crea", type="primary"):
            err = (
                None if nv_name.strip() else "Il nome è obbligatorio."
            ) or ui.validate_email_or_blank(nv_email) or ui.validate_website_or_blank(nv_website)
            if err:
                st.error(err)
            else:
                db.insert_venue({
                    "name": nv_name.strip(),
                    "type": nv_type or None,
                    "city": nv_city or None,
                    "region": nv_region or None,
                    "email": nv_email or None,
                    "language": nv_lang,
                    "angle": nv_angle or None,
                    "website": nv_website or None,
                    "notes": nv_notes or None,
                    "source": "manuale",
                    "organizer_id": nv_org_id,
                })
                st.session_state["show_new_venue_form"] = False
                st.success("Venue creata.")
                st.rerun()
        if col_cancel.form_submit_button("Annulla"):
            st.session_state["show_new_venue_form"] = False
            st.rerun()


# Recupera venue + ultime interazioni → effective state
all_venues = db.list_venues()
last_int_map = db.get_last_interaction_per_venue()
pending_draft_venue_ids = db.venues_with_pending_drafts()

# Raggruppa per effective state
by_state: dict[str, list[dict]] = {s: [] for s in pipeline.PIPELINE_STATES}
for v in all_venues:
    eff = pipeline.derive_effective_state(v.get("pipeline_status"), last_int_map.get(v["id"]))
    by_state.setdefault(eff, []).append(v)

# Ordina venue in ogni colonna: 3 → 2 → 1 → ? (None)
def _score_sort_key(v: dict) -> int:
    s = v.get("acceptance_score")
    return -s if s in (1, 2, 3) else 0  # None → 0 (in fondo), 3 → -3, 2 → -2, 1 → -1

for state in by_state:
    by_state[state].sort(key=_score_sort_key)

# CSS per le card
st.markdown(
    """
<style>
.kanban-col-header {
    font-size: 1.05em;
    font-weight: 600;
    margin-bottom: 8px;
    padding-bottom: 6px;
    border-bottom: 2px solid rgba(255,255,255,0.1);
}
.venue-card {
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
    border: 1px solid rgba(255,255,255,0.12);
    transition: filter 0.15s, transform 0.15s;
}
/* Bordo tratteggiato per venue con draft non ancora confermato (Discovery): segnala che la mail va rivista */
.venue-card-pending-draft {
    border-style: dashed !important;
    border-width: 2px !important;
}
.venue-card-pending-draft::after {
    content: "✏️ draft";
    position: absolute;
    bottom: 6px;
    right: 10px;
    font-size: 0.7em;
    opacity: 0.75;
    font-style: italic;
}
.venue-card-da_contattare { background: rgba(255,255,255,0.07); }
.venue-card-contattata    { background: rgba(59,130,246,0.22); border-color: rgba(59,130,246,0.5); }
.venue-card-accettata     { background: rgba(0,230,118,0.22); border-color: rgba(0,230,118,0.5); }
.venue-card-interessati_futuro { background: rgba(245,158,11,0.22); border-color: rgba(245,158,11,0.5); }
.venue-card-rifiutata     { background: rgba(255,23,68,0.20); border-color: rgba(255,23,68,0.5); }
.venue-card-ghostati      { background: rgba(113,113,122,0.25); border-color: rgba(113,113,122,0.5); }
.venue-card-title {
    font-weight: 600;
    font-size: 0.96em;
    line-height: 1.3;
    margin-bottom: 4px;
    padding-right: 36px;  /* spazio riservato per lo score in alto a destra */
}
.venue-card-meta {
    font-size: 0.78em;
    opacity: 0.75;
    margin-bottom: 4px;
}
.venue-card-deadline {
    font-size: 0.78em;
    color: #fbbf24;
    font-weight: 500;
}
.venue-card-score {
    position: absolute;
    top: 10px;
    right: 14px;
    font-size: 1.5em;
    font-weight: 900;
    line-height: 1;
    text-shadow: 0 1px 3px rgba(0,0,0,0.6);
}
.venue-card { position: relative; }
.score-1 { color: #FF1744; }
.score-2 { color: #FFC107; }
.score-3 { color: #00E676; }
.score-unknown { color: #FFFFFF; opacity: 0.85; }
/* Bottone "azioni" reso minimale e attaccato alla card */
.stButton > button[kind="secondary"] {
    margin-top: -6px;
    margin-bottom: 8px;
}
</style>
""",
    unsafe_allow_html=True,
)


def _escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ============== BULK OPERATIONS ==============
with st.expander("🛠️ Operazioni in massa"):
    import pandas as _pd
    st.caption("Seleziona venue dalla tabella e applica un'azione. Le modifiche bypassano il flusso card e sono immediate.")

    bulk_rows = []
    for v in all_venues:
        bulk_rows.append({
            "_select": False,
            "id": v["id"],
            "Nome": v["name"],
            "Tipo": v.get("type") or "",
            "Città": v.get("city") or "",
            "Regione": v.get("region") or "",
            "Stato": v.get("pipeline_status") or "",
            "Score": v.get("acceptance_score"),
        })
    bulk_df = _pd.DataFrame(bulk_rows)
    edited = st.data_editor(
        bulk_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "_select": st.column_config.CheckboxColumn("✓", help="Seleziona venue per le azioni in massa"),
            "id": st.column_config.NumberColumn("id", disabled=True, width="small"),
            "Nome": st.column_config.TextColumn("Nome", disabled=True),
            "Tipo": st.column_config.TextColumn("Tipo", disabled=True, width="small"),
            "Città": st.column_config.TextColumn("Città", disabled=True, width="small"),
            "Regione": st.column_config.TextColumn("Regione", disabled=True, width="small"),
            "Stato": st.column_config.TextColumn("Stato", disabled=True, width="small"),
            "Score": st.column_config.NumberColumn("Score", disabled=True, width="small"),
        },
        key="bulk_editor",
        height=300,
    )
    selected_ids = [int(r["id"]) for _, r in edited.iterrows() if r["_select"]]
    st.write(f"Selezionate: **{len(selected_ids)}**")

    bulk_a, bulk_b, bulk_c = st.columns(3)
    with bulk_a:
        new_state = st.selectbox(
            "Cambia stato a…",
            options=[None] + pipeline.PIPELINE_STATES,
            format_func=lambda s: "—" if s is None else pipeline.PIPELINE_LABELS[s],
            key="bulk_new_state",
        )
        if st.button("Applica stato", disabled=(not selected_ids or new_state is None), use_container_width=True):
            for vid in selected_ids:
                db.update_venue(vid, {"pipeline_status": new_state})
            st.success(f"Stato aggiornato per {len(selected_ids)} venue.")
            st.rerun()
    with bulk_b:
        tags_to_add = st.text_input(
            "Aggiungi tag (separati da virgola)",
            key="bulk_tags_input",
            placeholder="ai, formazione, lombardia",
        )
        if st.button("Aggiungi tag", disabled=(not selected_ids or not tags_to_add.strip()), use_container_width=True):
            new_tags_list = [t.strip().lower() for t in tags_to_add.split(",") if t.strip()]
            for vid in selected_ids:
                existing = set(db.get_venue_tags(vid))
                merged = sorted(existing.union(new_tags_list))
                db.set_venue_tags(vid, merged)
            st.success(f"Tag aggiunti a {len(selected_ids)} venue.")
            st.rerun()
    with bulk_c:
        confirm_text = st.text_input(
            "Per cancellare digita: ELIMINA",
            key="bulk_confirm_delete",
            placeholder="ELIMINA",
        )
        if st.button(
            f"Elimina {len(selected_ids)} venue",
            disabled=(not selected_ids or confirm_text != "ELIMINA"),
            type="secondary",
            use_container_width=True,
        ):
            for vid in selected_ids:
                db.delete_venue(vid)
            st.success(f"Eliminate {len(selected_ids)} venue.")
            st.rerun()


# Render colonne
visible_states = [s for s in pipeline.PIPELINE_STATES if s in visible]
if not visible_states:
    st.info("Nessuna colonna selezionata. Scegli almeno uno stato sopra.")
    st.stop()

cols = st.columns(len(visible_states))
for col, state in zip(cols, visible_states):
    with col:
        emoji = pipeline.PIPELINE_EMOJI[state]
        label = pipeline.PIPELINE_LABELS[state].split(" ", 1)[-1]
        venues_in_col = by_state.get(state, [])
        st.markdown(
            f'<div class="kanban-col-header">{emoji} {label} '
            f'<span style="opacity:0.6;font-weight:400">({len(venues_in_col)})</span></div>',
            unsafe_allow_html=True,
        )
        for v in venues_in_col:
            title = _escape(v["name"])
            meta_parts = []
            if v.get("type"):
                meta_parts.append(_escape(v["type"]))
            if v.get("city"):
                meta_parts.append(_escape(v["city"]))
            meta = " · ".join(meta_parts) if meta_parts else "&nbsp;"
            deadline_html = ""
            if v.get("deadline_text"):
                deadline_html = (
                    f'<div class="venue-card-deadline">⏰ {_escape(v["deadline_text"])}</div>'
                )
            score = v.get("acceptance_score")
            if score in (1, 2, 3):
                score_class = f"score-{score}"
                score_text = str(score)
            else:
                score_class = "score-unknown"
                score_text = "?"
            score_html = f'<div class="venue-card-score {score_class}">{score_text}</div>'
            extra_class = " venue-card-pending-draft" if v["id"] in pending_draft_venue_ids else ""
            st.markdown(
                f'<div class="venue-card venue-card-{state}{extra_class}">'
                f'{score_html}'
                f'<div class="venue-card-title">{title}</div>'
                f'<div class="venue-card-meta">{meta}</div>'
                f'{deadline_html}'
                f'</div>',
                unsafe_allow_html=True,
            )
            bcol_chat, bcol_del = st.columns([5, 1])
            if bcol_chat.button("💬 Chat", key=f"chat_{v['id']}", use_container_width=True):
                st.session_state["draft_venue_id"] = v["id"]
                st.switch_page("pages/3_Outreach.py")
            if bcol_del.button("🗑", key=f"del_card_{v['id']}", help="Elimina venue", use_container_width=True):
                db.delete_venue(v["id"])
                st.rerun()
