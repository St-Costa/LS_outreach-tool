"""Enti — CRUD organizzazioni (associazioni, aziende, network) che raggruppano le venue."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import db, pipeline, ui

st.set_page_config(page_title="Enti", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Enti")
st.caption(
    "Organizzazioni madre delle venue (es. Distretto Rotary, ateneo, network di hub). "
    "Una venue può appartenere a un Ente; un Ente raggruppa più venue e può avere propri contatti."
)

# Permette navigazione cross-page (es. badge da pagina Venue)
preselected_id = st.session_state.pop("selected_organizer_id", None)


# ---------------- Filtri lista ----------------

f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
with f1:
    f_search = st.text_input("Cerca", placeholder="nome, descrizione, note...")
with f2:
    f_type = st.selectbox("Tipo", options=[""] + db.ORGANIZER_TYPES, format_func=lambda v: "(tutti)" if not v else v)
with f3:
    f_region = st.text_input("Regione")
with f4:
    f_lang = st.selectbox("Lingua", options=["", "IT", "EN", "DE", "IT/DE"], format_func=lambda v: "(tutte)" if not v else v)

filters: dict = {}
if f_search:
    filters["search"] = f_search
if f_type:
    filters["type"] = f_type
if f_region:
    filters["region"] = f_region
if f_lang:
    filters["language"] = f_lang

organizers = db.list_organizers(filters)
venues_count_by_org = db.count_venues_by_organizer()
st.write(f"**{len(organizers)}** enti trovati")

if organizers:
    rows = []
    for o in organizers:
        n_contacts = len(db.get_contacts_for_organizer(o["id"]))
        rows.append({
            "id": o["id"],
            "Nome": o["name"],
            "Tipo": o.get("type") or "",
            "HQ": o.get("hq_city") or "",
            "Regione": o.get("region") or "",
            "Lingua": o.get("language") or "",
            "N. venue": venues_count_by_org.get(o["id"], 0),
            "N. contatti": n_contacts,
        })
    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)

    options = [None] + [o["id"] for o in organizers]
    default_idx = options.index(preselected_id) if preselected_id in options else 0
    selected_id = st.selectbox(
        "Seleziona Ente per dettaglio",
        options=options,
        index=default_idx,
        format_func=lambda i: "—" if i is None else next(r["Nome"] for r in rows if r["id"] == i),
    )
else:
    selected_id = None
    st.info("Nessun Ente. Creane uno qui sotto.")

st.divider()


# ---------------- Crea nuovo ----------------

with st.expander("Crea nuovo Ente"):
    with st.form("new_organizer_form"):
        c1, c2 = st.columns(2)
        no_name = c1.text_input("Nome *")
        no_type = c2.selectbox("Tipo", options=[""] + db.ORGANIZER_TYPES)
        no_website = c1.text_input("Website")
        no_lang = c2.selectbox("Lingua", ["", "IT", "EN", "DE", "IT/DE"])
        no_hq_city = c1.text_input("HQ Città")
        no_hq_prov = c2.text_input("HQ Provincia")
        no_region = c1.text_input("Regione")
        no_li = c2.text_input("LinkedIn")
        no_ig = c1.text_input("Instagram")
        no_fb = c2.text_input("Facebook")
        no_desc = st.text_area("Descrizione", height=100)
        no_notes = st.text_area("Note", height=80)
        if st.form_submit_button("Crea"):
            if not (no_name or "").strip():
                st.error("Nome obbligatorio.")
            else:
                new_id = db.insert_organizer({
                    "name": no_name.strip(),
                    "type": no_type or None,
                    "website": no_website or None,
                    "language": no_lang or None,
                    "hq_city": no_hq_city or None,
                    "hq_province": no_hq_prov or None,
                    "region": no_region or None,
                    "social_linkedin": no_li or None,
                    "social_instagram": no_ig or None,
                    "social_facebook": no_fb or None,
                    "description": no_desc or None,
                    "notes": no_notes or None,
                    "source": "manual",
                })
                st.session_state["selected_organizer_id"] = new_id
                st.success(f"Ente creato (id {new_id}).")
                st.rerun()


# ---------------- Detail view ----------------

if selected_id:
    org = db.get_organizer(selected_id)
    if not org:
        st.error("Ente non trovato.")
    else:
        st.subheader(org["name"])
        meta_bits = []
        if org.get("type"):
            meta_bits.append(f"**{org['type']}**")
        if org.get("hq_city"):
            meta_bits.append(org["hq_city"])
        if org.get("region"):
            meta_bits.append(org["region"])
        if org.get("website"):
            meta_bits.append(f"[sito]({org['website']})")
        if meta_bits:
            st.markdown(" · ".join(meta_bits))

        # Stats aggregate per pipeline
        org_venues = db.get_venues_for_organizer(org["id"])
        n_total = len(org_venues)
        if n_total:
            counter: dict[str, int] = {}
            for v in org_venues:
                state = pipeline.normalize_state(v.get("pipeline_status"))
                counter[state] = counter.get(state, 0) + 1
            stat_cols = st.columns(len(pipeline.PIPELINE_STATES) + 1)
            stat_cols[0].metric("Venue totali", n_total)
            for i, st_name in enumerate(pipeline.PIPELINE_STATES):
                lbl = pipeline.PIPELINE_LABELS.get(st_name, st_name)
                stat_cols[i + 1].metric(lbl, counter.get(st_name, 0))
        else:
            st.caption("Nessuna venue collegata a questo Ente.")

        # Eliminazione
        if st.button("Elimina Ente", key="btn_del_org"):
            st.session_state["confirm_del_org"] = org["id"]
        if st.session_state.get("confirm_del_org") == org["id"]:
            cc1, cc2 = st.columns([1, 5])
            cc1.warning("Confermare?")
            cc2.caption("Le venue collegate restano (perdono solo il legame). I contatti restano.")
            if cc2.button("Sì, elimina"):
                db.delete_organizer(org["id"])
                st.session_state.pop("confirm_del_org", None)
                st.session_state.pop("selected_organizer_id", None)
                st.rerun()

        # Form edit
        with st.form(f"edit_organizer_{org['id']}"):
            c1, c2 = st.columns(2)
            eo_name = c1.text_input("Nome *", value=org.get("name") or "")
            type_options = [""] + db.ORGANIZER_TYPES
            current_type = org.get("type") or ""
            eo_type = c2.selectbox(
                "Tipo",
                options=type_options,
                index=type_options.index(current_type) if current_type in type_options else 0,
            )
            eo_website = c1.text_input("Website", value=org.get("website") or "")
            lang_options = ["", "IT", "EN", "DE", "IT/DE"]
            current_lang = org.get("language") or ""
            eo_lang = c2.selectbox(
                "Lingua",
                lang_options,
                index=lang_options.index(current_lang) if current_lang in lang_options else 0,
            )
            eo_hq_city = c1.text_input("HQ Città", value=org.get("hq_city") or "")
            eo_hq_prov = c2.text_input("HQ Provincia", value=org.get("hq_province") or "")
            eo_region = c1.text_input("Regione", value=org.get("region") or "")
            eo_li = c2.text_input("LinkedIn", value=org.get("social_linkedin") or "")
            eo_ig = c1.text_input("Instagram", value=org.get("social_instagram") or "")
            eo_fb = c2.text_input("Facebook", value=org.get("social_facebook") or "")
            eo_desc = st.text_area("Descrizione", value=org.get("description") or "", height=120)
            eo_notes = st.text_area("Note", value=org.get("notes") or "", height=100)
            if st.form_submit_button("Salva"):
                if not eo_name.strip():
                    st.error("Nome obbligatorio.")
                else:
                    db.update_organizer(org["id"], {
                        "name": eo_name.strip(),
                        "type": eo_type or None,
                        "website": eo_website or None,
                        "language": eo_lang or None,
                        "hq_city": eo_hq_city or None,
                        "hq_province": eo_hq_prov or None,
                        "region": eo_region or None,
                        "social_linkedin": eo_li or None,
                        "social_instagram": eo_ig or None,
                        "social_facebook": eo_fb or None,
                        "description": eo_desc or None,
                        "notes": eo_notes or None,
                    })
                    st.success("Salvato.")
                    st.rerun()

        # Venue dell'Ente
        st.subheader(f"Venue dell'Ente ({n_total})")
        if org_venues:
            for v in org_venues:
                vc1, vc2, vc3 = st.columns([5, 2, 1])
                state_lbl = pipeline.label(v.get("pipeline_status"), pipeline.PIPELINE_LABELS)
                vc1.write(f"**{v['name']}**")
                vc2.caption(f"{v.get('city','')} · {state_lbl}")
                if vc3.button("Apri", key=f"open_venue_{v['id']}"):
                    st.session_state["venue_edit_id"] = v["id"]
                    st.switch_page("pages/1_Venue.py")
        else:
            st.caption("Nessuna venue collegata.")

        # Bulk-assign orphan venues
        with st.expander("Assegna venue orfane a questo Ente"):
            orphan_filters: dict = {}
            if org.get("region"):
                orphan_filters["region"] = org["region"]
            orphans = db.list_orphan_venues(orphan_filters)
            st.caption(
                f"Mostrando {len(orphans)} venue senza Ente"
                + (f" (filtrate per regione = {org['region']})" if org.get("region") else "")
                + ". Cambia il filtro qui sotto per allargare/restringere."
            )
            f_oc1, f_oc2 = st.columns(2)
            override_region = f_oc1.text_input(
                "Filtro regione (vuoto = tutte)",
                value=org.get("region") or "",
                key=f"orphan_region_{org['id']}",
            )
            search_orphan = f_oc2.text_input(
                "Cerca nel nome",
                key=f"orphan_search_{org['id']}",
            )
            new_filters: dict = {}
            if override_region:
                new_filters["region"] = override_region
            if search_orphan:
                new_filters["search"] = search_orphan
            orphans = db.list_orphan_venues(new_filters)
            if orphans:
                pick = st.multiselect(
                    "Venue da assegnare",
                    options=[v["id"] for v in orphans],
                    format_func=lambda i: next(
                        f"{v['name']} ({v.get('city','-')})" for v in orphans if v["id"] == i
                    ),
                    key=f"bulk_pick_{org['id']}",
                )
                if st.button(f"Assegna {len(pick)} venue", key=f"bulk_btn_{org['id']}", disabled=not pick):
                    for vid in pick:
                        db.set_venue_organizer(vid, org["id"])
                    st.success(f"Assegnate {len(pick)} venue a {org['name']}.")
                    st.rerun()
            else:
                st.caption("Nessuna venue orfana che corrisponde al filtro.")

        # Contatti dell'Ente
        st.subheader("Contatti dell'Ente")
        linked_contacts = db.get_contacts_for_organizer(org["id"])
        if linked_contacts:
            for c in linked_contacts:
                cc1, cc2 = st.columns([5, 1])
                full = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip() or "(senza nome)"
                cc1.write(f"**{full}** — {c.get('role','')} · {c.get('email','')}")
                if cc2.button("Scollega", key=f"unlink_oc_{c['id']}"):
                    db.unlink_organizer_contact(org["id"], c["id"])
                    st.rerun()
        else:
            st.caption("Nessun contatto collegato.")

        with st.expander("Collega un contatto"):
            avail_contacts = [
                c for c in db.list_contacts()
                if c["id"] not in {x["id"] for x in linked_contacts}
            ]
            if avail_contacts:
                pick_c = st.selectbox(
                    "Contatto",
                    options=[c["id"] for c in avail_contacts],
                    format_func=lambda i: next(
                        " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip()
                        + (f" — {c.get('role','')}" if c.get("role") else "")
                        for c in avail_contacts if c["id"] == i
                    ),
                    key=f"link_oc_pick_{org['id']}",
                )
                if st.button("Collega", key=f"link_oc_btn_{org['id']}"):
                    db.link_organizer_contact(org["id"], pick_c)
                    st.rerun()
            else:
                st.caption("Nessun contatto disponibile da collegare.")
