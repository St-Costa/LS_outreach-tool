"""Stili globali UI iniettati da ogni pagina Streamlit.

Contiene il CSS globale: sidebar più stretta, layout compatto, header toolbar
ridotta, e la regola che **nasconde la pagina Outreach dal menu sidebar**
(accessibile solo via card "💬 Chat" da `1_Venue.py` settando
`st.session_state["draft_venue_id"]`).
"""
from __future__ import annotations

import streamlit as st


_GLOBAL_CSS = """
<style>
/* Sidebar più stretta */
[data-testid="stSidebar"] {
    min-width: 180px !important;
    max-width: 220px !important;
    width: 200px !important;
}
[data-testid="stSidebar"] > div:first-child {
    width: 200px !important;
}

/* Compatta lo spazio verticale in alto sul main e sulla sidebar */
.main .block-container,
[data-testid="stMain"] .block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
}
[data-testid="stSidebarUserContent"] {
    padding-top: 0.5rem !important;
}
[data-testid="stSidebarNav"] {
    padding-top: 0.25rem !important;
    padding-bottom: 0.25rem !important;
}
[data-testid="stSidebarNavItems"] {
    padding-top: 0 !important;
}
/* Nascondi la pagina Outreach dal menu — accessibile solo via "💬 Chat" dalle card Venue */
[data-testid="stSidebarNav"] a[href$="/Outreach"],
[data-testid="stSidebarNav"] a[href*="/Outreach"] {
    display: none !important;
}
/* Riduce lo spazio sopra al titolo di pagina */
[data-testid="stMain"] h1:first-child,
[data-testid="stMain"] h2:first-child {
    margin-top: 0 !important;
    padding-top: 0 !important;
}
/* Toolbar Streamlit (decorations) → riduci altezza */
[data-testid="stHeader"] {
    height: 0 !important;
    background: transparent !important;
}
[data-testid="stDecoration"] {
    display: none !important;
}
</style>
"""


def apply_global_style() -> None:
    """Inietta il CSS globale. Chiamare in cima ad ogni pagina dopo `st.set_page_config()`."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


def confirm_destructive(
    label: str,
    confirm_message: str,
    state_key: str,
    *,
    button_kwargs: dict | None = None,
) -> bool:
    """Pattern uniforme per delete/clear con conferma a 2 step.

    Usage:
        if ui.confirm_destructive("Elimina venue", "Verranno rimosse anche le interazioni.", f"del_venue_{vid}"):
            db.delete_venue(vid); st.rerun()

    Comportamento: primo click → mostra warning + bottoni 'Sì, conferma' / 'Annulla'.
    Ritorna True solo quando l'utente clicca 'Sì, conferma'. Lo stato è memorizzato
    in `st.session_state[state_key]` (auto-cleanup su Annulla).
    """
    button_kwargs = button_kwargs or {}
    armed = st.session_state.get(state_key) is True

    if not armed:
        if st.button(label, key=f"_btn_arm_{state_key}", **button_kwargs):
            st.session_state[state_key] = True
            st.rerun()
        return False

    st.warning(confirm_message)
    cc1, cc2 = st.columns([1, 5])
    confirmed = cc1.button("Sì, conferma", key=f"_btn_yes_{state_key}", type="primary")
    cancelled = cc2.button("Annulla", key=f"_btn_no_{state_key}")
    if confirmed:
        st.session_state.pop(state_key, None)
        return True
    if cancelled:
        st.session_state.pop(state_key, None)
        st.rerun()
    return False


def _ics_escape(s: str) -> str:
    """Escape minimo per testo in proprietà ICS (RFC 5545)."""
    if not s:
        return ""
    return (
        s.replace("\\", "\\\\")
         .replace(",", "\\,")
         .replace(";", "\\;")
         .replace("\r\n", "\\n")
         .replace("\n", "\\n")
    )


def venues_to_ics(venues: list[dict]) -> bytes:
    """Genera un file ICS (VCALENDAR) con un VEVENT all-day per ogni venue
    che ha `deadline_date` valorizzata. Compatibile Google Calendar/Apple/Outlook.
    """
    from datetime import datetime, date as _date

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Outreach Tool//Luca & Stefano//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for v in venues:
        d = v.get("deadline_date")
        if not d:
            continue
        try:
            if isinstance(d, str):
                d = _date.fromisoformat(d)
        except ValueError:
            continue
        dstart = d.strftime("%Y%m%d")
        # All-day: DTEND esclusivo = giorno successivo
        from datetime import timedelta as _td
        dend = (d + _td(days=1)).strftime("%Y%m%d")
        summary = f"Deadline: {v.get('name', '?')}"
        descr_bits = []
        if v.get("deadline_text"):
            descr_bits.append(v["deadline_text"])
        if v.get("city"):
            descr_bits.append(f"Città: {v['city']}")
        if v.get("website"):
            descr_bits.append(f"Sito: {v['website']}")
        descr = " — ".join(descr_bits)
        uid = f"venue-{v.get('id', 'x')}-{dstart}@outreach-tool"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{dstart}",
            f"DTEND;VALUE=DATE:{dend}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(descr)}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def rows_to_csv_bytes(rows: list[dict], columns: list[str] | None = None) -> bytes:
    """Serializza una lista di dict in CSV UTF-8 con BOM (compatibile con Excel IT).

    `columns` opzionale: se passato, restringe e ordina le colonne.
    Se assente, usa l'unione ordinata delle chiavi in `rows` (preservando l'ordine
    di prima apparizione).
    """
    import csv as _csv
    import io as _io

    if not rows:
        return "﻿".encode("utf-8")

    if columns is None:
        seen: dict[str, None] = {}
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen[k] = None
        columns = list(seen.keys())

    buf = _io.StringIO()
    buf.write("﻿")  # BOM per Excel su Windows
    writer = _csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        # Converte None → "" e dict/list → JSON per serializzazione safe
        flat = {}
        for k in columns:
            v = r.get(k)
            if v is None:
                flat[k] = ""
            elif isinstance(v, (dict, list)):
                import json as _json
                flat[k] = _json.dumps(v, ensure_ascii=False)
            else:
                flat[k] = v
        writer.writerow(flat)
    return buf.getvalue().encode("utf-8")
