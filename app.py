"""Outreach Intelligence Tool — Home."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from lib import db, importer, pipeline, ui
from lib.batch_followup_ui import render_batch_button
from lib.settings import has_api_key

st.set_page_config(
    page_title="Outreach Tool — Luca & Stefano",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

ui.apply_global_style()
db.init_db()


# Cache con TTL breve: evita ricaricare l'intero set di venue/interactions ad ogni
# rerun (cambio filtri, click bottone). Bottone "Aggiorna" in cima alla pagina svuota la cache.
@st.cache_data(ttl=30)
def _cached_venues() -> list[dict]:
    return db.list_venues()


@st.cache_data(ttl=30)
def _cached_last_interactions() -> dict:
    return db.get_last_interaction_per_venue()


@st.cache_data(ttl=30)
def _cached_last_outgoing(venue_id: int) -> dict | None:
    return db.get_last_outgoing_interaction(venue_id)


def _kpi_row(venues: list[dict], last_int_map: dict) -> None:
    counts: dict[str, int] = {s: 0 for s in pipeline.PIPELINE_STATES}
    for v in venues:
        eff = pipeline.derive_effective_state(v.get("pipeline_status"), last_int_map.get(v["id"]))
        counts[eff] = counts.get(eff, 0) + 1
    cols = st.columns(len(pipeline.PIPELINE_STATES))
    for col, status in zip(cols, pipeline.PIPELINE_STATES):
        col.metric(pipeline.PIPELINE_EMOJI[status], counts.get(status, 0))


def _compute_overdue(venues: list[dict]) -> list[tuple]:
    """Lista (days_since, venue, last_outgoing) per le venue 'contattata' con
    ultimo invio ≥ 7 giorni fa. Ordinate dalla più vecchia."""
    from datetime import datetime
    today = date.today()
    out = []
    for v in venues:
        if v.get("pipeline_status") not in ("contattata",):
            continue
        last = _cached_last_outgoing(v["id"])
        if not last:
            continue
        try:
            occurred_at = last.get("occurred_at")
            if isinstance(occurred_at, str):
                occurred_at = datetime.fromisoformat(occurred_at)
            days_since = (today - occurred_at.date()).days
            if days_since >= 7:
                out.append((days_since, v, last))
        except Exception:
            continue
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def _due_actions(venues: list[dict]) -> None:
    today = date.today()
    threshold_soon = today + timedelta(days=7)

    deadline_soon = []
    for v in venues:
        d = v.get("deadline_date")
        if not d:
            continue
        try:
            if isinstance(d, str):
                d = date.fromisoformat(d)
            if today <= d <= threshold_soon:
                deadline_soon.append((d, v))
        except Exception:
            continue
    deadline_soon.sort(key=lambda t: t[0])

    overdue_followups = _compute_overdue(venues)

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Deadline ravvicinate (7 giorni)")
        if deadline_soon:
            for d, v in deadline_soon:
                st.write(f"**{d.isoformat()}** — {v['name']} ({v.get('city','-')}) · {v.get('deadline_text','')}")
        else:
            st.caption("Nessuna deadline nei prossimi 7 giorni.")

    with col2:
        st.subheader("Follow-up dovuti (≥ 7 giorni dall'invio)")
        if overdue_followups:
            for days, v, last in overdue_followups[:15]:
                st.write(f"**{days} giorni fa** — {v['name']} · ultimo invio: {last.get('subject','(no oggetto)')}")
        else:
            st.caption("Nessun follow-up dovuto al momento.")


def main():
    col_title, col_refresh = st.columns([6, 1])
    with col_title:
        st.title("Outreach Intelligence Tool")
        st.caption("Sistema per gestire outreach B2B di Luca Nesler e Stefano come speaker/formatori.")
    with col_refresh:
        if st.button("↻ Aggiorna", help="Svuota la cache (KPI/follow-up/venue) e rilegge dal DB."):
            st.cache_data.clear()
            st.rerun()

    if not has_api_key():
        st.warning(
            "API key Anthropic non configurata. La generazione draft, l'analisi risposte e la "
            "discovery non funzioneranno. Vai su **Impostazioni** per inserirla."
        )

    venues = _cached_venues()
    last_int_map = _cached_last_interactions()

    # Banner follow-up dovuti: severità per giorni di ritardo (≥14 = error, 7-13 = warning)
    overdue = _compute_overdue(venues)
    if overdue:
        render_batch_button(key_prefix="home")
    if overdue:
        critical = [t for t in overdue if t[0] >= 14]
        oldest_days, oldest_venue, _ = overdue[0]
        bcol1, bcol2 = st.columns([5, 1])
        with bcol1:
            msg = (
                f"**{len(overdue)} follow-up dovuti** "
                f"({len(critical)} critici ≥14gg). Più vecchio: "
                f"_{oldest_venue['name']}_ — **{oldest_days} giorni** dall'ultimo invio."
            )
            if critical:
                st.error(msg)
            else:
                st.warning(msg)
        with bcol2:
            if st.button("Apri il più vecchio", key="btn_open_oldest_overdue", use_container_width=True):
                st.session_state["draft_venue_id"] = oldest_venue["id"]
                st.switch_page("pages/3_Outreach.py")

    base = Path(__file__).resolve().parent
    if importer.find_default_files(base) and len(venues) == 0:
        st.info(
            "I file sorgente `vanue 1.md` / `vanue 2.md` sono presenti ma il database è vuoto. "
            "Vai su **Impostazioni → Importa venue iniziali**."
        )

    st.divider()
    st.subheader("Pipeline")
    _kpi_row(venues, last_int_map)

    st.divider()
    _due_actions(venues)


if __name__ == "__main__":
    main()
