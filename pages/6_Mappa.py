"""Mappa — visualizzazione geografica delle venue."""
from __future__ import annotations

import time
from collections import Counter

import folium
import streamlit as st
from streamlit_folium import st_folium

from lib import db, geocode, pipeline, ui

st.set_page_config(page_title="Mappa", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Mappa venue")


@st.cache_data(ttl=30)
def _cached_venues() -> list[dict]:
    return db.list_venues()


@st.cache_data(ttl=30)
def _cached_last_interactions() -> dict:
    return db.get_last_interaction_per_venue()


# Auto-geocoding (cache hardcoded città/regioni, no rete) eseguito una sola volta
# per session: ad ogni rerun successivo skippiamo per non scrivere su DB ripetutamente.
if not st.session_state.get("mappa_autogeocoded"):
    auto_added = geocode.autocoord_all_venues()
    st.session_state["mappa_autogeocoded"] = True
    if auto_added:
        st.toast(f"Geocodificate {auto_added} venue al volo.")
        _cached_venues.clear()  # invalida cache: le coordinate sono cambiate


all_venues_cache = _cached_venues()

# ---------- Filtri ----------
col1, col2, col3 = st.columns(3)
with col1:
    types_all = sorted({v["type"] for v in all_venues_cache if v.get("type")})
    f_types = st.multiselect("Tipo", types_all, default=types_all)
with col2:
    f_statuses = st.multiselect(
        "Stato",
        pipeline.PIPELINE_STATES,
        default=pipeline.PIPELINE_STATES,
        format_func=lambda x: pipeline.PIPELINE_LABELS.get(x, x),
    )
with col3:
    f_angles = st.multiselect(
        "Angolo",
        pipeline.ANGLES,
        format_func=lambda x: pipeline.ANGLE_LABELS.get(x, x),
    )

venues = list(all_venues_cache)
last_int_map = _cached_last_interactions()

# Compute effective state per ogni venue (deriva da ultima interazione)
for v in venues:
    v["_effective_state"] = pipeline.derive_effective_state(
        v.get("pipeline_status"),
        last_int_map.get(v["id"]),
    )

if f_types:
    venues = [v for v in venues if v.get("type") in f_types]
if f_statuses:
    venues = [v for v in venues if v["_effective_state"] in f_statuses]
if f_angles:
    venues = [v for v in venues if v.get("angle") in f_angles]

with_coords = [v for v in venues if v.get("lat") and v.get("lon")]
without_coords = [v for v in venues if not v.get("lat") or not v.get("lon")]

st.write(f"**{len(with_coords)}** sulla mappa · **{len(without_coords)}** senza coordinate (né città né regione note)")


# ---------- Geocoding preciso (Nominatim) per le venue ancora senza coordinate ----------
if without_coords:
    n_to_geocode = min(len(without_coords), 30)
    with st.expander(f"Geocodifica precisa via OpenStreetMap ({n_to_geocode} venue)"):
        st.caption(
            "Prova a recuperare coordinate accurate via Nominatim. Lento (~1s per venue per rate limit). "
            "Le venue qui sotto non hanno né città né regione note: senza un'indicazione minima la geocodifica fallirà."
        )
        for v in without_coords[:10]:
            st.write(f"- {v['name']} (no city, no region)")
        if len(without_coords) > 10:
            st.caption(f"...e altre {len(without_coords) - 10}.")

        if st.button(f"Geocodifica precisa fino a {n_to_geocode}"):
            progress = st.progress(0)
            status = st.empty()
            successes = 0
            for i, v in enumerate(without_coords[:n_to_geocode]):
                status.text(f"Cerco coordinate per: {v['name']}...")
                coords = geocode.geocode_venue(v)
                if coords:
                    db.update_venue(v["id"], {"lat": coords[0], "lon": coords[1]})
                    successes += 1
                progress.progress((i + 1) / n_to_geocode)
            status.empty()
            _cached_venues.clear()
            st.success(f"Geocodificate {successes}/{n_to_geocode} venue.")
            time.sleep(1)
            st.rerun()


# ---------- Mappa ----------
if with_coords:
    avg_lat = sum(v["lat"] for v in with_coords) / len(with_coords)
    avg_lon = sum(v["lon"] for v in with_coords) / len(with_coords)

    m = folium.Map(location=[avg_lat, avg_lon], zoom_start=8, tiles="OpenStreetMap")

    for v in with_coords:
        state = v["_effective_state"]
        color = pipeline.PIPELINE_COLORS.get(state, "#666")
        emoji = pipeline.PIPELINE_EMOJI.get(state, "")
        popup_html = (
            f"<b>{v['name']}</b><br>"
            f"{v.get('type','')} · {v.get('city','') or v.get('region','')}<br>"
            f"Stato: {emoji} {pipeline.PIPELINE_LABELS.get(state, state).split(' ', 1)[-1]}<br>"
            f"Lingua: {v.get('language','')}<br>"
        )
        if v.get("email"):
            popup_html += f"Email: {v['email']}<br>"
        if v.get("description"):
            popup_html += f"<i>{v['description'][:200]}</i><br>"
        if v.get("deadline_text"):
            popup_html += f"<i>Deadline: {v['deadline_text']}</i><br>"
        if v.get("website"):
            popup_html += f'<a href="{v["website"]}" target="_blank">Sito</a><br>'

        if state == "ghostati":
            # Emoji 👻 come marker via DivIcon
            icon_html = (
                '<div style="font-size:24px;line-height:24px;'
                'text-align:center;text-shadow:0 1px 2px rgba(0,0,0,0.5)">👻</div>'
            )
            folium.Marker(
                location=[v["lat"], v["lon"]],
                icon=folium.DivIcon(
                    icon_size=(30, 30),
                    icon_anchor=(15, 15),
                    html=icon_html,
                ),
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{emoji} {v['name']}",
            ).add_to(m)
        else:
            folium.CircleMarker(
                location=[v["lat"], v["lon"]],
                radius=9,
                color="#000000",
                weight=1.5,
                fill=True,
                fill_color=color,
                fill_opacity=1.0,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{emoji} {v['name']}",
            ).add_to(m)

    st_folium(m, width=None, height=600, returned_objects=[])

    # Legenda
    st.subheader("Legenda")
    legend_cols = st.columns(len(pipeline.PIPELINE_STATES))
    for col, status in zip(legend_cols, pipeline.PIPELINE_STATES):
        color = pipeline.PIPELINE_COLORS[status]
        col.markdown(
            f"<span style='display:inline-block;width:14px;height:14px;background:{color};"
            f"border-radius:50%;margin-right:5px'></span> {pipeline.PIPELINE_LABELS[status]}",
            unsafe_allow_html=True,
        )

    st.caption(
        "Nota: i marker sono posizionati sulla città (con piccola dispersione casuale per evitare sovrapposizioni). "
        "Per coordinate accurate dell'indirizzo specifico, usa la geocodifica precisa qui sopra."
    )
else:
    st.info("Nessuna venue con coordinate. Le venue senza city né region non possono essere mappate automaticamente.")

# ---------- Distribuzione regionale ----------
st.divider()
st.subheader("Distribuzione regionale")
region_counts = Counter(v.get("region") or "(sconosciuta)" for v in all_venues_cache)
for region, count in region_counts.most_common():
    st.write(f"- **{region}**: {count}")
