"""Profilo — strategia progetto + profili speaker, in tab."""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from lib import db, ui

st.set_page_config(page_title="Profilo", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Profilo")
st.caption(
    "Strategia di progetto + profili speaker. Questi blocchi vengono iniettati in tutte le chiamate LLM "
    "(discovery, draft, follow-up). Più sono compilati bene, migliori sono i risultati."
)


def _safe_load_list(raw: Any) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, list) else []
    except Exception:
        return []


# ---------- TAB STRATEGIA ----------

def render_strategia():
    profile = db.get_project_profile()

    if db.project_profile_is_empty(profile):
        st.warning(
            "Il profilo è vuoto. La discovery e i draft funzioneranno comunque, ma con qualità ridotta. "
            "Dedica 5 minuti a compilare almeno Mission, Offerta e Target."
        )

    with st.form("project_profile_form"):
        st.subheader("Mission / scopo")
        st.caption("Cosa vogliamo ottenere con l'outreach? In 2-4 frasi.")
        mission = st.text_area(
            "Mission",
            value=profile.get("mission") or "",
            height=120,
            label_visibility="collapsed",
        )

        st.subheader("Offerta")
        st.caption("Cosa proponiamo concretamente. Formato, durata, lingua, prezzo (se noto).")
        offering = st.text_area(
            "Offerta",
            value=profile.get("offering") or "",
            height=160,
            label_visibility="collapsed",
        )

        st.subheader("Target ideale")
        st.caption("Che venue cerchiamo? Settori, dimensioni, formati, geografia preferita.")
        target_ideal = st.text_area(
            "Target ideale",
            value=profile.get("target_ideal") or "",
            height=160,
            label_visibility="collapsed",
        )

        st.subheader("Esclusioni")
        st.caption("Cosa NON cerchiamo. Deal-breakers.")
        exclusions = st.text_area(
            "Esclusioni",
            value=profile.get("exclusions") or "",
            height=120,
            label_visibility="collapsed",
        )

        st.subheader("Differenziatori")
        st.caption("Cosa ci rende unici, perché una venue dovrebbe sceglierci.")
        differentiators = st.text_area(
            "Differenziatori",
            value=profile.get("differentiators") or "",
            height=120,
            label_visibility="collapsed",
        )

        st.subheader("Note libere")
        notes = st.text_area(
            "Note",
            value=profile.get("notes") or "",
            height=100,
            label_visibility="collapsed",
        )

        if st.form_submit_button("Salva strategia"):
            db.update_project_profile({
                "mission": mission.strip(),
                "offering": offering.strip(),
                "target_ideal": target_ideal.strip(),
                "exclusions": exclusions.strip(),
                "differentiators": differentiators.strip(),
                "notes": notes.strip(),
            })
            st.success("Strategia salvata.")
            st.rerun()


# ---------- TAB SPEAKER ----------

def render_speaker_form(name: str):
    sp = db.get_speaker(name)
    if not sp:
        st.error(f"Profilo {name} non trovato.")
        return

    skills = _safe_load_list(sp.get("skills_json"))
    languages = _safe_load_list(sp.get("languages_json"))
    experiences = _safe_load_list(sp.get("experiences_json"))

    with st.form(f"form_{name}"):
        bio = st.text_area("Bio", value=sp.get("bio") or "", height=180)

        skills_text = st.text_area(
            "Competenze (una per riga)",
            value="\n".join(skills),
            height=120,
        )

        languages_text = st.text_input(
            "Lingue (separate da virgola)",
            value=", ".join(languages),
        )

        experiences_text = st.text_area(
            "Esperienze (una per riga, formato 'titolo: descrizione')",
            value="\n".join(
                f"{e.get('titolo','')}: {e.get('descrizione','')}" if isinstance(e, dict) else str(e)
                for e in experiences
            ),
            height=220,
        )

        role = st.text_input(
            "Ruolo nella coppia",
            value=sp.get("role_in_pair") or "",
        )

        if st.form_submit_button(f"Salva {name}"):
            new_skills = [s.strip() for s in skills_text.splitlines() if s.strip()]
            new_languages = [l.strip().upper() for l in languages_text.split(",") if l.strip()]
            new_experiences = []
            for line in experiences_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if ":" in line:
                    title, desc = line.split(":", 1)
                    new_experiences.append({"titolo": title.strip(), "descrizione": desc.strip()})
                else:
                    new_experiences.append({"titolo": line, "descrizione": ""})

            db.update_speaker(name, {
                "bio": bio,
                "skills_json": json.dumps(new_skills, ensure_ascii=False),
                "languages_json": json.dumps(new_languages, ensure_ascii=False),
                "experiences_json": json.dumps(new_experiences, ensure_ascii=False),
                "role_in_pair": role,
            })
            st.success(f"Profilo {name} salvato.")
            st.rerun()


tab_strategia, tab_luca, tab_stefano = st.tabs(["Strategia", "Luca", "Stefano"])
with tab_strategia:
    render_strategia()
with tab_luca:
    render_speaker_form("Luca")
with tab_stefano:
    render_speaker_form("Stefano")
