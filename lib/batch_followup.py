"""Batch follow-up automatico per le venue con follow-up dovuto.

Per ogni venue in stato 'contattata' con ultima mail uscente ≥ 7 giorni fa
(stessa soglia di `app._compute_overdue`), classifica se serve una rivalutazione
web (analyze_outreach_approach) o basta un follow-up diretto, esegue le azioni
e produce un report .md.

Classificazione = regole deterministiche OR router LLM Haiku (vedi `classify`).

Gotcha #5 (CLAUDE.md): per scelta esplicita dell'utente i draft generati dal
batch vengono salvati come INVIATI (is_draft=0). L'md include un avviso in testa
per ricordare la copia-incolla in Aruba subito dopo.
"""
from __future__ import annotations

import re
from datetime import datetime, date
from typing import Callable, Optional

from . import db, claude, pipeline


# ---------- Regole deterministiche ----------

EMAIL_GENERIC_PREFIXES: tuple[str, ...] = (
    "info", "segreteria", "contact", "contatti", "contatto",
    "eventi", "cultura", "comunicazione", "associazione",
    "amministrazione", "direzione", "ufficio", "redazione",
    "press", "stampa", "marketing", "hello", "ciao",
)
_GENERIC_EMAIL_RE = re.compile(
    r"^(" + "|".join(EMAIL_GENERIC_PREFIXES) + r")@",
    re.IGNORECASE,
)

OLD_CONTACT_DAYS = 60
FU_THRESHOLD_FOR_DISCOVERY = 2  # ≥2 mail uscenti senza risposta → rivaluta


def is_generic_email(email: Optional[str]) -> bool:
    if not email:
        return False
    return bool(_GENERIC_EMAIL_RE.match(email.strip()))


def _count_confirmed_outgoing(interactions: list[dict]) -> int:
    return sum(1 for it in interactions if pipeline.is_confirmed_outgoing(it))


def _has_any_received(interactions: list[dict]) -> bool:
    return any(it.get("direction") == "ricevuta" for it in interactions)


def _first_outgoing_date(interactions: list[dict]) -> Optional[date]:
    for it in interactions:
        if not pipeline.is_confirmed_outgoing(it):
            continue
        occ = it.get("occurred_at")
        if isinstance(occ, str):
            try:
                return datetime.fromisoformat(occ).date()
            except Exception:
                return None
        if isinstance(occ, datetime):
            return occ.date()
    return None


def evaluate_rules(
    venue: dict,
    contact: Optional[dict],
    interactions: list[dict],
) -> dict:
    """Ritorna {trigger: bool, flags: list[str]}. Una regola scattata → trigger=True."""
    flags: list[str] = []

    email = (contact or {}).get("email")
    if is_generic_email(email):
        flags.append("email_generica")

    n_out = _count_confirmed_outgoing(interactions)
    if n_out >= FU_THRESHOLD_FOR_DISCOVERY and not _has_any_received(interactions):
        flags.append(f"≥{FU_THRESHOLD_FOR_DISCOVERY}_mail_senza_risposta")

    if not venue.get("acceptance_score"):
        flags.append("score_mai_assegnato")

    if contact:
        no_first = not (contact.get("first_name") or "").strip()
        no_role = not (contact.get("role") or "").strip()
        if no_first or no_role:
            missing = []
            if no_first:
                missing.append("nome")
            if no_role:
                missing.append("ruolo")
            flags.append("contatto_incompleto_(" + "+".join(missing) + ")")
    else:
        flags.append("nessun_contatto_personale")

    first_date = _first_outgoing_date(interactions)
    if first_date:
        days_since_first = (date.today() - first_date).days
        if days_since_first > OLD_CONTACT_DAYS:
            flags.append(f"primo_contatto_{days_since_first}gg_fa")

    return {"trigger": len(flags) > 0, "flags": flags}


# ---------- Classificazione (regole + LLM) ----------

def classify(
    venue: dict,
    contact: Optional[dict],
    interactions: list[dict],
    days_since_last_outgoing: int,
    skip_llm: bool = False,
) -> dict:
    """Classifica con OR: regole o LLM (qualunque dei due) → needs_discovery.

    Output: {needs_discovery, rules: {trigger, flags}, llm: {needs_discovery,
    reason} | None, disagreement: bool}.
    """
    rules = evaluate_rules(venue, contact, interactions)

    llm_out: Optional[dict] = None
    if not skip_llm:
        try:
            n_out = _count_confirmed_outgoing(interactions)
            last_received = next(
                (it for it in reversed(interactions)
                 if it.get("direction") == "ricevuta"),
                None,
            )
            excerpt = None
            if last_received:
                excerpt = (last_received.get("content") or "").strip()[:200]
            llm_out = claude.route_followup_need(
                venue=venue,
                current_contact=contact,
                fu_count=n_out,
                days_since_last_outgoing=days_since_last_outgoing,
                last_received_excerpt=excerpt,
            )
        except Exception as e:
            llm_out = {"needs_discovery": False, "reason": f"(errore LLM: {e})", "_error": True}

    rules_says = rules["trigger"]
    llm_says = bool(llm_out and llm_out.get("needs_discovery"))
    needs = rules_says or llm_says
    disagreement = (
        rules_says != llm_says
        and llm_out is not None
        and not llm_out.get("_error")
    )

    return {
        "needs_discovery": needs,
        "rules": rules,
        "llm": llm_out,
        "disagreement": disagreement,
    }


# ---------- Overdue extraction ----------

def compute_overdue_contexts() -> list[dict]:
    """Per ogni venue 'contattata' con ultima uscente ≥ 7 giorni fa, ritorna il
    contesto completo per il batch (venue, contact, interactions, last_outgoing,
    days_since). Ordinati per ritardo desc."""
    today = date.today()
    out: list[dict] = []
    for v in db.list_venues():
        if v.get("pipeline_status") != "contattata":
            continue
        last = db.get_last_outgoing_interaction(v["id"])
        if not last:
            continue
        try:
            occ = last.get("occurred_at")
            if isinstance(occ, str):
                occ = datetime.fromisoformat(occ)
            days_since = (today - occ.date()).days
        except Exception:
            continue
        if days_since < 7:
            continue

        all_ints = db.get_interactions_for_venue(v["id"])
        interactions = [it for it in all_ints if not pipeline.is_pending_draft(it)]
        contact_id = last.get("contact_id")
        contact = db.get_contact(contact_id) if contact_id else None

        out.append({
            "venue": v,
            "contact": contact,
            "interactions": interactions,
            "last_outgoing": last,
            "days_since": days_since,
        })
    out.sort(key=lambda r: r["days_since"], reverse=True)
    return out


# ---------- Persistenza draft ----------

def _save_outgoing_interaction(
    venue: dict,
    contact: Optional[dict],
    draft: dict,
) -> int:
    """Salva il draft come interaction inviata (is_draft=0), tipo derivato dal
    count attuale di uscenti confermate. Per scelta utente (vedi modulo docstring)
    i draft batch NON restano pending."""
    n_out = _count_confirmed_outgoing(db.get_interactions_for_venue(venue["id"]))
    inter_type = pipeline.derive_interaction_type("inviata", n_out)
    chan = (draft.get("channel_suggestion") or "email").strip() or "email"
    body = draft.get("body") or ""
    subj = draft.get("subject") or ""
    return db.insert_interaction({
        "occurred_at": datetime.now().isoformat(timespec="seconds"),
        "channel": chan,
        "direction": "inviata",
        "venue_id": venue["id"],
        "contact_id": (contact or {}).get("id"),
        "type": inter_type,
        "subject": subj,
        "content": body,
        "llm_draft": body,
        "is_draft": 0,
        "speaker_choice": draft.get("speaker_choice"),
    })


# ---------- Esecuzione batch ----------

ProgressCb = Callable[[int, int, str], None]


def run_batch(
    contexts: list[dict],
    progress_cb: Optional[ProgressCb] = None,
) -> list[dict]:
    """Esegue il batch sui contesti pre-calcolati. Ritorna lista di result dict
    per il report .md. progress_cb(done_count, total, msg)."""
    results: list[dict] = []
    total = len(contexts)

    def _emit(done: int, msg: str) -> None:
        if progress_cb:
            progress_cb(done, total, msg)

    for i, ctx in enumerate(contexts):
        v = ctx["venue"]
        contact = ctx["contact"]
        interactions = ctx["interactions"]
        last_outgoing = ctx["last_outgoing"]
        days_since = ctx["days_since"]

        _emit(i, f"Venue {i+1}/{total} · classificazione · {v['name']}")

        # Riusa la classificazione se già calcolata nella preview
        classification = ctx.get("_classification")
        if classification is None:
            classification = classify(v, contact, interactions, days_since)

        result: dict = {
            "venue_id": v["id"],
            "venue_name": v["name"],
            "venue_city": v.get("city"),
            "state_before": v.get("pipeline_status"),
            "score_before": v.get("acceptance_score"),
            "contact_before": _format_contact(contact),
            "classification": classification,
            "action_taken": None,
            "draft": None,
            "errors": [],
            "score_after": None,
            "contact_after": None,
            "state_after": None,
            "analysis_summary": None,
            "days_since": days_since,
        }

        last_received = next(
            (it for it in reversed(interactions) if it.get("direction") == "ricevuta"),
            None,
        )

        try:
            if classification["needs_discovery"]:
                _emit(i, f"Venue {i+1}/{total} · 🔍 discovery web · {v['name']}")
                analysis = claude.analyze_outreach_approach(
                    venue=v,
                    current_contact=contact,
                    venue_contacts=db.get_contacts_for_venue(v["id"]),
                    interactions=interactions,
                    days_since_last_outgoing=days_since,
                    on_progress=lambda msg, _i=i, _v=v: _emit(
                        _i, f"Venue {_i+1}/{total} · {msg} · {_v['name']}"
                    ),
                )
                result["analysis_summary"] = analysis.get("summary")

                # 1) acceptance_score
                fit = analysis.get("fit_reassessment") or {}
                new_score = fit.get("score")
                if new_score and new_score != v.get("acceptance_score"):
                    db.update_venue(v["id"], {"acceptance_score": new_score})
                    result["score_after"] = new_score

                # 2) next_action
                action = analysis.get("next_action") or "follow_up"
                result["action_taken"] = action

                if action == "mark_rejected":
                    db.update_venue(v["id"], {"pipeline_status": "rifiutata"})
                    result["state_after"] = "rifiutata"
                    result["rejection_reasoning"] = analysis.get("rejection_reasoning")

                elif action == "wait":
                    plan = analysis.get("follow_up_plan") or {}
                    result["wait_rationale"] = plan.get("rationale") or analysis.get("summary")

                elif action in ("follow_up", "switch_contact"):
                    target_contact = contact
                    contact_switched = False

                    if action == "switch_contact":
                        bc = analysis.get("better_contact") or {}
                        if bc.get("name") or bc.get("email"):
                            # Backfill: lega le interazioni NULL al vecchio contatto
                            if contact:
                                db.backfill_null_contact_for_venue(v["id"], contact["id"])
                            full = (bc.get("name") or "").strip()
                            first, last_name = None, None
                            if full:
                                parts = full.split(maxsplit=1)
                                first = parts[0]
                                last_name = parts[1] if len(parts) > 1 else None
                            new_cid = db.insert_contact({
                                "first_name": first,
                                "last_name": last_name,
                                "role": bc.get("role") or None,
                                "email": bc.get("email") or None,
                                "phone": bc.get("phone") or None,
                                "language_pref": v.get("language") or "IT",
                                "notes": (
                                    f"[Adottato come nuovo referente {date.today().isoformat()} via batch follow-up]\n"
                                    f"Fonte: {bc.get('source_url', '—')}\n"
                                    f"Motivazione LLM: {bc.get('rationale', '—')}\n"
                                    f"Email confidence: {bc.get('email_confidence', '?')}"
                                ),
                            })
                            db.link_venue_contact(v["id"], new_cid)
                            target_contact = db.get_contact(new_cid)
                            contact_switched = True
                            result["contact_after"] = _format_contact(target_contact)

                    _emit(i, f"Venue {i+1}/{total} · ✍️ drafting · {v['name']}")
                    draft = claude.draft_follow_up(
                        venue=v,
                        contact=target_contact,
                        last_interaction=last_outgoing,
                        response=last_received,
                        days_since=days_since,
                        analysis_context=analysis,
                        contact_switched=contact_switched,
                    )
                    if draft.get("should_send", True):
                        result["draft"] = draft
                        _save_outgoing_interaction(v, target_contact, draft)
                    else:
                        # Il drafter ha deciso di non inviare → non salvare, logga il rationale
                        result["action_taken"] = "wait"
                        result["wait_rationale"] = draft.get("rationale")
            else:
                # Solo follow-up (no rivalutazione)
                _emit(i, f"Venue {i+1}/{total} · ✍️ drafting · {v['name']}")
                draft = claude.draft_follow_up(
                    venue=v,
                    contact=contact,
                    last_interaction=last_outgoing,
                    response=last_received,
                    days_since=days_since,
                )
                result["action_taken"] = "follow_up"
                if draft.get("should_send", True):
                    result["draft"] = draft
                    _save_outgoing_interaction(v, contact, draft)
                else:
                    result["action_taken"] = "wait"
                    result["wait_rationale"] = draft.get("rationale")

        except Exception as e:
            result["errors"].append(f"{type(e).__name__}: {e}")

        results.append(result)

    _emit(total, f"Completato · {total} venue elaborate")
    return results


def _format_contact(c: Optional[dict]) -> Optional[str]:
    if not c:
        return None
    name = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip()
    email = c.get("email")
    role = c.get("role")
    parts = []
    if name:
        parts.append(name)
    if role:
        parts.append(role)
    if email:
        parts.append(f"<{email}>")
    return " · ".join(parts) if parts else None


# ---------- Report .md ----------

_ACTION_LABEL = {
    "follow_up": "📨 Follow-up",
    "switch_contact": "🔄 Cambio contatto + follow-up",
    "mark_rejected": "🚫 Marcata come rifiutata",
    "wait": "⏳ Attendi (nessuna azione)",
    None: "❌ Errore",
}


def generate_md(results: list[dict]) -> str:
    """Genera il report markdown del batch. Header con avviso copia-Aruba."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_total = len(results)
    n_only_fu = sum(1 for r in results if not r["classification"]["needs_discovery"])
    n_discovery = n_total - n_only_fu
    n_drafts = sum(1 for r in results if r["draft"])
    n_rejected = sum(1 for r in results if r["action_taken"] == "mark_rejected")
    n_switched = sum(1 for r in results if r["action_taken"] == "switch_contact" and r["draft"])
    n_wait = sum(1 for r in results if r["action_taken"] == "wait")
    n_errors = sum(1 for r in results if r["errors"])
    n_disagree = sum(1 for r in results if r["classification"]["disagreement"])

    lines: list[str] = []
    lines.append(f"# Batch follow-up automatico — {now}")
    lines.append("")
    lines.append(
        "> ⚠️ **I draft sono stati salvati nel DB come MAIL INVIATA** "
        "(`is_draft=0`). Copiali subito in Aruba e inviali, altrimenti il "
        "DB sarà disallineato con la realtà (gli LLM successivi crederanno "
        "che siano partite)."
    )
    lines.append("")
    lines.append("## Riepilogo")
    lines.append(f"- Venue elaborate: **{n_total}**")
    lines.append(f"- Solo follow-up: **{n_only_fu}**")
    lines.append(f"- Discovery + follow-up: **{n_discovery}**")
    lines.append(f"- Draft generati e salvati: **{n_drafts}**")
    lines.append(f"- Contatti switchati: **{n_switched}**")
    lines.append(f"- Marcate rifiutate: **{n_rejected}**")
    lines.append(f"- In attesa (nessuna azione): **{n_wait}**")
    lines.append(f"- Errori: **{n_errors}**")
    lines.append(f"- Disaccordi regole↔LLM: **{n_disagree}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    for r in results:
        lines.append(f"## {r['venue_name']}" + (f" — {r['venue_city']}" if r.get("venue_city") else ""))
        lines.append(f"_venue id {r['venue_id']} · {r['days_since']} giorni dall'ultima uscente_")
        lines.append("")
        cls = r["classification"]
        lines.append(f"**Decisione**: {_ACTION_LABEL.get(r['action_taken'], r['action_taken'] or '—')}"
                     + (" · 🔍 con rivalutazione web" if cls["needs_discovery"] else ""))
        rules = cls["rules"]
        lines.append(
            f"- **Regole**: {'✓ ' + ', '.join(rules['flags']) if rules['flags'] else '✗ nessuna scattata'}"
        )
        llm = cls.get("llm")
        if llm:
            sym = "✓" if llm.get("needs_discovery") else "✗"
            err_mark = " ⚠️ errore" if llm.get("_error") else ""
            lines.append(f"- **LLM**: {sym} {llm.get('reason', '')}{err_mark}")
        if cls["disagreement"]:
            lines.append("- ⚠️ **Disaccordo regole ↔ LLM** — utile da osservare per tarare le soglie")

        if r.get("contact_before"):
            lines.append(f"- Contatto precedente: {r['contact_before']}")
        if r.get("contact_after"):
            lines.append(f"- 🔄 Nuovo contatto: **{r['contact_after']}**")
        if r.get("score_before") or r.get("score_after"):
            sb = r.get("score_before") or "—"
            sa = r.get("score_after") or sb
            arrow = f"{sb} → **{sa}**" if r.get("score_after") else f"{sb}"
            lines.append(f"- acceptance_score: {arrow}/3")
        if r.get("state_after"):
            lines.append(f"- Stato pipeline: {r['state_before']} → **{r['state_after']}**")
        if r.get("analysis_summary"):
            lines.append(f"- Sintesi analisi: _{r['analysis_summary']}_")
        if r.get("rejection_reasoning"):
            lines.append(f"- Motivo rifiuto: {r['rejection_reasoning']}")
        if r.get("wait_rationale"):
            lines.append(f"- Motivo attesa: {r['wait_rationale']}")

        if r["draft"]:
            d = r["draft"]
            lines.append("")
            lines.append("**Draft generato:**")
            lines.append(f"- Canale: {d.get('channel_suggestion') or 'email'}")
            if d.get("subject"):
                lines.append(f"- **Oggetto**: {d['subject']}")
            lines.append("")
            lines.append("```")
            lines.append(d.get("body") or "")
            lines.append("```")

        if r["errors"]:
            lines.append("")
            lines.append("**❌ Errori:**")
            for e in r["errors"]:
                lines.append(f"- {e}")

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
