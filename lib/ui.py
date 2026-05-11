"""Stili globali UI iniettati da ogni pagina Streamlit.

Contiene il CSS globale: sidebar più stretta, layout compatto, header toolbar
ridotta, e la regola che **nasconde la pagina Outreach dal menu sidebar**
(accessibile solo via card "💬 Chat" da `1_Venue.py` settando
`st.session_state["draft_venue_id"]`).
"""
from __future__ import annotations

import re

import streamlit as st


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def validate_email_or_blank(value: str) -> str | None:
    """Ritorna None se valido (anche se vuoto), altrimenti un messaggio d'errore in italiano."""
    v = (value or "").strip()
    if not v:
        return None
    if not _EMAIL_RE.match(v):
        return f"Email non valida: «{v}». Atteso formato `nome@dominio.tld`."
    return None


def validate_website_or_blank(value: str) -> str | None:
    """Ritorna None se valido (anche se vuoto), altrimenti un messaggio d'errore in italiano.

    Richiede schema http(s) esplicito: evita typo del tipo `www.foo.it` senza schema
    che poi rompono i link cliccabili nell'UI/export.
    """
    v = (value or "").strip()
    if not v:
        return None
    if not _URL_RE.match(v):
        return f"URL non valido: «{v}». Atteso schema esplicito, es. `https://www.dominio.it`."
    return None


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
