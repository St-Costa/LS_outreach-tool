"""Statistiche — KPI per speaker (Luca / Stefano / entrambi) e pipeline."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

from lib import db, pipeline, ui

st.set_page_config(page_title="Statistiche", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Statistiche")
st.caption(
    "KPI aggregati su pipeline, draft e speaker. Lo `speaker_choice` viene "
    "registrato sulle interazioni a partire dall'introduzione di questa pagina: "
    "le interazioni precedenti hanno valore mancante e contribuiscono a «(non specificato)»."
)


@st.cache_data(ttl=30)
def _all_interactions() -> list[dict]:
    return db.list_interactions({}, limit=100000)


interactions = _all_interactions()

# ----- KPI globali -----
n_total = len(interactions)
n_outgoing = sum(1 for i in interactions if i["direction"] == "inviata" and not i.get("is_draft"))
n_drafts = sum(1 for i in interactions if i["direction"] == "inviata" and i.get("is_draft"))
n_incoming = sum(1 for i in interactions if i["direction"] == "ricevuta")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Interazioni totali", n_total)
k2.metric("Inviate (confermate)", n_outgoing)
k3.metric("Draft pending", n_drafts)
k4.metric("Ricevute", n_incoming)

st.divider()

# ----- Per speaker -----
st.subheader("Per speaker")

by_speaker: dict[str, dict] = defaultdict(lambda: {"sent": 0, "draft": 0})
for it in interactions:
    if it["direction"] != "inviata":
        continue
    sp = it.get("speaker_choice") or "(non specificato)"
    if it.get("is_draft"):
        by_speaker[sp]["draft"] += 1
    else:
        by_speaker[sp]["sent"] += 1

if not by_speaker:
    st.info("Nessuna mail inviata ancora.")
else:
    rows = [
        {"Speaker": sp, "Inviate": d["sent"], "Draft pending": d["draft"], "Totale": d["sent"] + d["draft"]}
        for sp, d in by_speaker.items()
    ]
    df_sp = pd.DataFrame(rows).sort_values("Totale", ascending=False)
    st.dataframe(df_sp, hide_index=True, use_container_width=True)

st.divider()

# ----- Per stato pipeline -----
st.subheader("Pipeline corrente")

venues = db.list_venues()
last_int_map = db.get_last_interaction_per_venue()
state_counts: Counter = Counter()
for v in venues:
    eff = pipeline.derive_effective_state(v.get("pipeline_status"), last_int_map.get(v["id"]))
    state_counts[eff] += 1

pipe_rows = [
    {
        "Stato": pipeline.PIPELINE_LABELS.get(s, s),
        "N venue": state_counts.get(s, 0),
    }
    for s in pipeline.PIPELINE_STATES
]
st.dataframe(pd.DataFrame(pipe_rows), hide_index=True, use_container_width=True)

st.divider()

# ----- Tempo medio di risposta -----
st.subheader("Tempo medio di risposta (per venue)")
st.caption(
    "Per ogni venue: differenza tra prima 'inviata' confermata e prima 'ricevuta' successiva. "
    "Se la venue non ha mai risposto, viene esclusa dal calcolo medio."
)

# Raggruppa per venue
by_venue: dict[int, list[dict]] = defaultdict(list)
for it in interactions:
    if it.get("venue_id"):
        by_venue[it["venue_id"]].append(it)

deltas_days: list[float] = []
no_response = 0
for vid, ints in by_venue.items():
    ints_sorted = sorted(ints, key=lambda x: x["occurred_at"] or "")
    first_sent = next(
        (i for i in ints_sorted if i["direction"] == "inviata" and not i.get("is_draft")),
        None,
    )
    if not first_sent:
        continue
    response = next(
        (
            i for i in ints_sorted
            if i["direction"] == "ricevuta"
            and (i["occurred_at"] or "") > (first_sent["occurred_at"] or "")
        ),
        None,
    )
    if response:
        try:
            t_sent = datetime.fromisoformat(first_sent["occurred_at"])
            t_resp = datetime.fromisoformat(response["occurred_at"])
            deltas_days.append((t_resp - t_sent).total_seconds() / 86400)
        except Exception:
            pass
    else:
        no_response += 1

if deltas_days:
    avg = sum(deltas_days) / len(deltas_days)
    median = sorted(deltas_days)[len(deltas_days) // 2]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Venue con risposta", len(deltas_days))
    c2.metric("Venue senza risposta", no_response)
    c3.metric("Media giorni", f"{avg:.1f}")
    c4.metric("Mediana giorni", f"{median:.1f}")
    response_rate = len(deltas_days) / (len(deltas_days) + no_response) * 100
    st.caption(f"Tasso di risposta: **{response_rate:.1f}%**")
else:
    st.info("Nessuna risposta tracciata ancora.")
