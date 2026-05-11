"""Pipeline states (semplificati) e canali.

Configurazione data-driven: aggiungere/rinominare uno stato richiede solo
un edit a `PIPELINE_CONFIG` (label, emoji, color) + eventuale `LEGACY_STATE_MAP`.
Le costanti `PIPELINE_STATES`/`PIPELINE_LABELS`/`PIPELINE_EMOJI`/`PIPELINE_COLORS`
sono derivate, mantenute per backwards-compat con il resto del codice.
"""
from __future__ import annotations

# Singolo dict canonico. Ordine = ordine di display nelle kanban / KPI.
PIPELINE_CONFIG: dict[str, dict[str, str]] = {
    "da_contattare":     {"label": "⚪ Da contattare",          "emoji": "⚪", "color": "#FFFFFF"},
    "contattata":        {"label": "⏳ Contattata",             "emoji": "⏳", "color": "#3B82F6"},
    "accettata":         {"label": "🟢 Accettata",              "emoji": "🟢", "color": "#00E676"},
    "interessati_futuro": {"label": "💡 Interessati (altre venue)", "emoji": "💡", "color": "#F59E0B"},
    "rifiutata":         {"label": "🔴 Rifiutata",              "emoji": "🔴", "color": "#FF1744"},
    "ghostati":          {"label": "👻 Ghostati",               "emoji": "👻", "color": "#71717A"},
}

PIPELINE_STATES: list[str] = list(PIPELINE_CONFIG.keys())
PIPELINE_LABELS: dict[str, str] = {k: v["label"] for k, v in PIPELINE_CONFIG.items()}
PIPELINE_EMOJI: dict[str, str] = {k: v["emoji"] for k, v in PIPELINE_CONFIG.items()}
PIPELINE_COLORS: dict[str, str] = {k: v["color"] for k, v in PIPELINE_CONFIG.items()}

# Mapping legacy → nuovi stati (per migrazione e backwards compat)
LEGACY_STATE_MAP: dict[str, str] = {
    "risposta_ricevuta": "contattata",
    "meeting_fissato": "accettata",
    "presentazione_confermata": "accettata",
    "completata": "accettata",
    "nessuna_risposta": "ghostati",
}


def normalize_state(state: str | None) -> str:
    if not state:
        return "da_contattare"
    if state in PIPELINE_STATES:
        return state
    return LEGACY_STATE_MAP.get(state, "da_contattare")


def is_pending_draft(interaction: dict) -> bool:
    """True se l'interazione è un draft in uscita NON ancora confermato dall'utente.

    Invariante del gotcha "is_draft" (vedi CLAUDE.md): i draft non confermati hanno
    direction='inviata' ma is_draft=1; vanno **esclusi** da count outgoing, history
    fornita all'LLM, last_outgoing per follow-up. Usare nei filtri di esclusione
    come `[it for it in ints if not pipeline.is_pending_draft(it)]`.
    """
    return interaction.get("direction") == "inviata" and bool(interaction.get("is_draft"))


def is_confirmed_outgoing(interaction: dict) -> bool:
    """True se l'interazione è una mail/DM in uscita confermata (non un draft pending).

    Equivalente Python del filtro SQL `direction='inviata' AND COALESCE(is_draft,0)=0`.
    Usare nei conteggi positivi ("quante mail abbiamo davvero inviato").
    """
    return interaction.get("direction") == "inviata" and not interaction.get("is_draft")


CHANNELS: list[str] = [
    "email", "ig_dm", "li_dm", "fb_dm", "phone", "in_person", "altro",
]

CHANNEL_LABELS: dict[str, str] = {
    "email": "Email",
    "ig_dm": "DM Instagram",
    "li_dm": "DM LinkedIn",
    "fb_dm": "DM Facebook",
    "phone": "Telefono",
    "in_person": "Di persona",
    "altro": "Altro",
}

# Tipo interazione ora è derivato automaticamente, non scelto dall'utente.
INTERACTION_TYPES: list[str] = [
    "prima_mail", "follow_up_1", "follow_up_2", "follow_up_3", "follow_up_n",
    "risposta", "risposta_automatica", "altro",
]

INTERACTION_TYPE_LABELS: dict[str, str] = {
    "prima_mail": "Prima mail",
    "follow_up_1": "Follow-up 1",
    "follow_up_2": "Follow-up 2",
    "follow_up_3": "Follow-up 3",
    "follow_up_n": "Follow-up",
    "risposta": "Risposta",
    "risposta_automatica": "Risposta automatica",
    "altro": "Altro",
}


def derive_interaction_type(direction: str, prior_outgoing_count: int) -> str:
    """Derive interaction type from direction + count of prior outgoing.

    direction = 'inviata':
      0 prior  → 'prima_mail'
      1 prior  → 'follow_up_1'
      2 prior  → 'follow_up_2'
      3 prior  → 'follow_up_3'
      4+ prior → 'follow_up_n'
    direction = 'ricevuta' → 'risposta'
    """
    if direction == "inviata":
        if prior_outgoing_count == 0:
            return "prima_mail"
        if prior_outgoing_count == 1:
            return "follow_up_1"
        if prior_outgoing_count == 2:
            return "follow_up_2"
        if prior_outgoing_count == 3:
            return "follow_up_3"
        return "follow_up_n"
    return "risposta"


ANGLES: list[str] = [
    "storytelling_puro", "ai_storytelling", "ai_puro",
    "collaborazione", "misto",
]

ANGLE_LABELS: dict[str, str] = {
    "storytelling_puro": "Storytelling puro",
    "ai_storytelling": "AI + Storytelling",
    "ai_puro": "AI puro",
    "collaborazione": "Collaborazione",
    "misto": "Misto",
}


def derive_effective_state(manual_state: str | None, last_interaction: dict | None) -> str:
    """Stato 'corrente' calcolato dalle interazioni reali.

    Regola:
    - Nessuna interazione: vale lo stato manuale (default `da_contattare`)
    - Ultima interazione è una nostra mail (outgoing): siamo in attesa → `contattata`,
      tranne se l'utente ha esplicitamente impostato `ghostati` o `interessati_futuro`
      (li manteniamo: sono esiti che la nuova mail non azzera automaticamente)
    - Ultima interazione è una loro risposta (incoming): vale lo stato manuale
      (accettata/rifiutata/contattata/ghostati/interessati_futuro — solo l'utente sa interpretare)

    Caso d'uso: ricontatto dopo "Accettata" → torna a "Contattata" automaticamente.
    """
    manual = normalize_state(manual_state)
    if not last_interaction:
        return manual
    if last_interaction.get("direction") == "inviata":
        if manual in ("ghostati", "interessati_futuro"):
            return manual
        return "contattata"
    return manual


def label(value: str | None, mapping: dict[str, str], fallback: str = "—") -> str:
    if not value:
        return fallback
    return mapping.get(value, value)
