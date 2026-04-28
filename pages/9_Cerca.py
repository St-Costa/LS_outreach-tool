"""Cerca — ricerca globale tra venue, contatti, interazioni, enti."""
from __future__ import annotations

import streamlit as st

from lib import db, ui

st.set_page_config(page_title="Cerca", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Cerca")
st.caption(
    "Ricerca testuale (LIKE case-insensitive) su nomi, note, email, oggetti/corpi mail, "
    "descrizioni Ente. Limite 30 risultati per categoria."
)

q = st.text_input(
    "Query",
    value=st.session_state.get("search_q", ""),
    placeholder="parola, email, città, frammento di testo…",
    key="search_q",
)

if not q:
    st.info("Inserisci almeno una parola.")
    st.stop()

results = db.global_search(q, limit_per_table=30)

n_v = len(results["venues"])
n_c = len(results["contacts"])
n_i = len(results["interactions"])
n_o = len(results["organizers"])
total = n_v + n_c + n_i + n_o
st.write(f"**{total}** risultati (venue: {n_v} · contatti: {n_c} · interazioni: {n_i} · enti: {n_o})")

if total == 0:
    st.warning("Nessun risultato.")
    st.stop()

tab_v, tab_c, tab_i, tab_o = st.tabs([
    f"Venue ({n_v})",
    f"Contatti ({n_c})",
    f"Interazioni ({n_i})",
    f"Enti ({n_o})",
])

with tab_v:
    if not results["venues"]:
        st.caption("—")
    for v in results["venues"]:
        col_l, col_r = st.columns([5, 1])
        col_l.markdown(
            f"**{v['name']}** · {v.get('type','') or '—'} · "
            f"{v.get('city','') or '—'} · stato: {v.get('pipeline_status','—')}"
        )
        if col_r.button("Apri", key=f"open_v_{v['id']}", use_container_width=True):
            st.session_state["draft_venue_id"] = v["id"]
            st.session_state["return_to"] = {"page": "pages/9_Cerca.py", "label": "Cerca"}
            st.switch_page("pages/3_Outreach.py")
        if v.get("description"):
            st.caption(v["description"][:200])
        if v.get("notes"):
            st.caption(f"Note: {v['notes'][:200]}")
        st.divider()

with tab_c:
    if not results["contacts"]:
        st.caption("—")
    for c in results["contacts"]:
        full = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip() or "(senza nome)"
        st.markdown(
            f"**{full}** · {c.get('role','') or '—'} · "
            f"`{c.get('email','') or '—'}` · {c.get('phone','') or ''}"
        )
        if c.get("notes"):
            st.caption(c["notes"][:200])
        venues_linked = db.get_venues_for_contact(c["id"])
        if venues_linked:
            st.caption("Venue: " + ", ".join(v["name"] for v in venues_linked))
        st.divider()

with tab_i:
    if not results["interactions"]:
        st.caption("—")
    for it in results["interactions"]:
        v_name = "—"
        if it.get("venue_id"):
            vv = db.get_venue(it["venue_id"])
            if vv:
                v_name = vv["name"]
        st.markdown(
            f"`{it.get('occurred_at','')}` · **{it.get('direction','')}** · "
            f"{it.get('channel','')} · venue: _{v_name}_"
        )
        if it.get("subject"):
            st.markdown(f"**{it['subject']}**")
        st.text((it.get("content") or "")[:500] + ("..." if it.get("content") and len(it["content"]) > 500 else ""))
        st.divider()

with tab_o:
    if not results["organizers"]:
        st.caption("—")
    for o in results["organizers"]:
        st.markdown(f"**{o['name']}** · {o.get('type','') or '—'} · {o.get('region','') or ''}")
        if o.get("description"):
            st.caption(o["description"][:200])
        st.divider()
