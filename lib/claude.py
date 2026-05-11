"""Anthropic SDK wrapper for the outreach tool.

All methods produce structured output via output_config.format=json_schema.
The system prompt and speaker profiles are wrapped in a cache_control block
so repeated calls within ~5 minutes hit the prompt cache.
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Optional

import anthropic

from . import db, prompts

MODEL = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 4096

# Modello per task: i task "ad alto impatto creativo" (draft email, discovery)
# usano Sonnet; i task estrattivi/classificatori usano Haiku (~5x più economico,
# latenza minore). Override puntuale possibile passando model=... a _call_json.
MODEL_BY_TASK = {
    "draft_first_email": MODEL,
    "refine_first_email": MODEL,
    "draft_follow_up": MODEL,
    "discover_venues": MODEL,
    "test_connection": MODEL,
    # Task economici → Haiku
    "enrich_venue": MODEL_HAIKU,
    "suggest_channel": MODEL_HAIKU,
    "analyze_response": MODEL_HAIKU,
    # PDF input richiede Sonnet (Haiku attualmente non supporta `document` blocks)
    "summarize_attachment": MODEL,
}

# Retry su errori transienti API Anthropic (rate limit, 5xx, connessione, timeout).
# Backoff esponenziale con jitter: 1s, 2s, 4s. Errori di autenticazione/validazione
# (4xx non-429) non vengono ritentati: li ri-alziamo subito.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
)

# Pricing USD per 1M token (Sonnet 4.6, valori pubblici attuali).
# Aggiornare se i listini cambiano. Usato dalla pagina Costi per stimare a posteriori.
PRICING_USD_PER_MTOK = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_creation": 1.00,
    },
}


def _client() -> anthropic.Anthropic:
    from .settings import get_api_key, api_key_status
    key = get_api_key()
    if not key:
        status = api_key_status()
        if status == "corrupt":
            raise RuntimeError(
                "API key Anthropic non decifrabile: la master key è cambiata o persa. "
                "Aprire pagina Impostazioni e re-inserire la chiave (vedi docs/OPERATIONS.md per il recovery)."
            )
        raise RuntimeError("API key Anthropic non configurata. Aprire pagina Impostazioni.")
    return anthropic.Anthropic(api_key=key)


def _with_retry(call):
    """Esegue `call()` (zero-arg) con retry/backoff esponenziale + jitter su errori transienti.
    Errori non retry-abili (auth, schema, ecc.) vengono ri-alzati subito.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return call()
        except _RETRYABLE_ERRORS as e:
            last_exc = e
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                break
            delay = _RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _extract_usage(response: Any) -> dict:
    """Estrae i counter di token da una response Anthropic. Tollerante ai missing field."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def _log_llm_call(
    task: str,
    model: str,
    usage: dict,
    duration_ms: int,
    error: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    """Persistenza non-fatal: se il logging fallisce, non blocca la chiamata."""
    try:
        db.insert_llm_call({
            "task": task,
            "model": model,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_tokens", 0),
            "cache_creation_tokens": usage.get("cache_creation_tokens", 0),
            "duration_ms": duration_ms,
            "error": error,
            "meta_json": json.dumps(meta) if meta else None,
        })
    except Exception:
        pass


# ----- Schemas for structured outputs -----

DRAFT_FIRST_EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "channel_suggestion": {"type": "string", "enum": ["email", "li_dm", "ig_dm", "fb_dm", "phone"]},
        "speaker_choice": {"type": "string", "enum": ["Luca", "Stefano", "entrambi"]},
        "tone": {"type": "string", "enum": ["formale", "cordiale", "informale", "tecnico"]},
        "language": {"type": "string", "enum": ["IT", "EN", "DE"]},
        "rationale": {"type": "string"},
    },
    "required": ["subject", "body", "channel_suggestion", "speaker_choice", "tone", "language", "rationale"],
    "additionalProperties": False,
}

DRAFT_FOLLOW_UP_SCHEMA = {
    "type": "object",
    "properties": {
        "timing_suggestion_days": {"type": "integer"},
        "should_send": {"type": "boolean"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "channel_suggestion": {"type": "string", "enum": ["email", "li_dm", "ig_dm", "fb_dm", "phone"]},
        "rationale": {"type": "string"},
    },
    "required": ["timing_suggestion_days", "should_send", "subject", "body", "channel_suggestion", "rationale"],
    "additionalProperties": False,
}

ANALYZE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["positivo", "neutro", "negativo", "automatico"]},
        # Score numerico -1.0 (molto negativo) .. +1.0 (molto positivo). Permette
        # trend analysis e confronti aggregati che la sola enum non supporta.
        "sentiment_score": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "is_meeting_proposal": {"type": "boolean"},
        "is_rejection": {"type": "boolean"},
        "suggested_status": {
            "type": "string",
            "enum": [
                "da_contattare", "contattata", "risposta_ricevuta",
                "meeting_fissato", "presentazione_confermata",
                "completata", "rifiutata", "nessuna_risposta",
            ],
        },
        "suggested_action": {"type": "string"},
        "key_info_extracted": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": [
        "sentiment", "sentiment_score",
        "is_meeting_proposal", "is_rejection",
        "suggested_status", "suggested_action", "key_info_extracted", "notes",
    ],
    "additionalProperties": False,
}

CONTACT_OBJ_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "role": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "email_confidence": {"type": "string", "enum": ["alta", "media", "bassa"]},
        "is_primary": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
    "required": ["name", "role", "email", "phone", "email_confidence", "is_primary", "rationale"],
    "additionalProperties": False,
}


DISCOVER_VENUES_SCHEMA = {
    "type": "object",
    "properties": {
        "venues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "website": {"type": "string"},
                    "city": {"type": "string"},
                    "province": {"type": "string"},
                    "region": {"type": "string"},
                    "language": {"type": "string"},
                    "angle": {"type": "string"},
                    "deadline_text": {"type": "string"},
                    "contacts": {
                        "type": "array",
                        "items": CONTACT_OBJ_SCHEMA,
                        "description": "1-3 referenti per la venue. Esattamente uno con is_primary=true.",
                    },
                    "recommended_first_channel": {
                        "type": "string",
                        "enum": ["email", "li_dm", "ig_dm", "fb_dm", "phone"],
                    },
                    "social_handles": {
                        "type": "object",
                        "properties": {
                            "instagram": {"type": "string"},
                            "facebook": {"type": "string"},
                            "linkedin": {"type": "string"},
                        },
                        "required": ["instagram", "facebook", "linkedin"],
                        "additionalProperties": False,
                    },
                    "channel_rationale": {"type": "string"},
                    "fit_with_project": {"type": "string"},
                    "acceptance_score": {"type": "integer", "enum": [1, 2, 3]},
                    "acceptance_rationale": {"type": "string"},
                    "is_known_venue_new_event": {"type": "boolean"},
                    "organizer": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "website": {"type": "string"},
                            "hq_city": {"type": "string"},
                            "hq_province": {"type": "string"},
                            "region": {"type": "string"},
                            "language": {"type": "string"},
                            "description": {"type": "string"},
                            "social_linkedin": {"type": "string"},
                            "social_instagram": {"type": "string"},
                            "social_facebook": {"type": "string"},
                            "is_known": {
                                "type": "boolean",
                                "description": "true se questo Ente è già presente nei dossier mostrati come 'venue note'.",
                            },
                            "contacts": {
                                "type": "array",
                                "items": CONTACT_OBJ_SCHEMA,
                                "description": "0-2 referenti a livello Ente (es. presidente distretto, segretario network, responsabile programmazione nazionale). Diversi dai contatti della singola venue.",
                            },
                        },
                        "required": [
                            "name", "type", "website", "hq_city", "hq_province", "region",
                            "language", "description", "social_linkedin", "social_instagram",
                            "social_facebook", "is_known", "contacts",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "name", "type", "description", "website", "city", "province", "region",
                    "language", "angle", "deadline_text", "contacts",
                    "recommended_first_channel", "social_handles", "channel_rationale",
                    "fit_with_project", "acceptance_score", "acceptance_rationale",
                    "is_known_venue_new_event", "organizer",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["venues"],
    "additionalProperties": False,
}

ENRICH_VENUE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": ["string", "null"]},
        "city": {"type": ["string", "null"]},
        "province": {"type": ["string", "null"]},
        "region": {"type": ["string", "null"]},
        "language": {"type": ["string", "null"]},
        "angle": {"type": ["string", "null"]},
        "funding_type": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "website": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "organizer_name": {"type": ["string", "null"]},
        "organizer_type": {"type": ["string", "null"]},
        "organizer_website": {"type": ["string", "null"]},
    },
    "required": [
        "type", "city", "province", "region", "language", "angle", "funding_type",
        "tags", "website", "address",
        "organizer_name", "organizer_type", "organizer_website",
    ],
    "additionalProperties": False,
}

ATTACHMENT_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "kind": {"type": "string", "enum": [
            "slide", "workshop", "case_study", "brochure",
            "presentazione", "documento", "immagine", "altro",
        ]},
        "target_audience": {"type": "string"},
        "key_topics": {"type": "array", "items": {"type": "string"}},
        "duration_minutes": {"type": ["integer", "null"]},
        "summary": {"type": "string"},
        "when_to_use": {"type": "string"},
    },
    "required": [
        "title", "kind", "target_audience", "key_topics",
        "duration_minutes", "summary", "when_to_use",
    ],
    "additionalProperties": False,
}


SUGGEST_CHANNEL_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_channel": {"type": "string", "enum": ["email", "li_dm", "ig_dm", "fb_dm", "phone"]},
        "fallback_channel": {"type": ["string", "null"], "enum": ["email", "li_dm", "ig_dm", "fb_dm", "phone", None]},
        "rationale": {"type": "string"},
    },
    "required": ["primary_channel", "fallback_channel", "rationale"],
    "additionalProperties": False,
}


ANALYZE_OUTREACH_APPROACH_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_reassessment": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "enum": [1, 2, 3]},
                "recent_activities": {"type": "string"},
                "fit_rationale": {"type": "string"},
                "positive_signals": {"type": "array", "items": {"type": "string"}},
                "negative_signals": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "recent_activities", "fit_rationale", "positive_signals", "negative_signals"],
            "additionalProperties": False,
        },
        "is_current_contact_best": {"type": "boolean"},
        "current_contact_assessment": {"type": "string"},
        "better_contact": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "role": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "source_url": {"type": "string"},
                "email_confidence": {"type": "string", "enum": ["alta", "media", "bassa", ""]},
                "rationale": {"type": "string"},
            },
            "required": ["name", "role", "email", "phone", "source_url", "email_confidence", "rationale"],
            "additionalProperties": False,
        },
        "next_action": {
            "type": "string",
            "enum": ["follow_up", "switch_contact", "mark_rejected", "wait"],
        },
        "follow_up_plan": {
            "type": "object",
            "properties": {
                "should_send": {"type": "boolean"},
                "timing_days": {"type": "integer"},
                "tone": {"type": "string"},
                "subject_hint": {"type": "string"},
                "body_hint": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["should_send", "timing_days", "tone", "subject_hint", "body_hint", "rationale"],
            "additionalProperties": False,
        },
        "rejection_reasoning": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": [
        "fit_reassessment",
        "is_current_contact_best", "current_contact_assessment", "better_contact",
        "next_action", "follow_up_plan", "rejection_reasoning", "summary",
    ],
    "additionalProperties": False,
}


# ----- Core call helpers -----

def _build_system_blocks(
    speakers: list[dict],
    project_profile: dict | None = None,
    include_email_guidelines: bool = False,
) -> list[dict]:
    """System content con prompt caching.

    Ordine: SYSTEM_PROMPT → project profile → speakers (cache breakpoint base) →
    [email_guidelines (cache breakpoint specifico per task draft)].
    Mettiamo le email_guidelines (file di ~20 kB letto fresh ad ogni call) qui
    invece che in user_text per beneficiare del prompt cache: senza, il file
    veniva ri-trasmesso integralmente a ogni draft.
    """
    blocks: list[dict] = [
        {"type": "text", "text": prompts.SYSTEM_PROMPT},
        {"type": "text", "text": prompts.project_profile_block(project_profile)},
        {
            "type": "text",
            "text": prompts.speakers_block(speakers),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if include_email_guidelines:
        blocks.append({
            "type": "text",
            "text": prompts.email_drafting_guidelines(),
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


def _call_json(
    *,
    system_blocks: list[dict],
    user_text: str,
    schema: dict,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    tools: Optional[list[dict]] = None,
    task: str = "unknown",
    model: str = MODEL,
) -> dict:
    client = _client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": schema,
            }
        },
    }
    if tools:
        kwargs["tools"] = tools

    started_at = time.monotonic()
    error_msg: Optional[str] = None
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    try:
        response = _with_retry(lambda: client.messages.create(**kwargs))
        usage = _extract_usage(response)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        _log_llm_call(task, model, usage, int((time.monotonic() - started_at) * 1000), error=error_msg)
        raise

    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text

    duration_ms = int((time.monotonic() - started_at) * 1000)
    _log_llm_call(task, model, usage, duration_ms)

    if not text.strip():
        raise RuntimeError("Risposta LLM vuota.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Best-effort recovery: extract first { ... } block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"Risposta LLM non parsabile come JSON: {e}\n\nTesto:\n{text[:500]}")


# ----- Public methods -----

def _ctx() -> tuple[list[dict], dict]:
    return db.get_speakers(), db.get_project_profile()


def _build_draft_context_blocks(
    venue: dict,
    contact: Optional[dict],
    selected_attachment_ids: Optional[list[int]] = None,
) -> list[str]:
    """Blocchi di context riusabili per draft_first_email + refine_first_email.

    `selected_attachment_ids`: id degli allegati che l'utente intende includere
    in QUESTA mail. I loro riassunti vengono iniettati nel context così la LLM
    può deciderne la menzione nel body. In aggiunta vengono passati anche i
    riassunti degli allegati GIÀ INVIATI alla venue in passato (read-only,
    per evitare di ri-allegare la stessa cosa o per riferirsi a ciò che è
    già stato condiviso).
    """
    similar = _build_similar_history(venue)
    same_venue_ints = db.get_interactions_for_venue(venue["id"]) if venue.get("id") else []
    # I draft non confermati NON sono "history": sono il documento che stiamo riscrivendo.
    # Includerli farebbe credere all'LLM che la mail sia già stata inviata.
    same_venue_ints = [
        it for it in same_venue_ints
        if not (it.get("direction") == "inviata" and it.get("is_draft"))
    ]
    cross_contact_ints: list[dict] = []
    if contact and contact.get("id"):
        cross_contact_ints = [
            it for it in db.get_interactions_for_contact(contact["id"], limit=30)
            if it.get("venue_id") != venue.get("id")  # solo cross-venue
            and not (it.get("direction") == "inviata" and it.get("is_draft"))
        ]
    organizer, sibling_venues, sibling_ints = _organizer_context_for_venue(venue)
    blocks = [
        prompts.venue_block(venue),
        prompts.contact_block(contact),
        prompts.same_venue_history_block(same_venue_ints),
        prompts.similar_history_block(similar),
    ]
    org_block = prompts.organizer_block(organizer, n_related_venues=len(sibling_venues) + (1 if organizer else 0))
    if org_block:
        blocks.append(org_block)
    siblings_block = prompts.same_organizer_venues_block(organizer, sibling_venues, sibling_ints)
    if siblings_block:
        blocks.append(siblings_block)
    cross_block = prompts.contact_cross_venue_history_block(contact, cross_contact_ints)
    if cross_block:
        blocks.append(cross_block)

    # Allegati: già inviati a questa venue (storico) + selezionati per questa mail.
    if venue.get("id"):
        already_sent = db.get_attachments_sent_to_venue(venue["id"])
        if already_sent:
            blocks.append(prompts.attachments_block(
                already_sent,
                header="ALLEGATI GIÀ INVIATI A QUESTA VENUE IN PASSATO",
                intro=(
                    "Questi file sono già stati spediti alla venue in mail precedenti. "
                    "Non ri-allegarli a meno che non sia esplicitamente utile, e fai riferimento "
                    "a essi solo se rilevante per il filo del discorso (\"come anticipato nelle slide…\")."
                ),
            ))
    if selected_attachment_ids:
        selected = [
            db.get_attachment(aid) for aid in selected_attachment_ids
        ]
        selected = [a for a in selected if a]
        if selected:
            blocks.append(prompts.attachments_block(
                selected,
                header="ALLEGATI DA INCLUDERE IN QUESTA MAIL",
                intro=(
                    "L'utente intende allegare questi file alla mail in uscita. Se il loro "
                    "contenuto è coerente col messaggio, fai riferimento esplicito (es. "
                    "\"in allegato trovi le slide del workshop X\") in modo che il destinatario "
                    "sappia cosa aspettarsi. Se non aggiungono valore, non menzionarli."
                ),
            ))
    return blocks


def _organizer_context_for_venue(venue: dict) -> tuple[Optional[dict], list[dict], list[dict]]:
    """Fetch Ente + venue sorelle + loro interazioni (escludendo la venue corrente).

    Ritorna (organizer|None, sibling_venues, sibling_interactions). Le interazioni
    escludono i draft non confermati (coerente col resto del builder)."""
    if not venue.get("id"):
        return None, [], []
    organizer = db.get_organizer_for_venue(venue["id"])
    if not organizer:
        return None, [], []
    siblings = [
        v for v in db.get_venues_for_organizer(organizer["id"])
        if v["id"] != venue["id"]
    ]
    if not siblings:
        return organizer, [], []
    sibling_ints: list[dict] = []
    for sv in siblings:
        sib_ints = [
            it for it in db.get_interactions_for_venue(sv["id"])
            if not (it.get("direction") == "inviata" and it.get("is_draft"))
        ]
        sibling_ints.extend(sib_ints)
    return organizer, siblings, sibling_ints


def build_draft_first_email_prompt(
    venue: dict,
    contact: Optional[dict],
    selected_attachment_ids: Optional[list[int]] = None,
) -> str:
    """Restituisce il testo del prompt assemblato (utile per debug/inspection in UI).

    Le email guidelines sono nei system blocks (cachate), non in user_text.
    """
    return "\n\n".join(
        _build_draft_context_blocks(venue, contact, selected_attachment_ids)
        + [prompts.DRAFT_FIRST_EMAIL_TASK]
    )


def draft_first_email(
    venue: dict,
    contact: Optional[dict],
    selected_attachment_ids: Optional[list[int]] = None,
) -> dict:
    """Genera la prima mail per una venue. Output JSON: subject, body, channel_suggestion, speaker_choice, tone, language, rationale.

    Inietta nel prompt: profilo venue + contact, history same_venue, similar
    venues con storia, ente madre + venue sorelle, history cross-venue del
    contatto, allegati selezionati + già inviati. Esclude draft non confermati.
    """
    speakers, profile = _ctx()
    user_text = build_draft_first_email_prompt(venue, contact, selected_attachment_ids)
    return _call_json(
        system_blocks=_build_system_blocks(speakers, profile, include_email_guidelines=True),
        user_text=user_text,
        schema=DRAFT_FIRST_EMAIL_SCHEMA,
        task="draft_first_email",
    )


REFINE_DRAFT_TASK = """COMPITO: l'utente ha generato un draft e ora chiede una revisione in linguaggio naturale. Riscrivi il draft applicando il feedback. Mantieni la stessa struttura (subject + body) e lo stesso schema JSON dell'output originale.

Regole:
- Applica IL FEEDBACK in modo letterale ma sensato. Se l'utente dice "rimuovi X", rimuovilo davvero.
- Se il feedback è contraddittorio col profilo speaker o col profilo progetto, segui il feedback dell'utente (è il suo testo, decide lui) ma annota la tensione nel `rationale`.
- NON re-introdurre cose che l'utente ha esplicitamente chiesto di togliere in turni precedenti (vedi storia conversazione).
- Mantieni `speaker_choice`, `tone`, `language`, `channel_suggestion` se non chiesti esplicitamente diversi.
- `rationale`: 1-2 frasi su cosa hai cambiato rispetto alla versione precedente.

Output JSON con lo stesso schema della prima generazione (subject, body, channel_suggestion, speaker_choice, tone, language, rationale)."""


def refine_first_email(
    venue: dict,
    contact: Optional[dict],
    refinement_history: list[dict],
    selected_attachment_ids: Optional[list[int]] = None,
) -> dict:
    """Refine an email draft via natural-language feedback."""
    speakers, profile = _ctx()

    history_text_parts = ["=== STORIA REVISIONI (più vecchia → più recente) ==="]
    for i, entry in enumerate(refinement_history, 1):
        if entry["role"] == "draft":
            d = entry["content"] if isinstance(entry["content"], dict) else {}
            history_text_parts.append(
                f"\n[Draft v{i}]\nOggetto: {d.get('subject','')}\nCorpo:\n{d.get('body','')}"
            )
        else:
            history_text_parts.append(f"\n[Feedback utente turn {i}]\n{entry['content']}")

    user_text = "\n\n".join(
        _build_draft_context_blocks(venue, contact, selected_attachment_ids)
        + ["\n".join(history_text_parts), REFINE_DRAFT_TASK]
    )
    return _call_json(
        system_blocks=_build_system_blocks(speakers, profile, include_email_guidelines=True),
        user_text=user_text,
        schema=DRAFT_FIRST_EMAIL_SCHEMA,
        task="refine_first_email",
    )


def draft_follow_up(
    venue: dict,
    contact: Optional[dict],
    last_interaction: dict,
    response: Optional[dict],
    days_since: int,
    selected_attachment_ids: Optional[list[int]] = None,
) -> dict:
    """Suggerisce timing + draft di un follow-up. Output JSON: timing_suggestion_days, should_send (bool), subject, body, channel_suggestion, rationale.

    Se `should_send=False` il rationale spiega perché aspettare ancora.
    `last_interaction` deve essere una mail outgoing CONFERMATA (escludi
    is_draft=1 a monte).
    """
    speakers, profile = _ctx()
    history_text = "Ultima interazione inviata:\n"
    history_text += f"  Data: {last_interaction.get('occurred_at')}\n"
    history_text += f"  Canale: {last_interaction.get('channel')}\n"
    history_text += f"  Oggetto: {last_interaction.get('subject', '')}\n"
    history_text += f"  Contenuto: {(last_interaction.get('content') or '')[:1500]}\n"
    history_text += f"\nGiorni trascorsi dall'invio: {days_since}\n"
    if response:
        history_text += f"\nRisposta ricevuta il {response.get('occurred_at')}:\n{(response.get('content') or '')[:1500]}\n"
    else:
        history_text += "\nNessuna risposta ricevuta finora.\n"
    organizer, sibling_venues, sibling_ints = _organizer_context_for_venue(venue)
    parts = [
        prompts.venue_block(venue),
        prompts.contact_block(contact),
    ]
    org_block = prompts.organizer_block(organizer, n_related_venues=len(sibling_venues) + (1 if organizer else 0))
    if org_block:
        parts.append(org_block)
    siblings_block = prompts.same_organizer_venues_block(organizer, sibling_venues, sibling_ints)
    if siblings_block:
        parts.append(siblings_block)
    if venue.get("id"):
        already_sent = db.get_attachments_sent_to_venue(venue["id"])
        if already_sent:
            parts.append(prompts.attachments_block(
                already_sent,
                header="ALLEGATI GIÀ INVIATI A QUESTA VENUE IN PASSATO",
                intro="Sono già stati spediti in mail precedenti — non ri-allegarli salvo richiesta esplicita.",
            ))
    if selected_attachment_ids:
        selected = [db.get_attachment(aid) for aid in selected_attachment_ids]
        selected = [a for a in selected if a]
        if selected:
            parts.append(prompts.attachments_block(
                selected,
                header="ALLEGATI DA INCLUDERE IN QUESTO FOLLOW-UP",
            ))
    parts.extend([history_text, prompts.DRAFT_FOLLOW_UP_TASK])
    user_text = "\n\n".join(parts)
    return _call_json(
        system_blocks=_build_system_blocks(speakers, profile, include_email_guidelines=True),
        user_text=user_text,
        schema=DRAFT_FOLLOW_UP_SCHEMA,
        task="draft_follow_up",
    )


def analyze_response(venue: dict, response_text: str, history: list[dict]) -> dict:
    """Analizza una risposta ricevuta. Output JSON: sentiment, is_meeting_proposal, is_rejection, suggested_status (stato pipeline), suggested_action, key_info_extracted, notes.

    `suggested_status` può ritornare valori legacy (`risposta_ricevuta`, ecc.):
    chi consuma deve normalizzarlo via `pipeline.normalize_state()`.
    """
    speakers, profile = _ctx()
    organizer, sibling_venues, sibling_ints = _organizer_context_for_venue(venue)
    parts = [
        prompts.venue_block(venue),
        prompts.history_block(history),
    ]
    org_block = prompts.organizer_block(organizer, n_related_venues=len(sibling_venues) + (1 if organizer else 0))
    if org_block:
        parts.append(org_block)
    siblings_block = prompts.same_organizer_venues_block(organizer, sibling_venues, sibling_ints)
    if siblings_block:
        parts.append(siblings_block)
    parts.extend([f"=== RISPOSTA RICEVUTA ===\n{response_text}", prompts.ANALYZE_RESPONSE_TASK])
    user_text = "\n\n".join(parts)
    return _call_json(
        system_blocks=_build_system_blocks(speakers, profile),
        user_text=user_text,
        schema=ANALYZE_RESPONSE_SCHEMA,
        max_tokens=2048,
        task="analyze_response",
        model=MODEL_BY_TASK["analyze_response"],
    )


def analyze_outreach_approach(
    venue: dict,
    current_contact: Optional[dict],
    venue_contacts: list[dict],
    interactions: list[dict],
    days_since_last_outgoing: int,
    on_progress=None,
) -> dict:
    """Analisi web-search di outreach in corso: il contatto è il migliore? cosa fare adesso?

    Esegue una deep search (web_search_20250305) per verificare il referente attuale e
    suggerire next action (follow_up, switch_contact, mark_rejected, wait) + un piano di
    follow-up con hint per il prossimo draft. Loop pause_turn fino a 4 round, max 25 search.

    `current_contact`: il contatto usato nelle mail già inviate (o quello consigliato se
    non c'è ancora storia). Può essere None se le mail sono state mandate a indirizzo generico.
    `interactions`: tutte le interazioni della venue (già filtrate dai draft non confermati).
    """
    speakers, profile = _ctx()

    # Costruzione context: venue + ente + sorelle + contatto attuale + altri contatti + storico
    organizer, sibling_venues, sibling_ints = _organizer_context_for_venue(venue)
    parts = [
        prompts.venue_block(venue),
    ]
    # Punteggio fit precedente (se mai assegnato) → utile per il riesame, ma da
    # non assumere come ancoraggio: il prompt chiede esplicitamente fresh-eyes.
    prev_score = venue.get("acceptance_score")
    if prev_score:
        parts.append(
            f"=== VALUTAZIONE FIT PRECEDENTE ===\n"
            f"acceptance_score storico: {prev_score}/3. "
            "Rivaluta a freddo nel `fit_reassessment` — non ancorarti a questo valore, "
            "ma puoi citarlo se ti sembra ancora valido."
        )
    org_block = prompts.organizer_block(organizer, n_related_venues=len(sibling_venues) + (1 if organizer else 0))
    if org_block:
        parts.append(org_block)
    siblings_block = prompts.same_organizer_venues_block(organizer, sibling_venues, sibling_ints)
    if siblings_block:
        parts.append(siblings_block)

    # Contatto attualmente usato (evidenziato)
    if current_contact:
        cc_name = " ".join(filter(None, [current_contact.get("first_name"), current_contact.get("last_name")])).strip() or "(senza nome)"
        cc_lines = [f"=== CONTATTO ATTUALMENTE USATO PER L'OUTREACH ==="]
        cc_lines.append(f"Nome: {cc_name}")
        for k in ("role", "email", "phone", "social_linkedin"):
            v = current_contact.get(k)
            if v:
                cc_lines.append(f"{k}: {v}")
        if current_contact.get("notes"):
            cc_lines.append(f"Note: {current_contact['notes']}")
        parts.append("\n".join(cc_lines))
    else:
        parts.append(
            "=== CONTATTO ATTUALMENTE USATO PER L'OUTREACH ===\n"
            "(nessun contatto specifico — le mail sono state inviate all'indirizzo generico della venue)"
        )

    # Altri contatti noti per questa venue (escludendo l'attuale)
    other_contacts = [
        c for c in venue_contacts
        if not current_contact or c.get("id") != current_contact.get("id")
    ]
    if other_contacts:
        oc_lines = ["=== ALTRI CONTATTI GIÀ NOTI PER QUESTA VENUE ==="]
        for c in other_contacts:
            nm = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip() or "(senza nome)"
            extras = []
            if c.get("role"):
                extras.append(c["role"])
            if c.get("email"):
                extras.append(c["email"])
            oc_lines.append(f"- {nm}" + (f" — {' · '.join(extras)}" if extras else ""))
        parts.append("\n".join(oc_lines))

    # Storico completo della venue (timeline cronologica con date)
    if interactions:
        h_lines = ["=== STORICO INTERAZIONI VENUE (cronologico, dal più vecchio) ==="]
        for it in interactions:
            occurred = str(it.get("occurred_at") or "")[:10]
            arrow = "→ nostra" if it.get("direction") == "inviata" else "← loro"
            subj = (it.get("subject") or "").strip()
            body = (it.get("content") or "").strip().replace("\n", " ")
            h_lines.append(
                f"\n[{occurred}] {arrow} ({it.get('type', '')}) "
                f"{('«' + subj[:100] + '» ') if subj else ''}"
            )
            if body:
                h_lines.append(f"  {body[:800]}")
        parts.append("\n".join(h_lines))
    else:
        parts.append("=== STORICO INTERAZIONI VENUE ===\n(nessuna interazione registrata)")

    parts.append(f"=== TEMPO TRASCORSO ===\nGiorni dall'ultima mail uscente: {days_since_last_outgoing}")
    parts.append(prompts.ANALYZE_OUTREACH_APPROACH_TASK)

    user_text = "\n\n".join(parts)

    tools = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 25,
    }]
    system_blocks = _build_system_blocks(speakers, profile)

    client = _client()
    messages: list[dict] = [{"role": "user", "content": user_text}]
    final_response = None
    max_continuations = 4

    started_at = time.monotonic()
    cumulative_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    for attempt in range(max_continuations + 1):
        _emit(f"Round {attempt + 1}/{max_continuations + 1} — LLM al lavoro...")
        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=system_blocks,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": ANALYZE_OUTREACH_APPROACH_SCHEMA},
            },
        ) as stream:
            announced_thinking = False
            announced_output = False
            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    btype = getattr(block, "type", None)
                    if btype == "server_tool_use":
                        bname = getattr(block, "name", "")
                        if bname == "web_search":
                            _emit("🔎 Avvio ricerca web…")
                    elif btype == "thinking" and not announced_thinking:
                        _emit("💭 Sto ragionando sui risultati…")
                        announced_thinking = True
                    elif btype == "text" and not announced_output:
                        _emit("📝 Sto scrivendo l'analisi…")
                        announced_output = True
                elif etype == "content_block_stop":
                    snapshot = getattr(stream, "current_message_snapshot", None)
                    if snapshot and getattr(snapshot, "content", None):
                        last = snapshot.content[-1]
                        ltype = getattr(last, "type", None)
                        if ltype == "server_tool_use" and getattr(last, "name", "") == "web_search":
                            inp = getattr(last, "input", {}) or {}
                            query = inp.get("query") if isinstance(inp, dict) else None
                            if query:
                                _emit(f"🔎 Query: «{query}»")
                        elif ltype == "web_search_tool_result":
                            content = getattr(last, "content", []) or []
                            n = len(content) if hasattr(content, "__len__") else 0
                            _emit(f"📊 Risultati ricevuti ({n} link)")
                            announced_thinking = False
                elif etype == "message_delta":
                    delta = getattr(event, "delta", None)
                    stop_reason = getattr(delta, "stop_reason", None) if delta else None
                    if stop_reason == "pause_turn":
                        _emit("⏸️ Riprendo automaticamente…")
            response = stream.get_final_message()

        round_usage = _extract_usage(response)
        for k in cumulative_usage:
            cumulative_usage[k] += round_usage.get(k, 0)
        final_response = response
        sr = response.stop_reason
        if sr == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        _emit(f"✓ Stop reason: {sr}")
        break

    duration_ms = int((time.monotonic() - started_at) * 1000)
    meta = {"venue_id": venue.get("id")}

    if final_response is None:
        _log_llm_call("analyze_outreach_approach", MODEL, cumulative_usage, duration_ms,
                      error="no response", meta=meta)
        raise RuntimeError("Analisi outreach: nessuna risposta dal modello.")

    text = "".join(b.text for b in final_response.content if getattr(b, "type", None) == "text")
    if not text.strip():
        _log_llm_call("analyze_outreach_approach", MODEL, cumulative_usage, duration_ms,
                      error="empty response", meta=meta)
        raise RuntimeError("Analisi outreach: risposta vuota.")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            result = json.loads(text[start:end + 1])
        else:
            _log_llm_call("analyze_outreach_approach", MODEL, cumulative_usage, duration_ms,
                          error="json parse failed", meta=meta)
            raise RuntimeError(f"Analisi outreach: JSON non parsabile.\n{text[:500]}")

    _log_llm_call("analyze_outreach_approach", MODEL, cumulative_usage, duration_ms, meta=meta)
    return result


def _build_venue_dossier(v: dict) -> str:
    """Per una venue nota: profilo + thread di proposte raggruppati per evento + esiti."""
    lines: list[str] = []
    lines.append(f"### {v['name']}")

    # Profilo completo
    pf = []
    if v.get("type"): pf.append(f"tipo: {v['type']}")
    if v.get("city"): pf.append(f"città: {v['city']}")
    if v.get("region"): pf.append(f"regione: {v['region']}")
    if v.get("language"): pf.append(f"lingua: {v['language']}")
    if v.get("angle"): pf.append(f"angolo: {v['angle']}")
    if v.get("website"): pf.append(f"sito: {v['website']}")
    raw_state = v.get("pipeline_status") or "da_contattare"
    state_note = ""
    if raw_state == "interessati_futuro":
        state_note = " ⚠️ INTERESSATI MA NON IN QUESTA VENUE: il referente ha mostrato interesse genuino ma questa specifica sede non era adatta. Cerca attivamente NUOVE venue/eventi dello stesso organizzatore (sedi recenti, nuove edizioni, nuovi format) che potrebbero essere un fit migliore."
    pf.append(f"stato attuale: {raw_state}{state_note}")
    if v.get("deadline_text"): pf.append(f"deadline nota: {v['deadline_text']}")
    if v.get("acceptance_score"): pf.append(f"voto compatibilità precedente: {v['acceptance_score']}/3")
    lines.append("Profilo: " + " · ".join(pf))
    if v.get("description"):
        lines.append(f"Descrizione: {v['description'][:280]}")

    # Raggruppa interactions in thread: ogni `prima_mail` apre un thread,
    # i follow-up e le risposte vanno nel thread corrente fino alla prossima `prima_mail`.
    # Escludi i draft non confermati: non sono mail effettivamente inviate.
    ints = [
        it for it in db.get_interactions_for_venue(v["id"])
        if not (it.get("direction") == "inviata" and it.get("is_draft"))
    ]
    if not ints:
        return "\n".join(lines)

    threads: list[dict] = []
    current: dict | None = None
    for it in ints:
        if it.get("type") == "prima_mail":
            if current:
                threads.append(current)
            current = {"first_mail": it, "events": []}
        elif current is not None:
            current["events"].append(it)
    if current:
        threads.append(current)

    if not threads:
        # Solo follow-up senza prima_mail tracciata (caso anomalo); skip dettagli
        return "\n".join(lines)

    lines.append(f"Storico proposte ({len(threads)} thread):")
    for t in threads:
        fm = t["first_mail"]
        date_str = str(fm.get("occurred_at") or "")[:10]
        subj = (fm.get("subject") or "(senza oggetto)").strip()
        body_excerpt = (fm.get("content") or "").strip()[:450].replace("\n", " ")
        lines.append(f"  ▸ Thread {date_str} — \"{subj}\"")
        lines.append(f"    └ proposta inviata: {body_excerpt}")
        # Eventi successivi del thread (follow-up nostri o risposte loro)
        for ev in t["events"]:
            ev_date = str(ev.get("occurred_at") or "")[:10]
            ev_type = ev.get("type") or ""
            arrow = "→" if ev.get("direction") == "inviata" else "←"
            ev_subj = (ev.get("subject") or "").strip()[:80]
            ev_body = (ev.get("content") or "").strip()[:280].replace("\n", " ")
            lines.append(f"    {arrow} {ev_date} ({ev_type}) {('«'+ev_subj+'» ') if ev_subj else ''}{ev_body}")
        # Esito grezzo derivato da ultima interazione del thread (l'LLM giudicherà l'andazzo)
        last_ev = t["events"][-1] if t["events"] else fm
        is_outgoing_last = last_ev.get("direction") == "inviata"
        n_exchanges = len(t["events"])
        if is_outgoing_last:
            lines.append(f"    └ ultimo evento: nostra mail {str(last_ev.get('occurred_at') or '')[:10]} (in attesa di risposta)")
        else:
            lines.append(f"    └ ultimo evento: loro risposta {str(last_ev.get('occurred_at') or '')[:10]} ({n_exchanges} scambi totali nel thread)")

    return "\n".join(lines)


def discover_venues(scope: str, max_results: int = 8, on_progress=None) -> list[dict]:
    """Deep discovery: streaming + adaptive thinking + effort=high + pause_turn loop.

    Costruisce dossier per le prime 120 venue note (profilo + thread di
    proposte + esiti) e li passa come prior. Tool web_search_20250305 con
    max_uses scalato (~15 query/venue, cap 300). Loop fino a 10 round per
    gestire pause_turn. `on_progress(msg)` riceve eventi user-facing per
    mostrare i log step in UI.
    """
    speakers, profile = _ctx()
    existing_venues = db.list_venues()

    # Costruisci dossier per ogni venue: profilo completo + thread di proposte + esiti.
    # L'LLM legge i raw scambi e deduce da sé l'andazzo (accettato facile, scambio lungo, ghosting).
    dossier_blocks = [_build_venue_dossier(v) for v in existing_venues[:120]]
    existing_summary = "\n\n".join(dossier_blocks)

    task = prompts.DISCOVER_VENUES_TASK.format(scope=scope, max_results=max_results)
    user_text = "\n\n".join([
        f"=== VENUE GIÀ NOTE ({len(existing_venues)} totali, mostrate prime 120) ===\n"
        "Per OGNUNA leggi: profilo + thread di proposte già fatte. Per ogni thread vedrai "
        "la mail iniziale, eventuali follow-up nostri e risposte loro. **Deduci da te** "
        "l'andazzo: accettazione facile, scambio prolungato, ghosting, rifiuto. Usalo come "
        "prior per il voto di compatibilità su nuove proposte alla stessa venue.\n"
        "Cerca attivamente NUOVI eventi/edizioni/bandi non ancora coperti dalle proposte precedenti.\n\n"
        f"{existing_summary}",
        task,
    ])
    # Uso web_search_20250305 (senza dynamic filtering) — NON apre un code_execution sandbox
    # interno, quindi non c'è il bug del container_id pendente dopo pause_turn.
    # max_uses scalato sul numero di venue richieste: ~8 search per venue (drill-down generale +
    # identificazione referente + verifica email). Cap a 150 come ceiling assoluto.
    estimated_searches = min(150, max(24, max_results * 8))
    tools = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": estimated_searches,
    }]
    system_blocks = _build_system_blocks(speakers, profile, include_email_guidelines=True)

    client = _client()
    messages: list[dict] = [{"role": "user", "content": user_text}]
    final_response = None
    max_continuations = 10  # ogni round = max ~30 search; 10 round = fino a 300 search totali

    started_at = time.monotonic()
    cumulative_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    n_rounds_done = 0

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    for attempt in range(max_continuations + 1):
        _emit(f"Round {attempt + 1}/{max_continuations + 1} — LLM al lavoro...")
        with client.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=system_blocks,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": DISCOVER_VENUES_SCHEMA},
            },
        ) as stream:
            announced_thinking = False
            announced_output = False
            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    btype = getattr(block, "type", None)
                    if btype == "server_tool_use":
                        bname = getattr(block, "name", "")
                        if bname == "web_search":
                            _emit("🔎 Sto avviando una ricerca web…")
                        else:
                            _emit(f"🛠️ Tool server: {bname}")
                    elif btype == "thinking" and not announced_thinking:
                        _emit("💭 Sto ragionando sui risultati…")
                        announced_thinking = True
                    elif btype == "text" and not announced_output:
                        _emit("📝 Sto scrivendo l'output strutturato…")
                        announced_output = True
                elif etype == "content_block_stop":
                    snapshot = getattr(stream, "current_message_snapshot", None)
                    if snapshot and getattr(snapshot, "content", None):
                        last = snapshot.content[-1]
                        ltype = getattr(last, "type", None)
                        if ltype == "server_tool_use" and getattr(last, "name", "") == "web_search":
                            inp = getattr(last, "input", {}) or {}
                            query = inp.get("query") if isinstance(inp, dict) else None
                            if query:
                                _emit(f"🔎 Query: «{query}»")
                        elif ltype == "web_search_tool_result":
                            content = getattr(last, "content", []) or []
                            n = len(content) if hasattr(content, "__len__") else 0
                            _emit(f"📊 Risultati ricevuti ({n} link)")
                            announced_thinking = False  # reset, ricomincia il ragionamento
                elif etype == "message_delta":
                    delta = getattr(event, "delta", None)
                    stop_reason = getattr(delta, "stop_reason", None) if delta else None
                    if stop_reason == "pause_turn":
                        _emit("⏸️ Server-side loop limit raggiunto, riprendo automaticamente…")
            response = stream.get_final_message()

        # Accumula usage sui round (pause_turn → più round per la stessa run)
        round_usage = _extract_usage(response)
        for k in cumulative_usage:
            cumulative_usage[k] += round_usage.get(k, 0)
        n_rounds_done = attempt + 1

        final_response = response
        sr = response.stop_reason
        if sr == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if sr == "max_tokens":
            # Il modello ha esaurito il budget durante thinking/output. Non possiamo
            # "continuare" un output strutturato troncato — meglio fallire forte qui
            # con diagnostica chiara invece di restituire JSON parziale.
            _emit("⚠️ Stop per max_tokens — output strutturato non completato.")
        else:
            _emit(f"✓ Stop reason: {sr}")
        break

    duration_ms = int((time.monotonic() - started_at) * 1000)
    meta = {"n_rounds": n_rounds_done, "scope": scope, "max_results": max_results}

    if final_response is None:
        _log_llm_call("discover_venues", MODEL, cumulative_usage, duration_ms,
                      error="no response after max_continuations", meta=meta)
        raise RuntimeError("Discovery: nessuna risposta ricevuta.")

    text = "".join(b.text for b in final_response.content if getattr(b, "type", None) == "text")
    if not text.strip():
        block_types = [getattr(b, "type", "?") for b in (final_response.content or [])]
        sr = getattr(final_response, "stop_reason", "?")
        diag = f"stop_reason={sr}, blocks={block_types}"
        _log_llm_call("discover_venues", MODEL, cumulative_usage, duration_ms,
                      error=f"empty response ({diag})", meta=meta)
        if sr == "max_tokens":
            raise RuntimeError(
                "Discovery: il modello ha esaurito i token (max_tokens) durante "
                "il thinking/output e non ha prodotto JSON. Riprova con meno venue "
                "richieste, o riduci `effort` a 'medium' in lib/claude.py."
            )
        raise RuntimeError(f"Discovery: risposta vuota ({diag}).")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            result = json.loads(text[start:end + 1])
        else:
            _log_llm_call("discover_venues", MODEL, cumulative_usage, duration_ms,
                          error="json parse failed", meta=meta)
            raise RuntimeError(f"Discovery: JSON non parsabile.\n{text[:500]}")

    _log_llm_call("discover_venues", MODEL, cumulative_usage, duration_ms, meta=meta)
    venues_out = result.get("venues", [])
    # Lo schema strict non ammette null nelle stringhe (la grammar compilata diventa
    # troppo grande), quindi il modello restituisce "" per i valori sconosciuti.
    # Normalizziamo "" → None per il flusso downstream, e trasformiamo organizer con
    # name vuoto in None (sentinel "nessun ente padre").
    def _empty_to_none(v):
        if isinstance(v, dict):
            return {k: _empty_to_none(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_empty_to_none(x) for x in v]
        if isinstance(v, str) and v == "":
            return None
        return v
    venues_out = [_empty_to_none(v) for v in venues_out]
    for v in venues_out:
        org = v.get("organizer")
        if isinstance(org, dict) and not org.get("name"):
            v["organizer"] = None
    return venues_out


def enrich_venue(venue: dict) -> dict:
    speakers, profile = _ctx()
    user_text = "\n\n".join([
        prompts.venue_block(venue),
        prompts.ENRICH_VENUE_TASK,
    ])
    return _call_json(
        system_blocks=_build_system_blocks(speakers, profile),
        user_text=user_text,
        schema=ENRICH_VENUE_SCHEMA,
        max_tokens=2048,
        task="enrich_venue",
        model=MODEL_BY_TASK["enrich_venue"],
    )


def suggest_channel(venue: dict, contact: Optional[dict]) -> dict:
    speakers, profile = _ctx()
    user_text = "\n\n".join([
        prompts.venue_block(venue),
        prompts.contact_block(contact),
        prompts.SUGGEST_CHANNEL_TASK,
    ])
    return _call_json(
        system_blocks=_build_system_blocks(speakers, profile),
        user_text=user_text,
        schema=SUGGEST_CHANNEL_SCHEMA,
        max_tokens=1024,
        task="suggest_channel",
        model=MODEL_BY_TASK["suggest_channel"],
    )


def test_connection() -> tuple[bool, str]:
    """Quick ping to verify the API key works."""
    started_at = time.monotonic()
    try:
        client = _client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=20,
            messages=[{"role": "user", "content": "Rispondi solo con la parola: PRONTO"}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        _log_llm_call("test_connection", MODEL, _extract_usage(resp),
                      int((time.monotonic() - started_at) * 1000))
        return True, f"OK — modello {MODEL} risponde: {text.strip()[:50]}"
    except anthropic.AuthenticationError:
        return False, "API key non valida."
    except Exception as e:
        return False, f"Errore: {e}"


# ----- Attachment summarization -----

# MIME accettati nativamente da Anthropic per `document`/`image` blocks.
# Per gli altri ritorniamo uno stub (l'utente compilerà summary_manual a mano).
_NATIVE_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def summarize_attachment(path: str, filename: str, mime: Optional[str]) -> dict:
    """Genera un riassunto strutturato di un allegato leggendolo nativamente.

    PDF: inviato come `document` block. Immagini: inviate come `image` block.
    Formati non supportati nativamente (es. .docx, .pptx, .xlsx): ritorna uno
    stub con `summary` che invita l'utente a compilare a mano `summary_manual`.

    Costo: una sola call per allegato (one-shot al momento dell'upload). Il
    riassunto poi viene cached in DB e riusato in tutte le draft future.
    """
    import base64
    from pathlib import Path as _Path

    speakers, profile = _ctx()
    p = _Path(path)
    norm_mime = (mime or "").lower()

    content: list[dict] = []
    if norm_mime == "application/pdf" or p.suffix.lower() == ".pdf":
        data = p.read_bytes()
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        })
    elif norm_mime in _NATIVE_IMAGE_MIMES or p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix_to_mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                          ".gif": "image/gif", ".webp": "image/webp"}
        media_type = norm_mime if norm_mime in _NATIVE_IMAGE_MIMES else suffix_to_mime[p.suffix.lower()]
        data = p.read_bytes()
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        })
    else:
        # Formato non analizzabile nativamente → stub che invita a compilare manualmente
        return {
            "title": filename,
            "kind": "altro",
            "target_audience": "",
            "key_topics": [],
            "duration_minutes": None,
            "summary": (
                f"(File `{filename}` di tipo `{mime or p.suffix or 'sconosciuto'}` non analizzabile "
                "automaticamente — converti il file in PDF e ri-uploadalo, oppure compila a mano "
                "il campo \"Note utente\" con una breve descrizione che la LLM userà come context.)"
            ),
            "when_to_use": "",
        }

    content.append({"type": "text", "text": prompts.SUMMARIZE_ATTACHMENT_TASK.format(filename=filename)})

    client = _client()
    started_at = time.monotonic()
    model = MODEL_BY_TASK["summarize_attachment"]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    error_msg: Optional[str] = None
    try:
        response = _with_retry(lambda: client.messages.create(
            model=model,
            max_tokens=2048,
            system=_build_system_blocks(speakers, profile),
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": ATTACHMENT_SUMMARY_SCHEMA}},
        ))
        usage = _extract_usage(response)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        _log_llm_call("summarize_attachment", model, usage,
                      int((time.monotonic() - started_at) * 1000), error=error_msg,
                      meta={"filename": filename, "mime": mime})
        raise

    duration_ms = int((time.monotonic() - started_at) * 1000)
    _log_llm_call("summarize_attachment", model, usage, duration_ms,
                  meta={"filename": filename, "mime": mime})

    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise RuntimeError("Risposta LLM vuota.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise RuntimeError(f"Riassunto non parsabile come JSON.\n{text[:500]}")


# ----- Helpers -----

def _build_similar_history(venue: dict, max_similar: int = 3) -> list[dict]:
    """Per la venue corrente, trova le N venue più simili (tag + metadati strutturati)
    e restituisce ciascuna con la sua storia interazioni recente. Score-based ranking."""
    if not venue.get("id"):
        return []
    similar_with_score = db.find_similar_venues_extended(venue, limit=max_similar)
    result = []
    for entry in similar_with_score:
        sv = entry["venue"]
        ints = [
            it for it in db.list_interactions({"venue_id": sv["id"]}, limit=5)
            if not (it.get("direction") == "inviata" and it.get("is_draft"))
        ]
        result.append({"venue": sv, "interactions": ints, "score": entry.get("score")})
    return result
