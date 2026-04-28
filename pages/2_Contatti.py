"""Contatti — CRUD persone collegate alle venue."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import db, pipeline, ui

st.set_page_config(page_title="Contatti", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Contatti")
st.caption("Persone con cui si comunica. Un contatto può essere collegato a più venue.")


col1, col2 = st.columns(2)
with col1:
    f_search = st.text_input("Cerca", placeholder="nome, email, ruolo...")
with col2:
    venues_all = db.list_venues()
    venue_options = [None] + [v["id"] for v in venues_all]
    f_venue = st.selectbox(
        "Filtro venue",
        options=venue_options,
        format_func=lambda i: "(tutte)" if i is None else next(v["name"] for v in venues_all if v["id"] == i),
    )

filters: dict = {}
if f_search:
    filters["search"] = f_search
if f_venue:
    filters["venue_id"] = f_venue

contacts = db.list_contacts(filters)
header_l, header_r = st.columns([5, 1])
header_l.write(f"**{len(contacts)}** contatti trovati")
header_r.download_button(
    "⬇ CSV contatti",
    data=ui.rows_to_csv_bytes(
        [
            {
                "id": c["id"],
                "first_name": c.get("first_name"),
                "last_name": c.get("last_name"),
                "role": c.get("role"),
                "email": c.get("email"),
                "phone": c.get("phone"),
                "language_pref": c.get("language_pref"),
                "social_linkedin": c.get("social_linkedin"),
                "social_instagram": c.get("social_instagram"),
                "suggested_tone": c.get("suggested_tone"),
                "notes": c.get("notes"),
                "venues": ", ".join(v["name"] for v in db.get_venues_for_contact(c["id"])),
            }
            for c in contacts
        ],
        columns=[
            "id", "first_name", "last_name", "role", "email", "phone",
            "language_pref", "social_linkedin", "social_instagram",
            "suggested_tone", "notes", "venues",
        ],
    ),
    file_name="contatti.csv",
    mime="text/csv",
    use_container_width=True,
    help="Esporta i contatti correnti (filtri applicati) in CSV.",
)

if contacts:
    rows = []
    for c in contacts:
        venues = db.get_venues_for_contact(c["id"])
        rows.append({
            "id": c["id"],
            "Nome": " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip() or "(senza nome)",
            "Ruolo": c.get("role") or "",
            "Email": c.get("email") or "",
            "Lingua": c.get("language_pref") or "",
            "Venue collegate": ", ".join(v["name"] for v in venues) or "—",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)
    selected_id = st.selectbox(
        "Seleziona contatto per dettaglio",
        options=[None] + [c["id"] for c in contacts],
        format_func=lambda i: "—" if i is None else next(
            r["Nome"] for r in rows if r["id"] == i
        ),
    )
else:
    selected_id = None
    st.info("Nessun contatto.")

st.divider()


with st.expander("Aggiungi contatto"):
    with st.form("new_contact_form"):
        c1, c2 = st.columns(2)
        nc_first = c1.text_input("Nome")
        nc_last = c2.text_input("Cognome")
        nc_role = c1.text_input("Ruolo")
        nc_email = c2.text_input("Email")
        nc_phone = c1.text_input("Telefono")
        nc_lang = c2.selectbox("Lingua preferita", ["IT", "EN", "DE", "IT/DE"])
        nc_li = c1.text_input("LinkedIn")
        nc_ig = c2.text_input("Instagram")
        nc_tone = c1.selectbox("Tono consigliato", ["", "formale", "cordiale", "informale", "tecnico"])
        nc_notes = st.text_area("Note", height=120)
        if st.form_submit_button("Crea"):
            db.insert_contact({
                "first_name": nc_first or None,
                "last_name": nc_last or None,
                "role": nc_role or None,
                "email": nc_email or None,
                "phone": nc_phone or None,
                "language_pref": nc_lang,
                "social_linkedin": nc_li or None,
                "social_instagram": nc_ig or None,
                "suggested_tone": nc_tone or None,
                "notes": nc_notes or None,
            })
            st.success("Contatto creato.")
            st.rerun()


if selected_id:
    contact = db.get_contact(selected_id)
    if not contact:
        st.error("Non trovato.")
    else:
        full = " ".join(filter(None, [contact.get("first_name"), contact.get("last_name")])).strip() or "(senza nome)"
        st.subheader(full)

        if st.button("Elimina contatto", key="btn_del_contact"):
            st.session_state["confirm_del_contact"] = contact["id"]
        if st.session_state.get("confirm_del_contact") == contact["id"]:
            cc1, cc2 = st.columns([1, 5])
            cc1.warning("Confermare?")
            if cc2.button("Sì, elimina"):
                db.delete_contact(contact["id"])
                st.session_state.pop("confirm_del_contact", None)
                st.rerun()

        with st.form(f"edit_contact_{contact['id']}"):
            c1, c2 = st.columns(2)
            ec_first = c1.text_input("Nome", value=contact.get("first_name") or "")
            ec_last = c2.text_input("Cognome", value=contact.get("last_name") or "")
            ec_role = c1.text_input("Ruolo", value=contact.get("role") or "")
            ec_email = c2.text_input("Email", value=contact.get("email") or "")
            ec_phone = c1.text_input("Telefono", value=contact.get("phone") or "")
            lang_options = ["IT", "EN", "DE", "IT/DE"]
            ec_lang = c2.selectbox(
                "Lingua",
                lang_options,
                index=lang_options.index(contact.get("language_pref") or "IT") if (contact.get("language_pref") or "IT") in lang_options else 0,
            )
            ec_li = c1.text_input("LinkedIn", value=contact.get("social_linkedin") or "")
            ec_ig = c2.text_input("Instagram", value=contact.get("social_instagram") or "")
            tone_options = ["", "formale", "cordiale", "informale", "tecnico"]
            ec_tone = c1.selectbox(
                "Tono consigliato",
                tone_options,
                index=tone_options.index(contact.get("suggested_tone") or ""),
            )
            ec_notes = st.text_area("Note", value=contact.get("notes") or "", height=160)
            if st.form_submit_button("Salva"):
                db.update_contact(contact["id"], {
                    "first_name": ec_first or None,
                    "last_name": ec_last or None,
                    "role": ec_role or None,
                    "email": ec_email or None,
                    "phone": ec_phone or None,
                    "language_pref": ec_lang,
                    "social_linkedin": ec_li or None,
                    "social_instagram": ec_ig or None,
                    "suggested_tone": ec_tone or None,
                    "notes": ec_notes or None,
                })
                st.success("Salvato.")
                st.rerun()

        st.subheader("Venue collegate")
        linked = db.get_venues_for_contact(contact["id"])
        if linked:
            for v in linked:
                vc1, vc2 = st.columns([5, 1])
                vc1.write(f"**{v['name']}** — {v.get('city','')} · {pipeline.label(v.get('pipeline_status'), pipeline.PIPELINE_LABELS)}")
                if vc2.button("Scollega", key=f"unlink_v_{v['id']}"):
                    db.unlink_venue_contact(v["id"], contact["id"])
                    st.rerun()
        else:
            st.caption("Nessuna venue collegata.")

        with st.expander("Collega a una venue"):
            avail = [v for v in db.list_venues() if v["id"] not in {x["id"] for x in linked}]
            if avail:
                pick = st.selectbox(
                    "Venue",
                    options=[v["id"] for v in avail],
                    format_func=lambda i: next(v["name"] for v in avail if v["id"] == i),
                    key=f"link_pick_{contact['id']}",
                )
                if st.button("Collega", key=f"link_btn_{contact['id']}"):
                    db.link_venue_contact(pick, contact["id"])
                    st.rerun()
            else:
                st.caption("Nessuna venue disponibile da collegare.")

        st.subheader("Enti collegati")
        linked_orgs = db.get_organizers_for_contact(contact["id"])
        if linked_orgs:
            for o in linked_orgs:
                oc1, oc2 = st.columns([5, 1])
                meta = f" — {o['type']}" if o.get("type") else ""
                oc1.write(f"🏛 **{o['name']}**{meta}" + (f" · {o.get('hq_city')}" if o.get("hq_city") else ""))
                if oc2.button("Scollega", key=f"unlink_o_{o['id']}"):
                    db.unlink_organizer_contact(o["id"], contact["id"])
                    st.rerun()
        else:
            st.caption("Nessun Ente collegato.")

        with st.expander("Collega a un Ente"):
            avail_orgs = [o for o in db.list_organizers() if o["id"] not in {x["id"] for x in linked_orgs}]
            if avail_orgs:
                pick_o = st.selectbox(
                    "Ente",
                    options=[o["id"] for o in avail_orgs],
                    format_func=lambda i: next(
                        o["name"] + (f" ({o['type']})" if o.get("type") else "")
                        for o in avail_orgs if o["id"] == i
                    ),
                    key=f"link_o_pick_{contact['id']}",
                )
                if st.button("Collega Ente", key=f"link_o_btn_{contact['id']}"):
                    db.link_organizer_contact(pick_o, contact["id"])
                    st.rerun()
            else:
                st.caption("Nessun Ente disponibile da collegare.")

        st.subheader("Storico interazioni")
        ints = db.list_interactions({"contact_id": contact["id"]}, limit=50)
        if ints:
            for it in ints:
                with st.expander(f"{it.get('occurred_at')} · {pipeline.label(it.get('channel'), pipeline.CHANNEL_LABELS)} · {it.get('direction')}"):
                    if it.get("subject"):
                        st.markdown(f"**Oggetto:** {it['subject']}")
                    st.text(it.get("content") or "")
        else:
            st.caption("Nessuna interazione.")
