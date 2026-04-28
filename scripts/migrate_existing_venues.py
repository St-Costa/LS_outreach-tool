"""One-shot migration: identifica Enti, arricchisce metadati venue, crea contatti generici.

Pensato per essere lanciato UNA VOLTA sui ~54 venue già presenti.
Non è un'API riusabile: è uno script ad-hoc.

Fasi:
- A) Grouping per Ente padre via 1 sola call LLM (no web search). Crea Enti, setta organizer_id.
- B) Per ogni venue: enrichment metadati con web search + creazione contatto generico
     (solo se venue ha email valorizzato). Niente ricerca del referente migliore.

Uso:
    python scripts/migrate_existing_venues.py phase-a [--apply]
    python scripts/migrate_existing_venues.py phase-b [--apply] [--limit N]
    python scripts/migrate_existing_venues.py all [--apply] [--limit N]

Senza --apply gira in dry-run (nessuna scrittura).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import db, prompts, settings  # noqa: E402

import anthropic  # noqa: E402


MODEL = "claude-sonnet-4-6"


# ============================================================================
# FASE A — Grouping per Ente
# ============================================================================

PHASE_A_TASK = """COMPITO: ti fornisco una lista di venue (sedi, club, hub, scuole, fiere…) già presenti nel database. Devi raggrupparle per Ente padre comune.

# Cosa è un "Ente padre"
Un Ente è l'organizzazione MADRE che possiede/gestisce/raggruppa più venue. Esempi:
- "Rotary Club Padova Centro" + "Rotary Club Vicenza" + "Rotary Club Verona" → Ente "Distretto Rotary 2060" (network)
- "WIFI Bolzano" + "WIFI Merano" → Ente "Camera di Commercio di Bolzano" (istituzione)
- "Bocconi - Marketing" + "Bocconi - Finance" → Ente "Università Bocconi" (universita)
- "Confindustria Vicenza" + "Confindustria Verona" → Ente "Confindustria" (associazione)

# Regole
1. Una venue appartiene a UN SOLO Ente (o a nessuno).
2. Se non sei sicuro, NON forzare: lascia la venue senza Ente (`null`).
3. Se trovi UNA SOLA venue per un possibile Ente, valuta: ha senso creare l'Ente comunque? Sì se è un'organizzazione chiaramente più grande della singola venue (es. una sede locale di Confindustria); no se la venue È l'organizzazione (es. un hub indipendente).
4. Tipo Ente: associazione | azienda | istituzione | universita | network | hub | altro.
5. Non inventare website o description se non li conosci con certezza dai nomi/contesto forniti.

# Output
JSON con esattamente questa struttura:
{
  "organizers": [
    {
      "name": "nome canonico dell'Ente",
      "type": "associazione|azienda|istituzione|universita|network|hub|altro",
      "website": "url ufficiale o null",
      "description": "1-2 frasi su cosa fa l'Ente, o null",
      "venue_ids": [12, 5, 33]
    }
  ],
  "venues_left_alone": [4, 7, 21]
}

`venues_left_alone` contiene gli id delle venue che NON appartengono a nessun Ente (sono autonome) o per cui hai dubbi.
La somma di tutti i venue_ids + venues_left_alone deve coprire ESATTAMENTE l'input.
"""


PHASE_A_SCHEMA = {
    "type": "object",
    "properties": {
        "organizers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": [
                        "associazione", "azienda", "istituzione", "universita",
                        "network", "hub", "altro",
                    ]},
                    "website": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                    "venue_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "type", "website", "description", "venue_ids"],
                "additionalProperties": False,
            },
        },
        "venues_left_alone": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["organizers", "venues_left_alone"],
    "additionalProperties": False,
}


def _client() -> anthropic.Anthropic:
    key = settings.get_api_key()
    if not key:
        print("ERRORE: API key Anthropic non configurata. Apri Streamlit → Impostazioni.")
        sys.exit(1)
    return anthropic.Anthropic(api_key=key)


def _venue_compact_for_grouping(v: dict) -> str:
    """Riassunto compatto della venue per il prompt di grouping (Fase A)."""
    parts = [f"id={v['id']} | {v['name']}"]
    bits = []
    if v.get("type"):
        bits.append(v["type"])
    if v.get("city"):
        bits.append(v["city"])
    if v.get("region"):
        bits.append(v["region"])
    if bits:
        parts.append(" · ".join(bits))
    # Estraggo solo la prima riga "[Contesto]" delle note (no bozza, troppo verbosa)
    notes = v.get("notes") or ""
    m = re.search(r"\[Contesto\][^\[]*", notes, re.DOTALL)
    if m:
        ctx = re.sub(r"\s+", " ", m.group(0)).strip()[:280]
        parts.append(f"contesto: {ctx}")
    return " | ".join(parts)


def phase_a(apply: bool) -> None:
    print("\n" + "=" * 70)
    print("FASE A — Identificazione Enti (grouping di tutte le venue)")
    print("=" * 70)

    venues = db.list_venues()
    venues_no_org = [v for v in venues if not v.get("organizer_id")]
    print(f"\nVenue totali: {len(venues)}; di cui senza Ente: {len(venues_no_org)}")
    if not venues_no_org:
        print("Nessuna venue da raggruppare. Skip Fase A.")
        return

    compact = "\n".join(_venue_compact_for_grouping(v) for v in venues_no_org)

    user_text = (
        f"=== VENUE DA RAGGRUPPARE ({len(venues_no_org)}) ===\n{compact}\n\n"
        + PHASE_A_TASK
    )

    print(f"\nPrompt size: {len(user_text)} char. Invio a {MODEL}...")
    client = _client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=[
            {"type": "text", "text": prompts.SYSTEM_PROMPT},
        ],
        messages=[{"role": "user", "content": user_text}],
        output_config={
            "format": {"type": "json_schema", "schema": PHASE_A_SCHEMA},
        },
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    if not text.strip():
        print("ERRORE: risposta LLM vuota.")
        sys.exit(1)
    result = json.loads(text)

    orgs = result.get("organizers", [])
    left = result.get("venues_left_alone", [])
    venue_by_id = {v["id"]: v for v in venues_no_org}

    print(f"\n→ LLM propone {len(orgs)} Enti, {len(left)} venue autonome.\n")

    for i, o in enumerate(orgs, 1):
        print(f"  [{i}] Ente: {o['name']} ({o['type']})")
        if o.get("website"):
            print(f"      website: {o['website']}")
        if o.get("description"):
            print(f"      desc: {o['description']}")
        print(f"      venue ({len(o['venue_ids'])}):")
        for vid in o["venue_ids"]:
            v = venue_by_id.get(vid)
            if v:
                print(f"        - id={vid} {v['name']} ({v.get('city','-')})")
            else:
                print(f"        - id={vid} (NON TROVATA — id sconosciuto)")
    if left:
        print(f"\n  Autonome (nessun Ente): {len(left)} venue")
        for vid in left[:20]:
            v = venue_by_id.get(vid)
            if v:
                print(f"    - id={vid} {v['name']}")
        if len(left) > 20:
            print(f"    ... +{len(left) - 20} altre")

    # Sanity check: tutti i venue_id coperti?
    all_assigned = set(left)
    for o in orgs:
        all_assigned.update(o["venue_ids"])
    expected = set(venue_by_id.keys())
    missing = expected - all_assigned
    extra = all_assigned - expected
    if missing or extra:
        print(f"\n⚠️  Coverage incompleta: missing={missing}, extra={extra}")

    if not apply:
        print("\n[DRY-RUN] Nessuna scrittura. Rilancia con --apply per applicare.")
        return

    print(f"\nApplicazione su DB...")
    n_orgs_created = 0
    n_links_set = 0
    for o in orgs:
        # Lookup-or-create per nome (case-sensitive: il LLM ha normalizzato)
        existing = db.get_organizer_by_name(o["name"])
        if existing:
            org_id = existing["id"]
            print(f"  ente già esistente, riusato: {o['name']} (id {org_id})")
        else:
            org_id = db.insert_organizer({
                "name": o["name"],
                "type": o["type"],
                "website": o.get("website"),
                "description": o.get("description"),
                "source": "llm-migration",
            })
            n_orgs_created += 1
            print(f"  creato Ente: {o['name']} (id {org_id})")
        for vid in o["venue_ids"]:
            if vid in venue_by_id:
                db.set_venue_organizer(vid, org_id)
                n_links_set += 1

    print(f"\n✅ Fase A completata: {n_orgs_created} Enti creati, {n_links_set} venue collegate.")


# ============================================================================
# FASE B — Enrichment + creazione contatto generico
# ============================================================================

PHASE_B_TASK_TEMPLATE = """COMPITO: arricchisci i metadati di questa venue (e dell'Ente padre se presente). Usa web search per verificare/completare le info.

Output JSON conforme allo schema. Per ogni campo:
- compilalo se hai trovato un'informazione attendibile,
- mettilo a `null` se non sei sicuro (NON inventare),
- usalo SOLO per completare/correggere, non per sovrascrivere se il dato esistente è già coerente.

NON cercare il referente migliore (nome+ruolo+email di una persona specifica). Niente ricerca contatti.

Sii conservativo: meglio null che dati sbagliati.

=== VENUE ATTUALE ===
{venue_block}

=== ENTE PADRE ATTUALE (se presente) ===
{organizer_block}
"""


PHASE_B_SCHEMA = {
    "type": "object",
    "properties": {
        "venue": {
            "type": "object",
            "properties": {
                "type": {"type": ["string", "null"]},
                "city": {"type": ["string", "null"]},
                "province": {"type": ["string", "null"]},
                "region": {"type": ["string", "null"]},
                "language": {"type": ["string", "null"]},
                "angle": {"type": ["string", "null"]},
                "funding_type": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
                "address": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
            },
            "required": [
                "type", "city", "province", "region", "language", "angle",
                "funding_type", "website", "address", "description",
            ],
            "additionalProperties": False,
        },
        "organizer": {
            "type": ["object", "null"],
            "properties": {
                "website": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "hq_city": {"type": ["string", "null"]},
                "region": {"type": ["string", "null"]},
            },
            "required": ["website", "description", "hq_city", "region"],
            "additionalProperties": False,
        },
    },
    "required": ["venue", "organizer"],
    "additionalProperties": False,
}


def _venue_block_for_phase_b(v: dict) -> str:
    lines = [f"id: {v['id']}", f"name: {v['name']}"]
    for k in ("type", "city", "province", "region", "language", "angle", "funding_type",
              "website", "address", "email"):
        val = v.get(k)
        if val:
            lines.append(f"{k}: {val}")
    if v.get("description"):
        lines.append(f"description: {v['description']}")
    notes = v.get("notes") or ""
    m = re.search(r"\[Contesto\][^\[]*", notes, re.DOTALL)
    if m:
        lines.append(f"context_notes: {m.group(0).strip()[:600]}")
    return "\n".join(lines)


def _organizer_block_for_phase_b(o: dict | None) -> str:
    if not o:
        return "(nessun Ente associato)"
    lines = [f"name: {o['name']}", f"type: {o.get('type','-')}"]
    for k in ("website", "description", "hq_city", "region"):
        if o.get(k):
            lines.append(f"{k}: {o[k]}")
    return "\n".join(lines)


ROLE_KEYWORDS = {
    "segreteria", "redazione", "direzione", "ufficio", "presidenza",
    "segretariato", "comunicazione", "amministrazione", "segreterie",
    "responsabile", "direttore", "presidente", "team",
    "organizzazione", "segretario", "referente", "staff", "consiglio",
}


def _infer_contact_from_notes(notes: str) -> dict:
    """Estrae info contatto dal saluto della bozza.

    Ritorna dict con first_name, last_name, role. Se il saluto è un termine generico
    (Segreteria, Redazione…) → role=quello, nomi None. Se è un nome proprio → split
    in first/last, role generico 'Referente'.
    """
    default = {"first_name": None, "last_name": None, "role": "Segreteria"}
    if not notes:
        return default
    m = re.search(
        r"Gentile\s+([A-ZÀ-Ý][a-zA-Zà-ÿ’']+(?:\s+[A-ZÀ-Ý][a-zA-Zà-ÿ’']+){0,2})\s*[,\n]",
        notes,
    )
    if not m:
        return default
    candidate = m.group(1).strip()
    if len(candidate) < 3 or candidate.lower() in {"sigg", "sigg.ri", "dott", "dott.ri"}:
        return default
    # Se la prima parola è un termine di ruolo conosciuto, è un saluto generico
    first_token = candidate.split()[0].lower()
    if first_token in ROLE_KEYWORDS:
        return {"first_name": None, "last_name": None, "role": candidate}
    # Altrimenti è probabilmente un nome proprio
    parts = candidate.split()
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": None, "role": "Referente"}
    return {"first_name": parts[0], "last_name": " ".join(parts[1:]), "role": "Referente"}


def _enrich_one_venue(client, venue: dict, organizer: dict | None, dry_run: bool) -> dict:
    """Singola call LLM con web search per arricchire venue + Ente padre."""
    user_text = PHASE_B_TASK_TEMPLATE.format(
        venue_block=_venue_block_for_phase_b(venue),
        organizer_block=_organizer_block_for_phase_b(organizer),
    )
    messages = [{"role": "user", "content": user_text}]
    tools = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 8,
    }]

    final_response = None
    for attempt in range(4):  # max 4 round per pause_turn loop
        with client.messages.stream(
            model=MODEL,
            max_tokens=8000,
            system=[{"type": "text", "text": prompts.SYSTEM_PROMPT}],
            messages=messages,
            tools=tools,
            output_config={
                "format": {"type": "json_schema", "schema": PHASE_B_SCHEMA},
            },
        ) as stream:
            for _event in stream:
                pass  # silenzioso; possiamo aggiungere log se serve
            response = stream.get_final_message()
        final_response = response
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break

    if final_response is None:
        raise RuntimeError("Nessuna risposta")
    text = "".join(b.text for b in final_response.content if getattr(b, "type", None) == "text")
    return json.loads(text)


def _is_venue_already_processed(venue: dict) -> bool:
    """Skip se la venue sembra già passata da Fase B: ha tutti i campi chiave riempiti
    E ha già un contatto linkato (o non ha email da linkare)."""
    if not all(venue.get(k) for k in ("region", "angle", "description", "address")):
        return False
    if not venue.get("email"):
        return True  # nessuna email = nessun contatto da creare; basta che i campi siano pieni
    contacts = db.get_contacts_for_venue(venue["id"])
    return len(contacts) > 0


def phase_b(apply: bool, limit: int | None) -> None:
    print("\n" + "=" * 70)
    print("FASE B — Enrichment metadati venue + Ente, creazione contatti generici")
    print("=" * 70)

    all_venues = db.list_venues()
    venues = [v for v in all_venues if not _is_venue_already_processed(v)]
    n_skipped = len(all_venues) - len(venues)
    if n_skipped:
        print(f"\nSaltate {n_skipped} venue già arricchite in passate precedenti.")
    if limit:
        venues = venues[:limit]
        print(f"[LIMIT attivo] arricchirò solo le prime {limit} venue da processare.")

    print(f"Venue da processare: {len(venues)} (di {len(all_venues)} totali)")
    n_with_email = sum(1 for v in venues if v.get("email"))
    print(f"Di cui con email valorizzato (riceveranno un Contact): {n_with_email}")

    if not apply:
        print("\n[DRY-RUN] Mostro solo il piano. Per ogni venue verrà inviata 1 chiamata LLM con web search.")
        print(f"Stima: {len(venues)} call × ~8 web search = ~{len(venues)*8} query totali.")
        for v in venues[:5]:
            print(f"\n  Esempio: {v['name']}")
            inf = _infer_contact_from_notes(v.get('notes') or '')
            print(f"    contatto inferito: first={inf['first_name']} last={inf['last_name']} role={inf['role']}")
        if len(venues) > 5:
            print(f"  ... +{len(venues)-5} altre")
        print("\nRilancia con --apply per eseguire davvero.")
        return

    client = _client()
    n_venue_updated = 0
    n_org_updated = 0
    n_contacts_created = 0
    n_errors = 0

    for i, v in enumerate(venues, 1):
        print(f"\n[{i}/{len(venues)}] {v['name']}")
        # Reload (potrebbe aver cambiato in fasi precedenti)
        venue = db.get_venue(v["id"])
        organizer = db.get_organizer_for_venue(venue["id"]) if venue else None
        try:
            result = _enrich_one_venue(client, venue, organizer, dry_run=False)
        except Exception as e:
            print(f"  ❌ errore: {e}")
            n_errors += 1
            continue

        # Aggiorna venue: solo campi vuoti, mai sovrascrivere
        venue_patch = {}
        for k, val in (result.get("venue") or {}).items():
            if val and not venue.get(k):
                venue_patch[k] = val
        if venue_patch:
            db.update_venue(venue["id"], venue_patch)
            n_venue_updated += 1
            print(f"  venue: aggiornati {list(venue_patch.keys())}")

        # Aggiorna Ente: solo campi vuoti
        if organizer and result.get("organizer"):
            org_patch = {}
            for k, val in result["organizer"].items():
                if val and not organizer.get(k):
                    org_patch[k] = val
            if org_patch:
                db.update_organizer(organizer["id"], org_patch)
                n_org_updated += 1
                print(f"  ente: aggiornati {list(org_patch.keys())}")

        # Crea contatto generico se venue ha email e non c'è già un contatto linkato
        if venue.get("email"):
            existing_contacts = db.get_contacts_for_venue(venue["id"])
            already = any(
                (c.get("email") or "").strip().lower() == venue["email"].strip().lower()
                for c in existing_contacts
            )
            if not already:
                inf = _infer_contact_from_notes(venue.get("notes") or "")
                cid = db.insert_contact({
                    "first_name": inf["first_name"],
                    "last_name": inf["last_name"],
                    "role": inf["role"],
                    "email": venue["email"],
                    "language_pref": venue.get("language") or "IT",
                    "notes": "[creato da migrate_existing_venues] dedotto dal saluto della bozza pre-importata",
                })
                db.link_venue_contact(venue["id"], cid)
                n_contacts_created += 1
                full = " ".join(filter(None, [inf['first_name'], inf['last_name']])) or "(senza nome)"
                print(f"  contatto creato: {full} ({inf['role']}) {venue['email']}")

    print("\n" + "=" * 70)
    print(f"✅ Fase B completata.")
    print(f"   Venue aggiornate: {n_venue_updated}")
    print(f"   Enti aggiornati:  {n_org_updated}")
    print(f"   Contatti creati:  {n_contacts_created}")
    print(f"   Errori:           {n_errors}")


# ============================================================================
# FASE C — Risoluzione residui (re-grouping orphan + address mancanti + email mancanti)
# ============================================================================

PHASE_C_REGROUP_TASK = """COMPITO: ti fornisco una lista di venue ATTUALMENTE SENZA Ente padre. Alcune potrebbero in realtà appartenere a un Ente che la prima passata non ha identificato. Ri-valuta con cura.

Per OGNI venue, decidi:
- Se appartiene chiaramente a un'organizzazione madre (rete, federazione, ateneo, gruppo bancario, network internazionale, ecc.) → restituisci `organizer_name` + `organizer_type`.
- Se la venue È l'organizzazione (è autonoma) → lascia `organizer_name` a null.

Sii più aggressivo nel raggruppare rispetto al default: meglio un Ente in più (che l'utente può sempre eliminare) che lasciare orfane venue chiaramente collegate a un network più grande. Ma NON inventare Enti senza fondamento.

Esempi di Enti che potrebbero emergere:
- BNI Trentino → "BNI Italia" (network)
- HGV → entità autonoma (è l'associazione albergatori altoatesina) → null
- Sparkasse Bolzano → "Sparkasse" (azienda) o autonoma se è la singola sede storica
- TEDx → già esiste come Ente, riusa quel nome se applicabile
- Università → ateneo come Ente
- Confartigianato/CNA/Confcommercio/Confesercenti → autonome se non già collegate

Output JSON:
{
  "venues": [
    {"venue_id": 17, "organizer_name": "Nome Ente o null", "organizer_type": "tipo o null", "organizer_website": "url o null", "organizer_description": "1-2 frasi o null"}
  ]
}

Devi includere tutte le venue dell'input, anche quelle che lasci autonome (organizer_name=null).
"""


PHASE_C_REGROUP_SCHEMA = {
    "type": "object",
    "properties": {
        "venues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "venue_id": {"type": "integer"},
                    "organizer_name": {"type": ["string", "null"]},
                    "organizer_type": {"type": ["string", "null"]},
                    "organizer_website": {"type": ["string", "null"]},
                    "organizer_description": {"type": ["string", "null"]},
                },
                "required": ["venue_id", "organizer_name", "organizer_type",
                             "organizer_website", "organizer_description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["venues"],
    "additionalProperties": False,
}


PHASE_C_FILL_TASK = """COMPITO: trova le informazioni mancanti per questa venue. Usa web search.

Restituisci SOLO i campi richiesti, valorizzati con dati attendibili (sito ufficiale, fonti pubbliche). Se non trovi un dato con confidenza, mettilo a null. NON INVENTARE.

=== VENUE ===
{venue_block}

CAMPI RICHIESTI: {required_fields}
"""

PHASE_C_FILL_SCHEMA = {
    "type": "object",
    "properties": {
        "address": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
    },
    "required": ["address", "email"],
    "additionalProperties": False,
}


def phase_c(apply: bool) -> None:
    print("\n" + "=" * 70)
    print("FASE C — Risoluzione residui (re-grouping + address + email mancanti)")
    print("=" * 70)

    # ----- C1: re-grouping venue ancora autonome -----
    orphans = db.list_orphan_venues()
    print(f"\n[C1] Venue ancora autonome: {len(orphans)}")
    if orphans:
        compact = "\n".join(_venue_compact_for_grouping(v) for v in orphans)
        user_text = (
            f"=== VENUE AUTONOME DA RI-VALUTARE ({len(orphans)}) ===\n{compact}\n\n"
            + PHASE_C_REGROUP_TASK
        )
        client = _client()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=[{"type": "text", "text": prompts.SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": user_text}],
            output_config={
                "format": {"type": "json_schema", "schema": PHASE_C_REGROUP_SCHEMA},
            },
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        result = json.loads(text)
        proposals = result.get("venues", [])
        proposed_orgs = [p for p in proposals if p.get("organizer_name")]
        print(f"  → LLM propone Ente per {len(proposed_orgs)}/{len(proposals)} venue.")
        for p in proposed_orgs:
            v = next((v for v in orphans if v["id"] == p["venue_id"]), None)
            if not v:
                continue
            print(f"    - id={v['id']} {v['name']:50} → Ente: {p['organizer_name']} ({p.get('organizer_type','?')})")
        if apply:
            n_new_orgs = 0
            n_links = 0
            for p in proposed_orgs:
                v = next((v for v in orphans if v["id"] == p["venue_id"]), None)
                if not v:
                    continue
                existing = db.get_organizer_by_name(p["organizer_name"])
                if existing:
                    org_id = existing["id"]
                else:
                    raw_type = (p.get("organizer_type") or "altro").lower()
                    type_norm = next(
                        (t for t in db.ORGANIZER_TYPES if t in raw_type or raw_type in t),
                        "altro",
                    )
                    org_id = db.insert_organizer({
                        "name": p["organizer_name"],
                        "type": type_norm,
                        "website": p.get("organizer_website"),
                        "description": p.get("organizer_description"),
                        "source": "llm-migration-c",
                    })
                    n_new_orgs += 1
                db.set_venue_organizer(v["id"], org_id)
                n_links += 1
            print(f"  ✅ Creati {n_new_orgs} nuovi Enti, linkate {n_links} venue.")
    else:
        print("  Nessuna venue autonoma residua.")

    # ----- C2: address e email mancanti -----
    all_venues = db.list_venues()
    no_address = [v for v in all_venues if not v.get("address")]
    no_email_no_contact = []
    for v in all_venues:
        if v.get("email"):
            continue
        if db.get_contacts_for_venue(v["id"]):
            continue
        no_email_no_contact.append(v)

    targets = list({v["id"]: v for v in (no_address + no_email_no_contact)}.values())
    print(f"\n[C2] Venue da arricchire (address/email mancanti): {len(targets)}")
    for v in targets:
        miss = []
        if not v.get("address"):
            miss.append("address")
        if not v.get("email") and not db.get_contacts_for_venue(v["id"]):
            miss.append("email")
        print(f"  - id={v['id']} {v['name'][:42]:44} missing: {miss}")

    if not apply:
        print("\n[DRY-RUN] Per applicare lancia con --apply.")
        return

    if not targets:
        print("  Nessun residuo address/email.")
        print("\n✅ Fase C completata.")
        return

    client = _client()
    n_addr_filled = 0
    n_email_contacts = 0
    for v in targets:
        venue = db.get_venue(v["id"])
        required = []
        if not venue.get("address"):
            required.append("address (sede fisica/legale)")
        if not venue.get("email") and not db.get_contacts_for_venue(venue["id"]):
            required.append("email (di contatto, anche generica)")
        if not required:
            continue
        print(f"\n[C2] {venue['name']} — cerco: {required}")
        user_text = PHASE_C_FILL_TASK.format(
            venue_block=_venue_block_for_phase_b(venue),
            required_fields=", ".join(required),
        )
        messages = [{"role": "user", "content": user_text}]
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
        try:
            final = None
            for _round in range(3):
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=4000,
                    system=[{"type": "text", "text": prompts.SYSTEM_PROMPT}],
                    messages=messages,
                    tools=tools,
                    output_config={
                        "format": {"type": "json_schema", "schema": PHASE_C_FILL_SCHEMA},
                    },
                ) as stream:
                    for _e in stream:
                        pass
                    resp = stream.get_final_message()
                final = resp
                if resp.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": resp.content})
                    continue
                break
            text = "".join(b.text for b in final.content if getattr(b, "type", None) == "text")
            data = json.loads(text)
        except Exception as e:
            print(f"  ❌ errore: {e}")
            continue

        addr = data.get("address")
        email = data.get("email")
        if addr and not venue.get("address"):
            db.update_venue(venue["id"], {"address": addr})
            n_addr_filled += 1
            print(f"  ✓ address: {addr}")
        if email and not venue.get("email") and not db.get_contacts_for_venue(venue["id"]):
            db.update_venue(venue["id"], {"email": email})
            inf = _infer_contact_from_notes(venue.get("notes") or "")
            cid = db.insert_contact({
                "first_name": inf["first_name"],
                "last_name": inf["last_name"],
                "role": inf["role"],
                "email": email,
                "language_pref": venue.get("language") or "IT",
                "notes": "[creato da migrate_existing_venues phase-c] email trovata via web search",
            })
            db.link_venue_contact(venue["id"], cid)
            n_email_contacts += 1
            full = " ".join(filter(None, [inf['first_name'], inf['last_name']])) or "(senza nome)"
            print(f"  ✓ email + contatto creato: {full} ({inf['role']}) {email}")

    print(f"\n✅ Fase C completata.")
    print(f"   Address riempiti:                {n_addr_filled}")
    print(f"   Email + contatti creati:         {n_email_contacts}")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["phase-a", "phase-b", "phase-c", "all"])
    parser.add_argument("--apply", action="store_true",
                        help="Applica davvero (default: dry-run, nessuna scrittura)")
    parser.add_argument("--limit", type=int, default=None,
                        help="(solo Fase B) processa solo le prime N venue")
    args = parser.parse_args()

    db.init_db()

    if args.phase in ("phase-a", "all"):
        phase_a(apply=args.apply)
    if args.phase in ("phase-b", "all"):
        phase_b(apply=args.apply, limit=args.limit)
    if args.phase in ("phase-c", "all"):
        phase_c(apply=args.apply)


if __name__ == "__main__":
    main()
