"""Outreach — chat per venue. Genera mail, incolla risposte, aggiorna stato."""
from __future__ import annotations

import subprocess
import time
from datetime import date, datetime

import streamlit as st
import streamlit.components.v1 as components

from lib import claude, db, pipeline, ui


def attachment_icon(mime: str | None, filename: str | None) -> str:
    """Emoji da mostrare in base al formato (PDF/img/spreadsheet/...)."""
    m = (mime or "").lower()
    f = (filename or "").lower()
    if m.startswith("image/") or f.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
        return "🖼️"
    if m == "application/pdf" or f.endswith(".pdf"):
        return "📄"
    if "spreadsheet" in m or f.endswith((".xls", ".xlsx", ".csv", ".ods")):
        return "📊"
    if "presentation" in m or f.endswith((".ppt", ".pptx", ".key", ".odp")):
        return "🎬"
    if m.startswith("video/"):
        return "🎥"
    if m.startswith("audio/"):
        return "🎵"
    if "word" in m or f.endswith((".doc", ".docx", ".odt", ".rtf")):
        return "📝"
    if f.endswith((".zip", ".rar", ".7z", ".tar", ".gz")):
        return "🗜️"
    return "📎"


def fmt_size(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


ITALIAN_MONTHS = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


def humanize_date(dt: datetime) -> str:
    delta = (date.today() - dt.date()).days
    if delta < 0:
        return f"tra {-delta} giorni"
    if delta == 0:
        return "oggi"
    if delta == 1:
        return "ieri"
    if delta < 7:
        return f"{delta} giorni fa"
    if delta < 14:
        return "una settimana fa"
    if delta < 30:
        weeks = delta // 7
        return f"{weeks} settimane fa"
    if delta < 60:
        return "un mese fa"
    if delta < 365:
        return f"{delta // 30} mesi fa"
    years = delta // 365
    return "un anno fa" if years == 1 else f"{years} anni fa"


def absolute_date(dt: datetime) -> str:
    return f"{dt.day} {ITALIAN_MONTHS[dt.month - 1]}"

st.set_page_config(page_title="Outreach", layout="wide")
ui.apply_global_style()
db.init_db()

# Apertura allegato richiesta via query param: lo apriamo con l'app di sistema
# (xdg-open) — il tool gira locale single-user, quindi il "server" è la macchina
# dell'utente. Niente download del browser: l'app nativa visualizza il file.
if "open_attachment" in st.query_params:
    try:
        _att_id = int(st.query_params["open_attachment"])
        _att = db.get_attachment(_att_id)
        if _att and _att.get("path"):
            subprocess.Popen(
                ["xdg-open", _att["path"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
    del st.query_params["open_attachment"]
    st.rerun()

st.title("Outreach")

# Breadcrumb di ritorno (impostato dal chiamante via session_state["return_to"])
_return_to = st.session_state.get("return_to")
if isinstance(_return_to, dict) and _return_to.get("page"):
    if st.button(f"← Torna a {_return_to.get('label', _return_to['page'])}", key="btn_return_to"):
        target = _return_to["page"]
        st.session_state.pop("return_to", None)
        st.switch_page(target)

# ============== SIDEBAR — picker venue ==============
venues = db.list_venues()

if not venues:
    st.info("Nessuna venue. Importa o aggiungi venue prima.")
    st.stop()

# Default: tutti gli stati selezionati. Memorizzati in session_state per persistere oltre il popover.
if "outreach_status_filter" not in st.session_state:
    st.session_state["outreach_status_filter"] = list(pipeline.PIPELINE_STATES)
if "outreach_search" not in st.session_state:
    st.session_state["outreach_search"] = ""

status_filter = st.session_state["outreach_status_filter"]
search = st.session_state["outreach_search"]

filtered = []
for v in venues:
    if search:
        s = search.lower()
        hay = " ".join(filter(None, [v.get("name"), v.get("city"), v.get("region")])).lower()
        if s not in hay:
            continue
    filtered.append(v)
# Il filtro per stato (effective) viene applicato dopo, una volta caricata la mappa interazioni


def venue_chat_label(v: dict, last_int_map: dict[int, dict] | None = None) -> str:
    last_int = (last_int_map or {}).get(v["id"]) or db.get_last_interaction_for_venue(v["id"])
    state = pipeline.derive_effective_state(v.get("pipeline_status"), last_int)
    emoji = pipeline.PIPELINE_EMOJI[state]
    last_out = db.get_last_outgoing_interaction(v["id"])
    if last_out:
        try:
            occurred = last_out.get("occurred_at")
            if isinstance(occurred, str):
                occurred = datetime.fromisoformat(occurred)
            days = (datetime.now() - occurred).days if occurred else "?"
            tail = f"  ·  ultima mail {days}g fa"
        except Exception:
            tail = ""
    else:
        tail = ""
    city = v.get("city") or "—"
    return f"{emoji}  {v['name']}  ·  {city}{tail}"


# Pre-select dalla redirect (Venue/Discovery page → switch_page con draft_venue_id).
# Lo scriviamo nella chiave del radio, così la selezione sopravvive ai rerun
# (altrimenti dopo un click qualsiasi la radio tornava sulla prima venue).
preselect_id = st.session_state.pop("draft_venue_id", None)

# Pre-fetch ultima interazione per ogni venue → effective state senza N+1 query
sidebar_last_map = db.get_last_interaction_per_venue()

# Filtra per effective state
filtered = [
    v for v in filtered
    if pipeline.derive_effective_state(v.get("pipeline_status"), sidebar_last_map.get(v["id"])) in status_filter
]

options = [v["id"] for v in filtered]

# Se la preselect è fuori dal filtro corrente, riportala dentro: l'utente ha
# appena chiesto esplicitamente di aprire quella venue.
if preselect_id and preselect_id not in options:
    pre_v = db.get_venue(preselect_id)
    if pre_v:
        filtered.insert(0, pre_v)
        options = [v["id"] for v in filtered]

if not filtered:
    st.warning("Nessuna venue corrisponde ai filtri.")
    st.stop()

# Applica la preselect alla chiave del radio PRIMA del render del widget.
if preselect_id and preselect_id in options:
    st.session_state["outreach_selected_venue"] = preselect_id

# Se la chiave persistita punta a una venue non più in `options` (filtro cambiato,
# venue eliminata, ecc.), ripiega sulla prima disponibile.
if st.session_state.get("outreach_selected_venue") not in options:
    st.session_state["outreach_selected_venue"] = options[0]

selected_id = st.session_state.get("outreach_selected_venue") or options[0]

venue = db.get_venue(selected_id)
if not venue:
    st.error("Venue non trovata.")
    st.stop()

# ============== HEADER VENUE ==============
manual_state = pipeline.normalize_state(venue.get("pipeline_status"))
last_int_for_state = db.get_last_interaction_for_venue(venue["id"])
effective_state = pipeline.derive_effective_state(manual_state, last_int_for_state)
header_col1, header_col2 = st.columns([3, 2])
with header_col1:
    st.subheader(venue["name"])
    bits = [
        venue.get("type") or "",
        venue.get("city") or "",
        f"Lingua: {venue.get('language', 'IT')}",
        f"Angolo: {pipeline.label(venue.get('angle'), pipeline.ANGLE_LABELS, fallback='-')}",
    ]
    st.caption("  ·  ".join(b for b in bits if b))
    if venue.get("description"):
        st.markdown(f"_{venue['description']}_")

    quick_links = []
    if venue.get("website"):
        url = venue["website"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        quick_links.append(f"[🌐 Sito]({url})")
    if venue.get("email"):
        quick_links.append(f"📧 `{venue['email']}`")
    if venue.get("social_linkedin"):
        quick_links.append(f"[💼 LinkedIn]({venue['social_linkedin']})")
    if venue.get("social_instagram"):
        quick_links.append(f"[📷 Instagram]({venue['social_instagram']})")
    if venue.get("social_facebook"):
        quick_links.append(f"[📘 Facebook]({venue['social_facebook']})")
    if quick_links:
        st.markdown("  ·  ".join(quick_links))
    venue_organizer = db.get_organizer_for_venue(venue["id"])
    if venue_organizer:
        org_meta = venue_organizer.get("type") or ""
        org_meta_str = f" ({org_meta})" if org_meta else ""
        sibling_n = max(0, len(db.get_venues_for_organizer(venue_organizer["id"])) - 1)
        sib_str = f" · {sibling_n} altre sedi nel DB" if sibling_n else ""
        st.markdown(f"🏛 **Ente:** {venue_organizer['name']}{org_meta_str}{sib_str}")

with header_col2:
    st.markdown(
        f"**Stato corrente:** {pipeline.PIPELINE_EMOJI[effective_state]} "
        f"{pipeline.PIPELINE_LABELS[effective_state].split(' ', 1)[-1]}"
    )
    new_state = st.selectbox(
        "Stato manuale (sovrascritto se mando una nuova mail)",
        options=pipeline.PIPELINE_STATES,
        index=pipeline.PIPELINE_STATES.index(manual_state),
        format_func=lambda s: pipeline.PIPELINE_LABELS[s],
        key=f"state_picker_{venue['id']}",
    )
    if new_state != manual_state:
        db.update_venue(venue["id"], {"pipeline_status": new_state})
        st.rerun()
    if effective_state != manual_state:
        st.caption(
            f"💡 Il manuale è **{pipeline.PIPELINE_LABELS[manual_state]}**, ma l'ultima mail uscente "
            "ha riportato lo stato a *Contattata*."
        )

contacts = db.get_contacts_for_venue(venue["id"])


def _rank_contacts_for_venue(cs: list[dict], current_venue_id: int) -> list[tuple[dict, int, str]]:
    """Ordina i contatti per probabilità di risposta in base allo storico cross-venue.

    Tier (score desc):
      3 = ha già RISPOSTO altrove (≥1 interazione direction='ricevuta' su altra venue)
      2 = è stato CONTATTATO altrove senza risposta (≥1 'inviata' non-draft, 0 'ricevuta')
      1 = ha risposto SU QUESTA venue (storia same-venue)
      0 = mai usato / nessuna interazione rilevante

    Restituisce lista di (contact, score, motivo) ordinata desc."""
    ranked: list[tuple[dict, int, str]] = []
    for c in cs:
        ints = db.get_interactions_for_contact(c["id"], limit=50)
        ints = [it for it in ints if not (it.get("direction") == "inviata" and it.get("is_draft"))]
        cross = [it for it in ints if it.get("venue_id") != current_venue_id]
        same = [it for it in ints if it.get("venue_id") == current_venue_id]
        cross_received = [it for it in cross if it.get("direction") == "ricevuta"]
        cross_sent = [it for it in cross if it.get("direction") == "inviata"]
        same_received = [it for it in same if it.get("direction") == "ricevuta"]
        if cross_received:
            # Trova il nome venue dove ha risposto, se possibile
            v_other = cross_received[0].get("venue_id")
            v_obj = db.get_venue(v_other) if v_other else None
            v_name = v_obj["name"] if v_obj else "altra venue"
            ranked.append((c, 3, f"ha già risposto da {v_name}"))
        elif cross_sent:
            ranked.append((c, 2, f"contattato su {len(cross_sent)} altra/e venue, nessuna risposta"))
        elif same_received:
            ranked.append((c, 1, "ha già risposto su questa venue"))
        else:
            ranked.append((c, 0, "mai usato"))
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


ranked_contacts = _rank_contacts_for_venue(contacts, venue["id"]) if contacts else []
contact_for_draft = ranked_contacts[0][0] if ranked_contacts else None

if ranked_contacts:
    # Bottoni cliccabili che aprono il contatto specifico nella pagina Contatti.
    # Streamlit non supporta link cross-page nel markdown: serve un button reale.
    top_score = ranked_contacts[0][1]
    if top_score >= 2:
        _name = " ".join(filter(None, [contact_for_draft.get("first_name"), contact_for_draft.get("last_name")])).strip() or contact_for_draft.get("email") or "primo contatto"
        st.markdown(f"👥 **Contatti** &nbsp; — &nbsp; ✨ consigliato: **{_name}** ({ranked_contacts[0][2]})")
    else:
        st.markdown("👥 **Contatti:**")
    cols = st.columns(max(len(ranked_contacts), 4))
    for idx, (c, score, reason) in enumerate(ranked_contacts):
        label = (
            " ".join(filter(None, [c.get("first_name"), c.get("last_name")])).strip()
            or c.get("email")
            or "(senza nome)"
        )
        role = f" · {c['role']}" if c.get("role") else ""
        prefix = "✨ " if idx == 0 and score >= 2 else ""
        if cols[idx].button(
            f"{prefix}{label}{role}",
            key=f"open_contact_{c['id']}",
            use_container_width=True,
            help=f"Apri scheda contatto — {reason}",
        ):
            st.session_state["contact_focus_id"] = c["id"]
            st.session_state["return_to"] = {
                "page": "pages/3_Outreach.py", "label": "Outreach",
            }
            st.switch_page("pages/2_Contatti.py")
else:
    st.caption("Nessun contatto collegato. La mail userà l'email generica della venue.")

st.divider()

# ============== CHAT ==============
all_interactions = db.get_interactions_for_venue(venue["id"])
# Separa i draft pending: non vanno in chat history, vanno mostrati come draft da confermare
pending_drafts = [
    it for it in all_interactions
    if it.get("direction") == "inviata" and it.get("is_draft")
]
interactions = [
    it for it in all_interactions
    if not (it.get("direction") == "inviata" and it.get("is_draft"))
]

# Auto-idratazione del draft pending nel session_state (così il pannello editor lo prende)
if pending_drafts and (
    not st.session_state.get("active_draft")
    or st.session_state.get("active_draft_venue_id") != venue["id"]
):
    pd = pending_drafts[-1]
    st.session_state["active_draft"] = {
        "subject": pd.get("subject") or "",
        "body": pd.get("content") or "",
        "channel_suggestion": pd.get("channel") or "email",
        "speaker_choice": "?",
        "tone": "?",
        "language": venue.get("language") or "?",
    }
    st.session_state["active_draft_venue_id"] = venue["id"]
    st.session_state["active_draft_interaction_id"] = pd["id"]
    st.session_state["active_draft_is_followup"] = False
    st.session_state["active_draft_is_pending_db"] = True
    st.session_state["draft_refinement_history"] = [
        {"role": "draft", "content": st.session_state["active_draft"]}
    ]
    # Bump della versione: forza il refresh dei widget editor anche quando si passa
    # da una venue all'altra con draft diverso.
    st.session_state["active_draft_version"] = (
        st.session_state.get("active_draft_version", 0) + 1
    )

if pending_drafts:
    st.warning(
        f"⏳ Draft non confermato (generato da Discovery). "
        "Usa il pannello qui sotto per modificarlo, rigenerarlo o confermarlo come inviato."
    )

if not interactions and not pending_drafts:
    st.info("Nessuna comunicazione ancora. Usa **Crea prima mail** qui sotto per generare la prima.")
elif not interactions:
    pass  # solo draft pending: nessuna chat da mostrare, il warning sopra spiega
else:

    def _escape(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Pre-fetch allegati per ogni interaction in un solo passaggio
    interaction_atts: dict[int, list[dict]] = {}
    for it in interactions:
        atts = db.get_attachments_for_interaction(it["id"])
        if atts:
            interaction_atts[it["id"]] = atts

    # Costruisco TUTTA la chat come singolo blocco HTML — nessun rerun, expand puro CSS+HTML.
    bubbles_html: list[str] = []
    for it in interactions:
        is_us = it.get("direction") == "inviata"
        when_raw = it.get("occurred_at") or ""
        when_dt = None
        if isinstance(when_raw, datetime):
            when_dt = when_raw
        elif isinstance(when_raw, str) and when_raw:
            try:
                when_dt = datetime.fromisoformat(when_raw)
            except ValueError:
                when_dt = None
        when_str = (
            f"{humanize_date(when_dt)} ({absolute_date(when_dt)})" if when_dt else str(when_raw)
        )

        subject = _escape(it.get("subject") or "(senza oggetto)")
        content = _escape(it.get("content") or "")
        side_class = "us" if is_us else "them"

        # Chip allegati legati a questa interaction (icona + filename).
        # Click → query param open_attachment → xdg-open server-side (vedi top file).
        att_html = ""
        atts_for_it = interaction_atts.get(it["id"]) or []
        if atts_for_it:
            chips = "".join(
                f'<a class="attachment-chip" href="?open_attachment={a["id"]}" '
                f'title="Apri {_escape(a.get("filename",""))}" '
                f'onclick="event.stopPropagation();">'
                f'{attachment_icon(a.get("mime"), a.get("filename"))} {_escape(a.get("filename",""))}'
                f'</a>'
                for a in atts_for_it
            )
            att_html = f'<div class="attachments-strip">{chips}</div>'

        bubbles_html.append(
            f'<div class="bubble-row {side_class}">'
            f'<details class="bubble {side_class}">'
            f'<summary>'
            f'<span class="bubble-subject">{subject}</span>'
            f'<span class="bubble-meta">{_escape(when_str)}</span>'
            f'{att_html}'
            f'</summary>'
            f'<div class="bubble-body">{content}</div>'
            f'</details></div>'
        )

    chat_html = (
        """
<style>
.chat-container { padding: 4px 0; }
.bubble-row { display: flex; margin: 6px 0; }
.bubble-row.us { justify-content: flex-end; }
.bubble-row.them { justify-content: flex-start; }
.bubble {
    max-width: 75%;
    padding: 10px 14px;
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.2);
    cursor: pointer;
    transition: background 0.18s;
}
.bubble:hover { filter: brightness(1.15); }
.bubble.us { background: #1e3a5f; border-bottom-right-radius: 4px; }
.bubble.them { background: #3a3a3a; border-bottom-left-radius: 4px; }
.bubble summary {
    list-style: none;
    cursor: pointer;
    user-select: none;
    outline: none;
}
.bubble summary::-webkit-details-marker { display: none; }
.bubble summary::marker { display: none; content: ""; }
.bubble-subject {
    font-weight: 600;
    font-size: 0.95em;
    line-height: 1.3;
    display: block;
}
.bubble-meta {
    font-size: 0.72em;
    opacity: 0.6;
    margin-top: 8px;
    display: block;
}
.bubble.us .bubble-meta { text-align: right; }
.bubble.them .bubble-meta { text-align: left; }
.bubble-body {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid rgba(255,255,255,0.18);
    white-space: pre-wrap;
    font-weight: 400;
    font-size: 0.92em;
    line-height: 1.5;
    overflow: hidden;
    cursor: pointer;
    animation: bubble-expand 0.22s ease-out;
}
.attachments-strip {
    margin-top: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}
.attachment-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 9px;
    border-radius: 12px;
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.18);
    font-size: 0.78em;
    font-weight: 500;
    max-width: 240px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: inherit !important;
    text-decoration: none !important;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
}
.attachment-chip:hover {
    background: rgba(255,255,255,0.20);
    border-color: rgba(255,255,255,0.35);
}
@keyframes bubble-expand {
    from { opacity: 0; max-height: 0; transform: translateY(-4px); }
    to   { opacity: 1; max-height: 2000px; transform: translateY(0); }
}
</style>
<div class="chat-container">
"""
        + "\n".join(bubbles_html)
        + "</div>"
    )

    st.markdown(chat_html, unsafe_allow_html=True)

    # Script in iframe components: aggiunge il click-toggle sul body delle bolle.
    components.html(
        """
<script>
(function() {
    function bind() {
        const doc = window.parent.document;
        const bodies = doc.querySelectorAll('.bubble-body');
        bodies.forEach(function(body) {
            if (body.dataset.toggleBound) return;
            body.dataset.toggleBound = '1';
            body.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                const details = body.closest('details');
                if (details) details.open = false;
            });
        });
    }
    bind();
    // Re-bind se Streamlit ridisegna la pagina
    const observer = new MutationObserver(bind);
    observer.observe(window.parent.document.body, { childList: true, subtree: true });
})();
</script>
""",
        height=0,
    )

st.divider()

# Stato selezione allegati per la prossima mail (consumato sia dalla popover
# sotto le azioni sia dal pannello draft attivo).
sel_key = f"draft_attachment_ids_{venue['id']}"
if sel_key not in st.session_state:
    st.session_state[sel_key] = []

# ============== AZIONI ==============
prior_outgoing = db.count_outgoing_for_venue(venue["id"])
last_outgoing = db.get_last_outgoing_interaction(venue["id"])

action_col1, action_col2, action_col3 = st.columns(3)

# Se il draft attivo viene da un pending già nel db (Discovery), il bottone primario
# diventa "Rigenera draft" — sovrascrive il contenuto via LLM mantenendo la stessa
# interaction_id, così la conferma aggiorna il record giusto.
has_pending_db_draft = bool(st.session_state.get("active_draft_is_pending_db")) and \
    st.session_state.get("active_draft_venue_id") == venue["id"]

with action_col1:
    if has_pending_db_draft:
        action_label = "🔄 Rigenera draft con LLM"
    elif prior_outgoing == 0:
        action_label = "Crea prima mail"
    else:
        action_label = "Crea follow-up"

    if st.button(action_label, key="btn_create_mail", type="primary", use_container_width=True):
        with st.spinner("Generazione draft con LLM..."):
            try:
                selected_ids = list(st.session_state.get(sel_key, []))
                if prior_outgoing == 0:
                    draft = claude.draft_first_email(venue, contact_for_draft, selected_ids)
                else:
                    last_received = next(
                        (i for i in reversed(interactions) if i.get("direction") == "ricevuta"),
                        None,
                    )
                    days_since = 0
                    if last_outgoing:
                        try:
                            occurred = last_outgoing.get("occurred_at")
                            if isinstance(occurred, str):
                                occurred = datetime.fromisoformat(occurred)
                            days_since = (datetime.now() - occurred).days if occurred else 0
                        except Exception:
                            days_since = 0
                    draft = claude.draft_follow_up(
                        venue, contact_for_draft, last_outgoing, last_received, days_since,
                        selected_ids,
                    )
                st.session_state["active_draft"] = draft
                st.session_state["active_draft_venue_id"] = venue["id"]
                st.session_state["active_draft_is_followup"] = prior_outgoing > 0
                st.session_state["draft_refinement_history"] = [
                    {"role": "draft", "content": draft}
                ]
                # Persisti subito sulla riga draft del DB (se esiste): export .md dalla
                # discovery deve riflettere la versione corrente, non la prima generata.
                pending_iid = st.session_state.get("active_draft_interaction_id")
                if pending_iid:
                    db.update_interaction(pending_iid, {
                        "subject": draft.get("subject"),
                        "content": draft.get("body"),
                        "llm_draft": draft.get("body"),
                        "channel": draft.get("channel_suggestion") or "email",
                        "is_draft": 1,
                    })
                # Bump della versione del draft → cambia le chiavi dei widget editor,
                # così Streamlit li ricrea da zero usando i nuovi value= (altrimenti
                # tiene il testo vecchio anche dopo pop dei session_state).
                st.session_state["active_draft_version"] = (
                    st.session_state.get("active_draft_version", 0) + 1
                )
                st.rerun()
            except Exception as e:
                st.error(f"Errore: {e}")

with action_col2:
    write_label = "Scrivi prima mail" if prior_outgoing == 0 else "Scrivi follow-up"
    if st.button(write_label, key="btn_write_mail", use_container_width=True):
        # Draft vuoto: l'utente scrive da zero, ma può comunque chiedere a Claude
        # di intervenire dal pannello di refinement che compare sotto.
        empty_draft = {
            "subject": "",
            "body": "",
            "channel_suggestion": "email",
            "speaker_choice": "?",
            "tone": "?",
            "language": venue.get("language") or "IT",
        }
        st.session_state["active_draft"] = empty_draft
        st.session_state["active_draft_venue_id"] = venue["id"]
        st.session_state["active_draft_is_followup"] = prior_outgoing > 0
        st.session_state["draft_refinement_history"] = [
            {"role": "draft", "content": empty_draft}
        ]
        st.session_state["active_draft_version"] = (
            st.session_state.get("active_draft_version", 0) + 1
        )
        st.rerun()

with action_col3:
    paste_open = st.session_state.get(f"paste_open_{venue['id']}", False)
    if st.button("Incolla risposta", key="btn_paste_response", use_container_width=True):
        st.session_state[f"paste_open_{venue['id']}"] = not paste_open
        st.rerun()


# ============== ANALISI APPROCCIO (web search) ==============
# Solo se abbiamo almeno una mail uscente: prima del primo invio non c'è nulla da analizzare.
if prior_outgoing > 0:
    analysis_key = f"outreach_analysis_{venue['id']}"
    if st.button(
        "🔍 Analizza contatto e approccio (web search)",
        key="btn_analyze_outreach",
        use_container_width=True,
        help=(
            "L'LLM cerca online se il contatto usato è davvero il referente migliore, "
            "e suggerisce se fare follow-up, cambiare contatto o marcare come rifiutata. "
            "L'analisi viene salvata nelle note della venue."
        ),
    ):
        # Risolvi il contatto effettivamente usato per l'outreach: quello legato all'ultima
        # mail uscente. Se la mail era stata mandata a indirizzo generico, last_outgoing
        # ha contact_id=NULL → passiamo None all'LLM.
        contacted_id = (last_outgoing or {}).get("contact_id")
        contacted = db.get_contact(contacted_id) if contacted_id else None

        # Giorni dall'ultima uscente (stesso pattern del bottone Crea follow-up)
        days_since_out = 0
        if last_outgoing:
            try:
                occurred = last_outgoing.get("occurred_at")
                if isinstance(occurred, str):
                    occurred = datetime.fromisoformat(occurred)
                days_since_out = (datetime.now() - occurred).days if occurred else 0
            except Exception:
                days_since_out = 0

        run_id = datetime.now().strftime("%Y-%m-%d %H:%M")
        start_ts = time.time()
        with st.status(f"🔍 Analisi outreach — {run_id} (0s)", expanded=True) as status_box:
            def on_progress(msg: str):
                elapsed = int(time.time() - start_ts)
                mm, ss = divmod(elapsed, 60)
                time_str = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
                status_box.update(label=f"🔍 Analisi outreach — {run_id} ({time_str})")
                st.write(f"`[{time_str}]` {msg}")

            try:
                result = claude.analyze_outreach_approach(
                    venue=venue,
                    current_contact=contacted,
                    venue_contacts=contacts,
                    interactions=interactions,
                    days_since_last_outgoing=days_since_out,
                    on_progress=on_progress,
                )
                # Persisti subito nelle note venue, con marker timestampato.
                summary_text = (result.get("summary") or "").strip()
                if summary_text:
                    marker = f"[Analisi outreach {run_id}]"
                    existing_notes = (venue.get("notes") or "").rstrip()
                    new_notes = (
                        f"{existing_notes}\n\n{marker}\n{summary_text}"
                        if existing_notes else f"{marker}\n{summary_text}"
                    )
                    db.update_venue(venue["id"], {"notes": new_notes})
                st.session_state[analysis_key] = {"run_id": run_id, "result": result}
                elapsed = int(time.time() - start_ts)
                mm, ss = divmod(elapsed, 60)
                time_str = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
                status_box.update(
                    label=f"✓ Analisi completata in {time_str}", state="complete",
                )
                st.rerun()
            except Exception as e:
                elapsed = int(time.time() - start_ts)
                status_box.update(label=f"✗ Errore dopo {elapsed}s: {e}", state="error")
                st.error(f"Errore: {e}")

    # Render del risultato salvato in session_state (sopravvive ai rerun, finché non si chiude)
    saved = st.session_state.get(analysis_key)
    if saved:
        st.divider()
        result = saved["result"]
        head_col1, head_col2 = st.columns([5, 1])
        head_col1.subheader(f"🔍 Analisi outreach del {saved['run_id']}")
        if head_col2.button("Chiudi", key=f"close_analysis_{venue['id']}", use_container_width=True):
            st.session_state.pop(analysis_key, None)
            st.rerun()

        # 1. Riesame fit venue↔progetto (con attività recenti)
        fit = result.get("fit_reassessment") or {}
        if fit:
            with st.container(border=True):
                new_score = fit.get("score")
                old_score = venue.get("acceptance_score")
                score_emoji = {1: "🔴", 2: "🟡", 3: "🟢"}.get(new_score, "❔")
                score_label = {1: "Probabilmente no", 2: "Forse", 3: "Probabilmente sì"}.get(new_score, "?")
                head_left, head_right = st.columns([4, 1])
                head_left.markdown(
                    f"### {score_emoji} Fit aggiornato: **{new_score}/3** — {score_label}"
                )
                if old_score and new_score and old_score != new_score:
                    delta_arrow = "⬆️" if new_score > old_score else "⬇️"
                    head_right.markdown(f"{delta_arrow} _da {old_score}/3_")
                elif old_score:
                    head_right.markdown(f"_invariato ({old_score}/3)_")

                if fit.get("recent_activities"):
                    st.markdown(f"**Attività recenti:** {fit['recent_activities']}")
                if fit.get("fit_rationale"):
                    st.markdown(f"**Perché questo score:** {fit['fit_rationale']}")

                sig_cols = st.columns(2)
                pos = fit.get("positive_signals") or []
                neg = fit.get("negative_signals") or []
                if pos:
                    sig_cols[0].markdown("**✅ Segnali a favore**\n" + "\n".join(f"- {s}" for s in pos))
                if neg:
                    sig_cols[1].markdown("**⚠️ Segnali contro**\n" + "\n".join(f"- {s}" for s in neg))

                if new_score and new_score != old_score:
                    if st.button(
                        f"Aggiorna acceptance_score della venue a {new_score}/3",
                        key=f"btn_update_acceptance_{venue['id']}",
                    ):
                        db.update_venue(venue["id"], {"acceptance_score": new_score})
                        st.toast(f"acceptance_score aggiornato a {new_score}/3", icon="✓")
                        st.rerun()

        # 2. Contatto attuale: ok o no?
        is_best = bool(result.get("is_current_contact_best"))
        if is_best:
            st.success("✓ Il contatto attualmente usato è il referente giusto.")
        else:
            st.warning("⚠ Il contatto attualmente usato NON è il migliore.")
        if result.get("current_contact_assessment"):
            st.markdown(f"**Valutazione contatto attuale:** {result['current_contact_assessment']}")

        # 3. Contatto migliore (se trovato)
        bc = result.get("better_contact") or {}
        if bc.get("name") or bc.get("email") or bc.get("role"):
            with st.container(border=True):
                st.markdown("**👤 Contatto suggerito alternativo:**")
                rows = []
                if bc.get("name"): rows.append(f"- **Nome:** {bc['name']}")
                if bc.get("role"): rows.append(f"- **Ruolo:** {bc['role']}")
                if bc.get("email"):
                    conf = bc.get("email_confidence") or ""
                    conf_label = f" _(confidenza: {conf})_" if conf else ""
                    rows.append(f"- **Email:** `{bc['email']}`{conf_label}")
                if bc.get("phone"): rows.append(f"- **Telefono:** {bc['phone']}")
                if bc.get("source_url"):
                    rows.append(f"- **Fonte:** [{bc['source_url']}]({bc['source_url']})")
                if bc.get("rationale"): rows.append(f"- **Motivazione:** {bc['rationale']}")
                st.markdown("\n".join(rows))

        # 4. Prossima azione consigliata
        action = result.get("next_action") or "follow_up"
        action_labels = {
            "follow_up": "📨 Fare follow-up",
            "switch_contact": "🔄 Cambiare contatto",
            "mark_rejected": "🚫 Marcare come rifiutata",
            "wait": "⏳ Aspettare",
        }
        st.markdown(f"### Prossima azione: {action_labels.get(action, action)}")

        if action == "mark_rejected":
            if result.get("rejection_reasoning"):
                st.markdown(f"**Perché:** {result['rejection_reasoning']}")
            if st.button(
                "Imposta venue come «Rifiutata»",
                key=f"btn_set_rejected_{venue['id']}",
                type="primary",
            ):
                db.update_venue(venue["id"], {"pipeline_status": "rifiutata"})
                st.session_state.pop(analysis_key, None)
                st.rerun()

        elif action in ("follow_up", "wait"):
            plan = result.get("follow_up_plan") or {}
            if plan.get("rationale"):
                st.markdown(f"**Motivazione:** {plan['rationale']}")
            cols_info = st.columns(3)
            cols_info[0].metric("Tempistica", f"{plan.get('timing_days', '?')}g da oggi")
            cols_info[1].metric("Tono", plan.get("tone") or "—")
            cols_info[2].metric("Inviare?", "sì" if plan.get("should_send") else "no")
            if plan.get("subject_hint"):
                st.markdown(f"**Suggerimento oggetto:** «{plan['subject_hint']}»")
            if plan.get("body_hint"):
                st.markdown(f"**Angolo / elemento nuovo:**\n\n> {plan['body_hint']}")

    st.divider()


# ============== PASTE RISPOSTA (se aperto) ==============
if st.session_state.get(f"paste_open_{venue['id']}"):
    st.subheader("Incolla la risposta ricevuta")
    default_subject = ""
    if last_outgoing and last_outgoing.get("subject"):
        last_subj = last_outgoing["subject"]
        default_subject = last_subj if last_subj.lower().startswith("re:") else f"Re: {last_subj}"

    with st.form(f"paste_response_form_{venue['id']}"):
        resp_subject = st.text_input(
            "Oggetto",
            value=default_subject,
            help="Se lasci il default, viene usato 'Re: ' + oggetto della tua mail precedente.",
        )
        resp_text = st.text_area("Testo della risposta", height=240,
                                  placeholder="Incolla qui il messaggio ricevuto.")
        c1, c2, c3 = st.columns(3)
        resp_channel = c1.selectbox(
            "Canale",
            options=pipeline.CHANNELS,
            format_func=lambda x: pipeline.CHANNEL_LABELS.get(x, x),
            index=pipeline.CHANNELS.index((last_outgoing or {}).get("channel") or "email")
            if last_outgoing else 0,
        )
        resp_date = c2.date_input("Data", value=date.today())
        is_auto = c3.checkbox("Risposta automatica")

        submitted = st.form_submit_button("Salva risposta")
        if submitted:
            if not resp_text.strip():
                st.error("Testo vuoto.")
            else:
                int_type = "risposta_automatica" if is_auto else "risposta"
                final_subject = resp_subject.strip() or default_subject or "(senza oggetto)"
                db.insert_interaction({
                    "occurred_at": datetime.combine(
                        resp_date, datetime.now().time()
                    ).isoformat(sep=" ", timespec="seconds"),
                    "channel": resp_channel,
                    "direction": "ricevuta",
                    "venue_id": venue["id"],
                    "contact_id": contact_for_draft["id"] if contact_for_draft else None,
                    "type": int_type,
                    "subject": final_subject,
                    "content": resp_text,
                })
                st.session_state[f"paste_open_{venue['id']}"] = False
                st.rerun()


# ============== DRAFT ATTIVO (se presente) ==============
draft = st.session_state.get("active_draft")
if draft and st.session_state.get("active_draft_venue_id") == venue["id"]:
    st.divider()
    st.subheader("Draft in lavorazione")
    is_followup = st.session_state.get("active_draft_is_followup", False)

    # Per i draft "vuoti" (Scrivi follow-up) non mostriamo info LLM finché l'utente
    # non chiede una revisione: tutti i campi sarebbero "?" e disorientanti.
    is_empty_draft = not (draft.get("body") or "").strip() and not draft.get("rationale")
    if not is_empty_draft:
        info_bits = [
            f"Speaker: **{draft.get('speaker_choice', '?')}**",
            f"Tono: **{draft.get('tone', '?')}**",
            f"Lingua: **{draft.get('language', '?')}**",
            f"Canale: **{draft.get('channel_suggestion', '?')}**",
        ]
        if is_followup and draft.get("timing_suggestion_days") is not None:
            info_bits.append(f"Timing consigliato: **{draft['timing_suggestion_days']}g**")
        st.info("  ·  ".join(info_bits))
        if draft.get("rationale"):
            st.caption(f"Motivazione LLM: {draft['rationale']}")
        if is_followup and draft.get("should_send") is False:
            st.warning("L'LLM sconsiglia di inviare questo follow-up. Considera di modificare lo stato della venue.")

    # Storico revisioni
    history = st.session_state.get("draft_refinement_history", [])
    refinement_turns = [h for h in history if h["role"] == "feedback"]
    if refinement_turns:
        with st.expander(f"Storico revisioni LLM ({len(refinement_turns)})"):
            for i, h in enumerate(history):
                if h["role"] == "feedback":
                    st.markdown(f"**Tu:** {h['content']}")
                else:
                    d = h["content"] if isinstance(h["content"], dict) else {}
                    st.caption(f"→ Draft: «{d.get('subject','')}»")

    # Chat di refinement
    with st.form("refinement_form_chat", clear_on_submit=True):
        feedback = st.text_area(
            "Scrivi a Claude",
            height=70,
            placeholder="Es. 'rendi il tono più diretto' oppure 'scrivimi tu una bozza partendo da quello che ho buttato giù'",
        )
        if st.form_submit_button("Invia ✦"):
            if feedback.strip():
                # Sincronizza l'eventuale testo già scritto/modificato dall'utente nei text_input
                # in modo che Claude veda il vero stato corrente, non la versione iniziale.
                _dv = st.session_state.get("active_draft_version", 0)
                cur_subject = st.session_state.get(f"active_subject_v{_dv}", draft.get("subject", ""))
                cur_body = st.session_state.get(f"active_body_v{_dv}", draft.get("body", ""))
                if cur_subject != draft.get("subject") or cur_body != draft.get("body"):
                    merged = dict(draft)
                    merged["subject"] = cur_subject
                    merged["body"] = cur_body
                    if history and history[-1].get("role") == "draft":
                        history[-1] = {"role": "draft", "content": merged}
                    else:
                        history.append({"role": "draft", "content": merged})
                    st.session_state["active_draft"] = merged
                    draft = merged
                history.append({"role": "feedback", "content": feedback.strip()})
                with st.spinner("Riscrittura..."):
                    try:
                        new_draft = claude.refine_first_email(
                            venue, contact_for_draft, history,
                            list(st.session_state.get(sel_key, [])),
                        )
                        history.append({"role": "draft", "content": new_draft})
                        st.session_state["active_draft"] = new_draft
                        st.session_state["draft_refinement_history"] = history
                        # Stessa logica del bottone Rigenera: se è un draft già nel DB,
                        # la riscrittura va persistita subito così l'export .md vede la nuova versione.
                        pending_iid = st.session_state.get("active_draft_interaction_id")
                        if pending_iid:
                            db.update_interaction(pending_iid, {
                                "subject": new_draft.get("subject"),
                                "content": new_draft.get("body"),
                                "llm_draft": new_draft.get("body"),
                                "channel": new_draft.get("channel_suggestion") or "email",
                                "is_draft": 1,
                            })
                        st.session_state["active_draft_version"] = (
                            st.session_state.get("active_draft_version", 0) + 1
                        )
                        st.rerun()
                    except Exception as e:
                        history.pop()
                        st.session_state["draft_refinement_history"] = history
                        st.error(f"Errore: {e}")

    # Edit diretto.
    # La chiave include la versione: ad ogni rigenerazione/refinement la chiave cambia,
    # Streamlit crea un widget nuovo e prende il value= aggiornato.
    draft_version = st.session_state.get("active_draft_version", 0)
    edited_subject = st.text_input(
        "Oggetto",
        value=draft.get("subject", ""),
        key=f"active_subject_v{draft_version}",
    )
    edited_body = st.text_area(
        "Corpo (modifica liberamente — questo è il testo che verrà salvato come inviato)",
        value=draft.get("body", ""),
        height=380,
        key=f"active_body_v{draft_version}",
    )

    # --- Popover allegati: visibile SOLO quando c'è un draft attivo,
    # cioè quando stiamo davvero componendo una mail in uscita.
    available_attachments = db.list_attachments(venue_id=venue["id"], include_shared=True)
    _n_sel = len(st.session_state[sel_key])
    _pop_label = (
        f"📎 Allegati per questa mail ({_n_sel} selezionati)"
        if _n_sel else "📎 Allegati per questa mail"
    )
    with st.popover(_pop_label, use_container_width=False):
        st.caption(
            "Carica/seleziona file da allegare a questo messaggio in uscita. "
            "Verranno legati alla mail una volta confermata."
        )

        uploader_key = f"att_uploader_v{venue['id']}_{st.session_state.get('att_uploader_version', 0)}"
        uploaded = st.file_uploader(
            "Allega un file (PDF, immagine, doc) — l'LLM ne caricherà una volta sola il riassunto",
            type=None,
            accept_multiple_files=False,
            key=uploader_key,
        )
        share_globally = st.checkbox(
            "Condividi globalmente (disponibile anche per altre venue)",
            value=True,
            key=f"att_share_v{venue['id']}",
        )
        if uploaded is not None:
            with st.spinner(f"Salvataggio e analisi LLM di {uploaded.name}…"):
                try:
                    rec = db.save_attachment(uploaded, venue_id=None if share_globally else venue["id"])
                    try:
                        summary = claude.summarize_attachment(rec["path"], rec["filename"], rec["mime"])
                        db.update_attachment_summary(
                            rec["id"], summary_json=summary, kind=summary.get("kind"),
                        )
                        st.success(
                            f"{attachment_icon(rec['mime'], rec['filename'])} **{rec['filename']}** caricato. "
                            f"Tipo: *{summary.get('kind','-')}*. Selezionato per questa mail."
                        )
                    except Exception as e:
                        st.warning(f"File salvato, ma analisi LLM fallita: {e}.")
                    st.session_state[sel_key] = list(set(st.session_state[sel_key] + [rec["id"]]))
                    st.session_state["att_uploader_version"] = st.session_state.get("att_uploader_version", 0) + 1
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore upload: {e}")

        if not available_attachments:
            st.caption("Nessun allegato in libreria.")
        else:
            st.caption("Seleziona gli allegati da includere:")
            for a in available_attachments:
                cols = st.columns([0.55, 5, 1.2])
                checked = cols[0].checkbox(
                    " ",
                    value=a["id"] in st.session_state[sel_key],
                    key=f"att_pick_{venue['id']}_{a['id']}",
                    label_visibility="collapsed",
                )
                if checked and a["id"] not in st.session_state[sel_key]:
                    st.session_state[sel_key].append(a["id"])
                elif not checked and a["id"] in st.session_state[sel_key]:
                    st.session_state[sel_key].remove(a["id"])

                scope_label = "globale" if a.get("venue_id") is None else "venue"
                summary = a.get("summary") or {}
                title = summary.get("title") or a.get("filename")
                cols[1].markdown(
                    f"**{attachment_icon(a.get('mime'), a.get('filename'))} {title}** "
                    f"<span style='opacity:0.6; font-size:0.85em;'>· {a.get('filename')} · "
                    f"{fmt_size(a.get('size'))} · {scope_label}</span>",
                    unsafe_allow_html=True,
                )
                if cols[2].button("🗑", key=f"att_del_{a['id']}", use_container_width=True):
                    db.delete_attachment(a["id"])
                    if a["id"] in st.session_state[sel_key]:
                        st.session_state[sel_key].remove(a["id"])
                    st.rerun()

    # Allegati selezionati per questa mail (chips visibili nel draft)
    selected_ids_now = list(st.session_state.get(sel_key, []))
    if selected_ids_now:
        sel_atts = [db.get_attachment(aid) for aid in selected_ids_now]
        sel_atts = [a for a in sel_atts if a]
        if sel_atts:
            chips_md = "  ".join(
                f"`{attachment_icon(a.get('mime'), a.get('filename'))} {a.get('filename')}`"
                for a in sel_atts
            )
            st.markdown(f"**📎 Allegati in questa mail:** {chips_md}")
            st.caption(
                "Quando confermi l'invio ti viene ricordato di allegarli su Aruba."
            )

    save_cols = st.columns(2)
    save_channel = save_cols[0].selectbox(
        "Canale usato",
        pipeline.CHANNELS,
        index=pipeline.CHANNELS.index(draft.get("channel_suggestion", "email"))
        if draft.get("channel_suggestion") in pipeline.CHANNELS else 0,
        format_func=lambda x: pipeline.CHANNEL_LABELS.get(x, x),
    )
    save_date = save_cols[1].date_input("Data invio", value=date.today())

    bcols = st.columns(2)
    pending_iid = st.session_state.get("active_draft_interaction_id")

    save_label = "Conferma e salva come inviata" if pending_iid else "Salva mail inviata"
    if bcols[0].button(save_label, key="btn_save_active", type="primary", use_container_width=True):
        derived_type = pipeline.derive_interaction_type("inviata", prior_outgoing)
        occurred_at = datetime.combine(
            save_date, datetime.now().time()
        ).isoformat(sep=" ", timespec="seconds")
        if pending_iid:
            # Conferma del draft esistente: aggiorna la riga, togli il flag draft.
            db.update_interaction(pending_iid, {
                "occurred_at": occurred_at,
                "channel": save_channel,
                "type": derived_type,
                "subject": edited_subject,
                "content": edited_body,
                "is_draft": 0,
                "speaker_choice": draft.get("speaker_choice"),
            })
            saved_iid = pending_iid
        else:
            saved_iid = db.insert_interaction({
                "occurred_at": occurred_at,
                "channel": save_channel,
                "direction": "inviata",
                "venue_id": venue["id"],
                "contact_id": contact_for_draft["id"] if contact_for_draft else None,
                "type": derived_type,
                "subject": edited_subject,
                "content": edited_body,
                "llm_draft": draft.get("body") if edited_body != draft.get("body") else None,
                "speaker_choice": draft.get("speaker_choice"),
            })
        # Persisti la selezione allegati e ricorda all'utente di allegarli su Aruba
        selected_ids_save = list(st.session_state.get(sel_key, []))
        if selected_ids_save:
            db.link_interaction_attachments(saved_iid, selected_ids_save)
            sel_atts = [db.get_attachment(aid) for aid in selected_ids_save]
            sel_atts = [a for a in sel_atts if a]
            files_str = ", ".join(
                f"{attachment_icon(a.get('mime'), a.get('filename'))} {a.get('filename')}"
                for a in sel_atts
            )
            st.toast(f"📎 Ricorda di allegare su Aruba: {files_str}", icon="📎")
        st.session_state[sel_key] = []
        for k in (
            "active_draft", "active_draft_venue_id", "active_draft_is_followup",
            "active_draft_interaction_id", "active_draft_is_pending_db",
            "draft_refinement_history", "active_draft_version",
        ):
            st.session_state.pop(k, None)
        st.rerun()

    discard_label = "Elimina draft" if pending_iid else "Scarta draft"
    if bcols[1].button(discard_label, use_container_width=True):
        # Se è un draft persistito in DB (da Discovery), eliminalo davvero.
        if pending_iid:
            with db.transaction() as conn:
                conn.execute("DELETE FROM interactions WHERE id=?", (pending_iid,))
        for k in (
            "active_draft", "active_draft_venue_id", "active_draft_is_followup",
            "active_draft_interaction_id", "active_draft_is_pending_db",
            "draft_refinement_history", "active_draft_version",
        ):
            st.session_state.pop(k, None)
        st.rerun()


