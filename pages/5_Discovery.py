"""Discovery — ricerca approfondita nuove venue + bulk creazione mail."""
from __future__ import annotations

import time
from datetime import datetime, date

import streamlit as st

from lib import claude, db, pipeline, ui


def build_mails_markdown(mails: list[dict], header_ts: str | None = None) -> str:
    """Costruisce un singolo markdown con tutte le mail separate da '---'.

    Struttura per ogni mail: OGGETTO / (testo) / CORPO / (testo).
    Le mail con is_draft=True (non ancora confermate dall'utente) vengono
    contrassegnate da un alert in cima alla sezione.
    """
    title_ts = header_ts or datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(mails)
    n_confirmed = sum(1 for m in mails if not m.get("is_draft"))
    lines: list[str] = [
        f"# Mail da inviare — {title_ts}",
        "",
        f"Totale: {total}  ·  Confermate: **{n_confirmed}/{total}**",
        "",
        "---",
        "",
    ]
    for i, m in enumerate(mails, 1):
        recipient_full = m["recipient"]
        if m.get("recipient_name"):
            extra = m["recipient_name"]
            if m.get("recipient_role"):
                extra += f" — {m['recipient_role']}"
            recipient_full = f"{extra} <{m['recipient']}>"
        section: list[str] = [f"## {i}. {m['venue_name']}", ""]
        if m.get("is_draft"):
            section.extend([
                "> ⚠️ **DRAFT NON VERIFICATO** — generato automaticamente dalla discovery,",
                "> non ancora rivisto/approvato a mano. Aprire la venue in Outreach e confermare.",
                "",
            ])
        section.extend([
            f"**Destinatario:** {recipient_full}",
            f"**Canale:** {m.get('channel', 'email')}",
            "",
            "OGGETTO",
            m.get("subject", ""),
            "",
            "CORPO",
            m.get("body", ""),
            "",
            "---",
            "",
        ])
        lines.extend(section)
    return "\n".join(lines)

st.set_page_config(page_title="Discovery", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Discovery venue (deep search)")
st.caption(
    "Ricerca approfondita: l'LLM cerca venue che matchano il profilo progetto, fa drill-down "
    "per identificare il referente migliore, l'email diretta, e suggerisce il canale ottimale "
    "per il primo contatto."
)


# Avvisi pre-discovery
profile = db.get_project_profile()
if db.project_profile_is_empty(profile):
    st.warning(
        "Profilo progetto vuoto → la discovery girerà solo sui profili speaker. "
        "Per risultati migliori, vai su **Strategia** prima di lanciare."
    )

speakers = db.get_speakers()
empty_speakers = [s for s in speakers if not (s.get("bio") or "").strip()]
if empty_speakers:
    st.warning(
        f"Profili speaker vuoti: {', '.join(s['name'] for s in empty_speakers)}. "
        "Vai su **Speaker** per compilarli."
    )


ITALIAN_REGIONS = [
    "Trentino-Alto Adige", "Veneto", "Lombardia", "Emilia-Romagna", "Friuli-Venezia Giulia",
    "Piemonte", "Liguria", "Valle d'Aosta", "Toscana", "Umbria", "Marche", "Lazio",
    "Abruzzo", "Molise", "Campania", "Basilicata", "Puglia", "Calabria", "Sicilia", "Sardegna",
]


# ---------- Lancio nuova run ----------
st.header("Lancia nuova discovery")

scope_type = st.radio(
    "Scope geografico",
    ["Trentino-Alto Adige (default)", "Regioni limitrofe (Veneto+Lombardia+FVG)",
     "Tutta Italia", "Personalizzato"],
)
if scope_type == "Trentino-Alto Adige (default)":
    scope = "Trentino-Alto Adige"
elif scope_type == "Regioni limitrofe (Veneto+Lombardia+FVG)":
    scope = "Veneto, Lombardia, Friuli-Venezia Giulia, Trentino-Alto Adige"
elif scope_type == "Tutta Italia":
    scope = "Tutta Italia"
else:
    selected = st.multiselect("Regioni", ITALIAN_REGIONS, default=["Veneto"])
    scope = ", ".join(selected) if selected else "Trentino-Alto Adige"

max_results = st.slider("Numero massimo venue da cercare", 3, 15, 6,
                          help="Meno venue = più drill-down per ognuna. 5-8 è il sweet spot.")

st.caption(f"**Scope:** {scope}")
st.caption(
    "Tempo atteso: 1-3 minuti. La ricerca usa thinking adaptive + effort high + più round di "
    "web search (l'LLM continua finché non ha le info necessarie su ogni venue)."
)

if st.button("Lancia discovery", key="btn_run_discovery", type="primary"):
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    db.start_discovery_run(run_id, scope, max_results)
    start_ts = time.time()
    log_entries: list[dict] = []  # raccolta degli step per persistenza

    with st.status(f"🔍 Discovery in corso — {run_id} (0s)", expanded=True) as status_box:
        st.write(f"**Scope:** {scope}")
        st.write(f"**Max venue:** {max_results}")

        def on_progress(msg: str):
            elapsed = int(time.time() - start_ts)
            mm, ss = divmod(elapsed, 60)
            time_str = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
            status_box.update(label=f"🔍 Discovery in corso — {run_id} ({time_str})")
            st.write(f"`[{time_str}]` {msg}")
            log_entries.append({"ts": elapsed, "msg": msg})
            # Persisti il log ogni 5 step così la run è "ispezionabile" anche durante l'esecuzione
            if len(log_entries) % 5 == 0:
                db.append_discovery_log(run_id, log_entries)

        on_progress("Avvio chiamata LLM con web search…")

        try:
            venues_found = claude.discover_venues(scope, max_results=max_results, on_progress=on_progress)
            for v in venues_found:
                db.insert_discovery_candidate(run_id, v)
            db.complete_discovery_run(run_id, len(venues_found))
            db.append_discovery_log(run_id, log_entries)
            elapsed = int(time.time() - start_ts)
            mm, ss = divmod(elapsed, 60)
            time_str = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
            status_box.update(
                label=f"✓ Completata in {time_str} — {len(venues_found)} venue trovate",
                state="complete",
            )
            st.rerun()
        except Exception as e:
            db.fail_discovery_run(run_id, str(e))
            db.append_discovery_log(run_id, log_entries)
            elapsed = int(time.time() - start_ts)
            mm, ss = divmod(elapsed, 60)
            time_str = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
            status_box.update(label=f"✗ Fallita dopo {time_str}: {e}", state="error")
            st.error(f"Errore dopo {time_str}: {e}")

st.divider()


# ---------- Storico run ----------
st.header("Run precedenti")
runs = db.list_discovery_runs()
if not runs:
    st.info("Nessuna discovery run finora. Lancia la prima sopra.")
else:
    from datetime import datetime as _dt
    import json as _json
    now = _dt.now()

    status_emoji = {
        "running": "🔄",
        "completed": "✓",
        "errored": "✗",
        "canceled": "⊘",
    }

    def _format_runtime(started: str | None, completed: str | None) -> str:
        try:
            s = _dt.fromisoformat(started) if isinstance(started, str) else started
            c = _dt.fromisoformat(completed) if isinstance(completed, str) else (completed or now)
            if not s:
                return "—"
            secs = int((c - s).total_seconds())
            mm, ss = divmod(max(0, secs), 60)
            return f"{mm}m {ss:02d}s" if mm else f"{ss}s"
        except Exception:
            return "—"

    def _format_dt(s: str | None) -> str:
        if not s:
            return "—"
        try:
            d = _dt.fromisoformat(s)
            return d.strftime("%d %b %Y, %H:%M")
        except Exception:
            return s

    for run in runs:
        s_emoji = status_emoji.get(run.get("status", "completed"), "·")
        s_status = run.get("status", "completed")
        title_dt = _format_dt(run.get("started_at"))

        if s_status == "running":
            duration = _format_runtime(run.get("started_at"), None)
            title = f"{s_emoji} Run {title_dt} — IN CORSO ({duration})"
        elif s_status == "errored":
            duration = _format_runtime(run.get("started_at"), run.get("completed_at"))
            title = f"{s_emoji} Run {title_dt} — FALLITA dopo {duration}"
        elif s_status == "canceled":
            title = f"{s_emoji} Run {title_dt} — CANCELLATA"
        else:
            duration = _format_runtime(run.get("started_at"), run.get("completed_at"))
            title = f"{s_emoji} Run {title_dt} — completata in {duration}"

        with st.expander(title, expanded=(s_status == "running")):
            # Header info
            info_cols = st.columns(4)
            info_cols[0].caption("Scope")
            info_cols[0].write(run.get("scope") or "—")
            info_cols[1].caption("Max venue")
            info_cols[1].write(str(run.get("max_results") or "—"))
            info_cols[2].caption("Tempo")
            info_cols[2].write(
                _format_runtime(run.get("started_at"), run.get("completed_at"))
                if s_status != "running"
                else f"{_format_runtime(run.get('started_at'), None)} (in corso)"
            )
            info_cols[3].caption("Venue identificate")
            n = run.get("n") or 0
            n_acc = run.get("accepted") or 0
            info_cols[3].write(f"{n} ({n_acc} accettate)")

            if run.get("error_message"):
                st.error(f"Errore: {run['error_message']}")

            # Step della ricerca (replay del log)
            log_raw = run.get("log_json")
            log_steps = []
            if log_raw:
                try:
                    log_steps = _json.loads(log_raw)
                except Exception:
                    log_steps = []
            if log_steps:
                st.markdown("**Step eseguiti:**")
                log_box = st.container(height=300 if s_status != "running" else 400)
                with log_box:
                    for entry in log_steps:
                        ts = entry.get("ts", 0)
                        mm, ss = divmod(int(ts), 60)
                        time_str = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
                        st.write(f"`[{time_str}]` {entry.get('msg','')}")
            elif s_status == "running":
                st.info("La run è in corso. Gli step vengono salvati periodicamente — riapri la scheda per aggiornare.")

            # Azioni
            run_mails = db.get_mails_for_discovery_run(run["run_id"])
            if run_mails:
                n_total = len(run_mails)
                n_confirmed = sum(1 for m in run_mails if not m.get("is_draft"))
                pending_mails = [m for m in run_mails if m.get("is_draft")]

                st.markdown(f"**Confermate {n_confirmed}/{n_total}**")
                if pending_mails:
                    st.caption(
                        f"⚠️ {len(pending_mails)} draft non ancora confermati — clicca per aprire e modificare/confermare:"
                    )
                    # Una riga per draft pending: link a Outreach
                    for m in pending_mails:
                        if st.button(
                            f"✏️ {m['venue_name']}",
                            key=f"open_draft_{run['run_id']}_{m['venue_id']}",
                            use_container_width=False,
                        ):
                            st.session_state["draft_venue_id"] = m["venue_id"]
                            st.session_state["return_to"] = {
                                "page": "pages/5_Discovery.py",
                                "label": f"Discovery run #{run['run_id']}",
                            }
                            st.switch_page("pages/3_Outreach.py")
                else:
                    st.caption("✓ Tutti i draft sono stati confermati.")

                action_cols = st.columns([1, 1, 4])
                run_md = build_mails_markdown(run_mails, header_ts=run["run_id"])
                dl_label = (
                    f"⬇️ Scarica {n_total} mail ({n_confirmed}/{n_total} confermate)"
                    if pending_mails
                    else f"⬇️ Scarica {n_total} mail"
                )
                action_cols[0].download_button(
                    label=dl_label,
                    data=run_md,
                    file_name=f"mail_run_{run['run_id']}.md",
                    mime="text/markdown",
                    key=f"dl_run_{run['run_id']}",
                    use_container_width=True,
                    help=(
                        "Le mail non confermate sono incluse ma marcate come 'DRAFT NON VERIFICATO'."
                        if pending_mails else None
                    ),
                )
            else:
                # Distinguo i tre casi possibili:
                # - run ancora con candidati pending → invita a generare draft
                # - tutti i candidati rifiutati → spiega
                # - nessun candidato in DB → run probabilmente fallita o vuota
                run_cands = db.list_discovery_candidates(run_id=run["run_id"])
                if any(c["status"] == "pending" for c in run_cands):
                    n_pend = sum(1 for c in run_cands if c["status"] == "pending")
                    st.info(
                        f"📨 Nessun draft ancora generato per questa run "
                        f"({n_pend} candidat{'o' if n_pend == 1 else 'i'} pending). "
                        f"Scorri sotto alla sezione 'Candidati da rivedere', seleziona "
                        f"questa run dal menu, scegli i candidati e premi "
                        f"**'📨 Genera e salva N draft'** per produrre le mail."
                    )
                elif run_cands:
                    n_rej = sum(1 for c in run_cands if c["status"] == "rejected")
                    n_acc = sum(1 for c in run_cands if c["status"] == "accepted")
                    st.caption(
                        f"Nessuna mail generata. Candidati: {n_acc} accettati senza draft, "
                        f"{n_rej} rifiutati."
                    )
                else:
                    st.caption("(nessuna venue identificata in questa run)")

            if s_status == "running":
                # Avvisa se la run è orfana da troppo tempo
                try:
                    started = run.get("started_at")
                    if isinstance(started, str):
                        started = _dt.fromisoformat(started)
                    if started:
                        minutes = (now - started).total_seconds() / 60
                        if minutes > 8:
                            st.warning(
                                f"Run marcata 'in corso' da {int(minutes)} minuti. "
                                "Probabile interruzione (tab chiusa, errore). Puoi marcarla come fallita."
                            )
                except Exception:
                    pass
                if action_cols[1].button("Marca fallita", key=f"cancel_{run['run_id']}", use_container_width=True):
                    db.cancel_discovery_run(run["run_id"])
                    st.rerun()

    selected_run = st.selectbox(
        "Apri run",
        options=[r["run_id"] for r in runs],
        format_func=lambda r: f"{r} ({next(x['n'] for x in runs if x['run_id'] == r)} candidati)",
    )

    candidates = db.list_discovery_candidates(run_id=selected_run)
    pending = [c for c in candidates if c["status"] == "pending"]
    accepted = [c for c in candidates if c["status"] == "accepted"]
    rejected = [c for c in candidates if c["status"] == "rejected"]

    st.write(f"Pending: **{len(pending)}** · Accettati: **{len(accepted)}** · Rifiutati: **{len(rejected)}**")

    if pending:
        st.subheader(f"Candidati da rivedere ({len(pending)})")

        # Filtro soglia minima acceptance_score (1=basso, 3=alto). "?" = score mancante.
        # Salvato in session_state per persistere tra i rerun delle checkbox.
        f_col1, f_col2 = st.columns([1, 5])
        with f_col1:
            min_score = st.selectbox(
                "Score minimo",
                options=[1, 2, 3],
                index=st.session_state.get("disc_min_score_idx", 0),
                format_func=lambda s: {1: "🔴 1+", 2: "🟡 2+", 3: "🟢 3"}[s],
                key="disc_min_score",
                help="Mostra solo candidati con acceptance_score ≥ soglia. I candidati senza score sono inclusi solo a soglia 1.",
            )
        st.session_state["disc_min_score_idx"] = [1, 2, 3].index(min_score)

        def _passes_threshold(c) -> bool:
            s = c.get("payload", {}).get("acceptance_score")
            if min_score == 1:
                return True  # mostra tutto, inclusi senza score
            if not isinstance(s, int):
                return False
            return s >= min_score

        pending_filtered = [c for c in pending if _passes_threshold(c)]
        with f_col2:
            n_hidden = len(pending) - len(pending_filtered)
            if n_hidden > 0:
                st.caption(f"{len(pending_filtered)}/{len(pending)} mostrati ({n_hidden} sotto soglia).")
            else:
                st.caption(f"{len(pending_filtered)}/{len(pending)} mostrati.")
        pending = pending_filtered

        # Ordina per acceptance_score: 3 → 2 → 1 → ?
        def _score_key(c):
            s = c.get("payload", {}).get("acceptance_score")
            return -s if s in (1, 2, 3) else 0
        pending.sort(key=_score_key)

        # ----- Quick-select buttons -----
        def _select_by_score(target_score):
            for c in pending:
                s = c.get("payload", {}).get("acceptance_score")
                key = f"sel_{c['id']}"
                if target_score is None or s == target_score:
                    st.session_state[key] = True

        def _deselect_all():
            for c in pending:
                st.session_state[f"sel_{c['id']}"] = False

        sb_cols = st.columns(5)
        if sb_cols[0].button("✓ Tutte le 🟢 3", use_container_width=True):
            _select_by_score(3); st.rerun()
        if sb_cols[1].button("✓ Tutte le 🟡 2", use_container_width=True):
            _select_by_score(2); st.rerun()
        if sb_cols[2].button("✓ Tutte le 🔴 1", use_container_width=True):
            _select_by_score(1); st.rerun()
        if sb_cols[3].button("✓ Tutte", use_container_width=True):
            _select_by_score(None); st.rerun()
        if sb_cols[4].button("✗ Deseleziona", use_container_width=True):
            _deselect_all(); st.rerun()

        st.divider()

        # ----- Lista candidati con checkbox -----
        for cand in pending:
            payload = cand.get("payload", {})
            # Backwards compat: vecchie run hanno best_contact, nuove hanno contacts[]
            contacts_list = payload.get("contacts") or []
            if not contacts_list and payload.get("best_contact"):
                bc = payload["best_contact"]
                contacts_list = [{
                    "name": bc.get("name"), "role": bc.get("role"),
                    "email": bc.get("email"), "phone": None,
                    "email_confidence": bc.get("email_confidence", "?"),
                    "is_primary": True, "rationale": bc.get("rationale", ""),
                }]
            primary = next((c for c in contacts_list if c.get("is_primary")), contacts_list[0] if contacts_list else {})
            channel = payload.get("recommended_first_channel") or "email"
            score = payload.get("acceptance_score")
            score_emoji = {1: "🔴", 2: "🟡", 3: "🟢"}.get(score, "⚪")

            chk_col, info_col = st.columns([1, 11])
            with chk_col:
                st.checkbox(
                    " ",
                    key=f"sel_{cand['id']}",
                    value=st.session_state.get(f"sel_{cand['id']}", False),
                    label_visibility="collapsed",
                )
            with info_col:
                header_bits = [
                    f"{score_emoji} **{score or '?'}**",
                    f"**{payload.get('name','?')}**",
                    f"_{payload.get('city','-')}_",
                    f"({payload.get('type','-')})",
                ]
                if payload.get("is_known_venue_new_event"):
                    header_bits.append("🆕 NUOVO EVENTO")
                st.markdown("  ·  ".join(header_bits))
                with st.expander("Dettagli", expanded=False):
                    if payload.get("description"):
                        st.markdown(f"**Descrizione:** {payload['description']}")
                    if payload.get("fit_with_project"):
                        st.markdown(f"**Match col progetto:** {payload['fit_with_project']}")
                    if payload.get("acceptance_rationale"):
                        st.markdown(f"**Voto:** {score_emoji} {score or '?'}/3 — *{payload['acceptance_rationale']}*")
                    if contacts_list:
                        st.markdown(f"**Contatti ({len(contacts_list)}):**")
                        for c in contacts_list:
                            badge = "⭐ primario" if c.get("is_primary") else "secondario"
                            line = f"  · **{badge}** "
                            if c.get("name"):
                                line += f"{c['name']}"
                                if c.get("role"):
                                    line += f" — *{c['role']}*"
                            elif c.get("role"):
                                line += f"*{c['role']}*"
                            st.markdown(line)
                            if c.get("email"):
                                st.write(f"     📧 `{c['email']}` (confidenza: {c.get('email_confidence','?')})")
                            if c.get("phone"):
                                st.write(f"     📞 `{c['phone']}`")
                            if c.get("rationale"):
                                st.caption(f"     {c['rationale']}")
                    channel_label = pipeline.CHANNEL_LABELS.get(channel, channel)
                    st.markdown(f"**Canale consigliato:** {channel_label}")
                    if payload.get("channel_rationale"):
                        st.caption(payload["channel_rationale"])
                    socials = payload.get("social_handles") or {}
                    relevant_socials = {k: v for k, v in socials.items() if v}
                    if relevant_socials and channel != "email":
                        st.markdown("**Social:** " + ", ".join(f"{k}: {v}" for k, v in relevant_socials.items()))
                    if payload.get("deadline_text"):
                        st.warning(f"⏰ Deadline: {payload['deadline_text']}")
                    meta_cols = st.columns(3)
                    meta_cols[0].caption(f"Sito: {payload.get('website') or '—'}")
                    meta_cols[1].caption(f"Lingua: {payload.get('language','-')}")
                    meta_cols[2].caption(f"Angolo: {pipeline.label(payload.get('angle'), pipeline.ANGLE_LABELS, fallback='-')}")
                    # Ente padre (formato nuovo: organizer dict; vecchio: organizer_name)
                    org_pl = payload.get("organizer")
                    if not org_pl and payload.get("organizer_name"):
                        org_pl = {"name": payload["organizer_name"], "type": payload.get("organizer_type"),
                                  "website": payload.get("organizer_website"), "is_known": False, "contacts": []}
                    if org_pl and org_pl.get("name"):
                        existing_org = db.get_organizer_by_name(org_pl["name"])
                        if existing_org:
                            org_status = "✓ già nel DB"
                        elif org_pl.get("is_known"):
                            org_status = "🔁 noto da altre venue (verrà unificato)"
                        else:
                            org_status = "🆕 verrà creato"
                        st.markdown(f"🏛 **Ente padre:** {org_pl['name']} _{org_pl.get('type','?')}_ — {org_status}")
                        if org_pl.get("description"):
                            st.caption(f"   {org_pl['description']}")
                        org_meta = []
                        for k in ("hq_city", "region", "language"):
                            if org_pl.get(k):
                                org_meta.append(f"{k}: {org_pl[k]}")
                        if org_meta:
                            st.caption("   " + " · ".join(org_meta))
                        org_contacts = org_pl.get("contacts") or []
                        if org_contacts:
                            st.markdown(f"   **Contatti Ente ({len(org_contacts)}):**")
                            for oc in org_contacts:
                                line = f"     · {oc.get('name','')}"
                                if oc.get("role"):
                                    line += f" — *{oc['role']}*"
                                if oc.get("email"):
                                    line += f" `{oc['email']}`"
                                st.markdown(line)
                                if oc.get("rationale"):
                                    st.caption(f"       {oc['rationale']}")

                    if st.button("Rifiuta candidato", key=f"rej_{cand['id']}"):
                        db.update_discovery_candidate_status(cand["id"], "rejected")
                        st.session_state.pop(f"sel_{cand['id']}", None)
                        st.rerun()

        st.divider()

        # ----- Bulk action -----
        selected_cands = [c for c in pending if st.session_state.get(f"sel_{c['id']}", False)]
        n_sel = len(selected_cands)

        st.markdown(
            f"### {n_sel} venue selezionate · "
            f"verrà generata una mail **draft** per ognuna · "
            f"stato venue → ⏳ Contattata"
        )
        st.caption(
            "Le mail vengono salvate come **draft non verificati**. "
            "Apri ciascuna venue dalla scheda Outreach (link più sotto, sotto la run) per confermarla "
            "o modificarla. Lo stato venue è già **Contattata** ma il bordo nella pagina Venue resta "
            "tratteggiato finché non confermi."
        )

        if st.button(
            f"📨 Genera e salva {n_sel} draft" if n_sel else "Seleziona almeno una venue",
            type="primary",
            disabled=n_sel == 0,
            use_container_width=True,
        ):
            progress = st.progress(0)
            status = st.empty()
            results = {"success": 0, "skipped_dup": [], "errors": [], "mails": []}
            now_ts = datetime.now().isoformat(sep=" ", timespec="seconds")

            for i, cand in enumerate(selected_cands):
                payload = cand.get("payload", {})
                name = payload.get("name") or "Senza nome"
                status.text(f"[{i+1}/{n_sel}] {name}")
                try:
                    existing = db.get_venue_by_name(name)
                    if existing:
                        results["skipped_dup"].append(name)
                        db.update_discovery_candidate_status(cand["id"], "accepted")
                        st.session_state.pop(f"sel_{cand['id']}", None)
                        progress.progress((i + 1) / n_sel)
                        continue

                    # Backwards compat: vecchie run hanno best_contact, nuove hanno contacts[]
                    contacts_payload = payload.get("contacts") or []
                    if not contacts_payload and payload.get("best_contact"):
                        bc = payload["best_contact"]
                        contacts_payload = [{
                            "name": bc.get("name"), "role": bc.get("role"),
                            "email": bc.get("email"), "phone": None,
                            "email_confidence": bc.get("email_confidence", "?"),
                            "is_primary": True, "rationale": bc.get("rationale", ""),
                        }]
                    primary_contact = next(
                        (c for c in contacts_payload if c.get("is_primary")),
                        contacts_payload[0] if contacts_payload else {},
                    )
                    chan_local = payload.get("recommended_first_channel") or "email"
                    socials_local = payload.get("social_handles") or {}
                    conf_local = primary_contact.get("email_confidence", "?")
                    venue_email = primary_contact.get("email") or None

                    venue_data = {
                        "name": name,
                        "type": payload.get("type"),
                        "city": payload.get("city"),
                        "province": payload.get("province"),
                        "region": payload.get("region"),
                        "email": venue_email,
                        "website": payload.get("website"),
                        "language": payload.get("language") or "IT",
                        "angle": payload.get("angle"),
                        "description": payload.get("description"),
                        "deadline_text": payload.get("deadline_text"),
                        "acceptance_score": payload.get("acceptance_score"),
                        "notes": (
                            f"[Da discovery {selected_run}]\n"
                            f"Match col progetto: {payload.get('fit_with_project','')}\n"
                            f"Voto compatibilità: {payload.get('acceptance_score','?')}/3 — {payload.get('acceptance_rationale','')}\n"
                            f"Canale consigliato: {chan_local} — {payload.get('channel_rationale','')}\n"
                            f"Email confidence (primario): {conf_local}\n"
                            f"Contatti trovati: {len(contacts_payload)}"
                        ),
                        "source": "llm-discovery",
                        "pipeline_status": "contattata",  # impostato a contattata: la mail è registrata come inviata
                    }
                    if chan_local != "email":
                        venue_data["social_instagram"] = socials_local.get("instagram")
                        venue_data["social_facebook"] = socials_local.get("facebook")
                        venue_data["social_linkedin"] = socials_local.get("linkedin")

                    # Lookup-or-create Ente padre — formato nuovo (organizer dict) + fallback vecchio
                    org_pl = payload.get("organizer")
                    if not org_pl and payload.get("organizer_name"):
                        org_pl = {
                            "name": payload["organizer_name"],
                            "type": payload.get("organizer_type"),
                            "website": payload.get("organizer_website"),
                            "contacts": [],
                        }
                    org_id = None
                    if org_pl and (org_pl.get("name") or "").strip():
                        org_name_raw = org_pl["name"].strip()
                        existing_org = db.get_organizer_by_name(org_name_raw)
                        if existing_org:
                            org_id = existing_org["id"]
                            # Arricchimento conservativo: riempi solo i campi vuoti dell'Ente esistente
                            patch = {}
                            for k in ("type", "website", "hq_city", "hq_province", "region",
                                      "language", "description", "social_linkedin",
                                      "social_instagram", "social_facebook"):
                                if org_pl.get(k) and not existing_org.get(k):
                                    patch[k] = org_pl[k]
                            if patch:
                                db.update_organizer(org_id, patch)
                        else:
                            org_id = db.insert_organizer({
                                "name": org_name_raw,
                                "type": org_pl.get("type"),
                                "website": org_pl.get("website"),
                                "hq_city": org_pl.get("hq_city"),
                                "hq_province": org_pl.get("hq_province"),
                                "region": org_pl.get("region") or payload.get("region"),
                                "language": org_pl.get("language") or payload.get("language") or "IT",
                                "description": org_pl.get("description"),
                                "social_linkedin": org_pl.get("social_linkedin"),
                                "social_instagram": org_pl.get("social_instagram"),
                                "social_facebook": org_pl.get("social_facebook"),
                                "source": "llm-discovery",
                            })
                        venue_data["organizer_id"] = org_id

                    new_venue_id = db.insert_venue(venue_data)

                    # Crea TUTTI i contatti venue, link al venue, primary salvato per la mail
                    new_contact_id = None  # primary, usato per la draft email
                    for c_pl in contacts_payload:
                        if not (c_pl.get("name") or c_pl.get("email")):
                            continue
                        full = (c_pl.get("name") or "").strip()
                        first, last = None, None
                        if full:
                            parts = full.split(maxsplit=1)
                            first = parts[0]
                            last = parts[1] if len(parts) > 1 else None
                        cid = db.insert_contact({
                            "first_name": first,
                            "last_name": last,
                            "role": c_pl.get("role"),
                            "email": c_pl.get("email"),
                            "phone": c_pl.get("phone"),
                            "language_pref": payload.get("language") or "IT",
                            "notes": (
                                f"[Da discovery {selected_run}]\n"
                                f"Ruolo nel venue: {'primario' if c_pl.get('is_primary') else 'secondario'}\n"
                                f"{c_pl.get('rationale','')}\n"
                                f"Email confidence: {c_pl.get('email_confidence','?')}"
                            ),
                        })
                        db.link_venue_contact(new_venue_id, cid)
                        if c_pl.get("is_primary") and new_contact_id is None:
                            new_contact_id = cid
                    if new_contact_id is None and contacts_payload:
                        # Fallback: prendi il primo contatto creato come primary per la draft
                        first_existing = db.get_contacts_for_venue(new_venue_id)
                        if first_existing:
                            new_contact_id = first_existing[0]["id"]

                    # Crea contatti a livello Ente, se proposti
                    if org_id and org_pl and org_pl.get("contacts"):
                        for oc_pl in org_pl["contacts"]:
                            if not (oc_pl.get("name") or oc_pl.get("email")):
                                continue
                            full = (oc_pl.get("name") or "").strip()
                            first, last = None, None
                            if full:
                                parts = full.split(maxsplit=1)
                                first = parts[0]
                                last = parts[1] if len(parts) > 1 else None
                            ocid = db.insert_contact({
                                "first_name": first,
                                "last_name": last,
                                "role": oc_pl.get("role"),
                                "email": oc_pl.get("email"),
                                "phone": oc_pl.get("phone"),
                                "language_pref": org_pl.get("language") or payload.get("language") or "IT",
                                "notes": (
                                    f"[Da discovery {selected_run}] contatto a livello Ente\n"
                                    f"{oc_pl.get('rationale','')}\n"
                                    f"Email confidence: {oc_pl.get('email_confidence','?')}"
                                ),
                            })
                            db.link_organizer_contact(org_id, ocid)

                    # Genera draft email con LLM (sfrutta tutti i context: profilo, speaker, venue arricchita, contatto)
                    fresh_venue = db.get_venue(new_venue_id)
                    fresh_contact = db.get_contact(new_contact_id) if new_contact_id else None
                    draft = claude.draft_first_email(fresh_venue, fresh_contact)

                    # Salva interazione come draft non confermato.
                    # is_draft=1 → l'utente deve aprire la venue in Outreach e confermare/modificare
                    # prima che venga considerata realmente inviata.
                    db.insert_interaction({
                        "occurred_at": now_ts,
                        "channel": chan_local,
                        "direction": "inviata",
                        "venue_id": new_venue_id,
                        "contact_id": new_contact_id,
                        "type": "prima_mail",
                        "subject": draft.get("subject"),
                        "content": draft.get("body"),
                        "llm_draft": draft.get("body"),
                        "is_draft": 1,
                        "speaker_choice": draft.get("speaker_choice"),
                    })

                    db.update_discovery_candidate_status(cand["id"], "accepted")
                    st.session_state.pop(f"sel_{cand['id']}", None)
                    results["success"] += 1
                    results["mails"].append({
                        "venue_name": name,
                        "venue_id": new_venue_id,
                        "recipient": primary_contact.get("email") or venue_email or "(email mancante)",
                        "recipient_name": primary_contact.get("name") or "",
                        "recipient_role": primary_contact.get("role") or "",
                        "channel": chan_local,
                        "subject": draft.get("subject") or "",
                        "body": draft.get("body") or "",
                        "is_draft": True,
                    })
                except Exception as e:
                    results["errors"].append(f"{name}: {e}")

                progress.progress((i + 1) / n_sel)

            status.empty()
            progress.empty()

            if results["success"]:
                st.success(
                    f"✓ Generati {results['success']} draft. Stato venue → ⏳ Contattata. "
                    f"Apri ogni venue in Outreach per confermare/modificare la mail prima dell'invio."
                )
            if results["skipped_dup"]:
                st.info(f"Saltate (già nel DB): {', '.join(results['skipped_dup'])}")
            if results["errors"]:
                st.error("Errori:\n" + "\n".join(results["errors"]))

            # Memorizza il run_id dell'ultimo bulk: il download qui sotto rilegge
            # sempre lo stato corrente dal db (così riflette i draft confermati nel frattempo).
            if results["mails"]:
                st.session_state["last_bulk_run_id"] = selected_run
                st.session_state["last_bulk_ts"] = datetime.now().strftime("%Y-%m-%d_%H-%M")
            st.balloons()

        # ----- Bulk delete (rimuove dal DB le venue selezionate) -----
        # Conferma a 2 step via session_state per evitare cancellazioni accidentali.
        confirm_key = f"confirm_del_{selected_run}"
        st.caption(
            "Oppure rimuovi definitivamente le venue selezionate dal DB "
            "(non verranno contattate e non resteranno tra i candidati)."
        )
        if not st.session_state.get(confirm_key):
            if st.button(
                f"🗑️ Cancella dal DB {n_sel} venue selezionate" if n_sel else "Seleziona almeno una venue",
                disabled=n_sel == 0,
                use_container_width=True,
                key=f"del_btn_{selected_run}",
            ):
                st.session_state[confirm_key] = True
                st.rerun()
        else:
            st.warning(
                f"Confermi la cancellazione definitiva di **{n_sel}** candidati? "
                "L'operazione non è reversibile."
            )
            c1, c2 = st.columns(2)
            if c1.button("✅ Sì, cancella", type="primary", use_container_width=True, key=f"del_yes_{selected_run}"):
                ids = [c["id"] for c in selected_cands]
                deleted = db.delete_discovery_candidates(ids)
                for cid in ids:
                    st.session_state.pop(f"sel_{cid}", None)
                st.session_state.pop(confirm_key, None)
                st.success(f"✓ Rimossi {deleted} candidati dal DB.")
                st.rerun()
            if c2.button("Annulla", use_container_width=True, key=f"del_no_{selected_run}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()

# ============== EXPORT MARKDOWN delle mail dell'ultimo bulk ==============
last_run_id = st.session_state.get("last_bulk_run_id")
if last_run_id:
    last_mails = db.get_mails_for_discovery_run(last_run_id)
    if last_mails:
        st.divider()
        st.subheader("📥 Esporta mail dell'ultimo bulk")

        n_total = len(last_mails)
        n_confirmed = sum(1 for m in last_mails if not m.get("is_draft"))
        st.progress(
            n_confirmed / n_total if n_total else 1.0,
            text=f"Confermate: {n_confirmed}/{n_total}",
        )
        if n_confirmed < n_total:
            pending_last = [m for m in last_mails if m.get("is_draft")]
            st.caption(
                f"⚠️ {len(pending_last)} draft non confermati — clicca per aprire e modificare/confermare:"
            )
            for m in pending_last:
                if st.button(
                    f"✏️ {m['venue_name']}",
                    key=f"open_bulk_draft_{last_run_id}_{m['venue_id']}",
                    use_container_width=False,
                ):
                    st.session_state["draft_venue_id"] = m["venue_id"]
                    st.session_state["return_to"] = {
                        "page": "pages/5_Discovery.py",
                        "label": f"Discovery run #{last_run_id}",
                    }
                    st.switch_page("pages/3_Outreach.py")
            st.caption(
                "I draft sono inclusi anche nel .md (marcati 'DRAFT NON VERIFICATO')."
            )

        md_content = build_mails_markdown(last_mails, header_ts=st.session_state.get("last_bulk_ts"))
        filename = f"mail_da_inviare_{st.session_state.get('last_bulk_ts', 'export')}.md"

        col_dl, col_clear = st.columns([3, 1])
        col_dl.download_button(
            label=f"⬇️ Scarica {filename}",
            data=md_content,
            file_name=filename,
            mime="text/markdown",
            use_container_width=True,
        )
        if col_clear.button("Chiudi", use_container_width=True):
            st.session_state.pop("last_bulk_run_id", None)
            st.session_state.pop("last_bulk_ts", None)
            st.rerun()

        with st.expander("Anteprima markdown"):
            st.code(md_content, language="markdown")

    if accepted:
        with st.expander(f"Accettati ({len(accepted)})"):
            for cand in accepted:
                st.write(f"- {cand.get('payload', {}).get('name')}")
    if rejected:
        with st.expander(f"Rifiutati ({len(rejected)})"):
            for cand in rejected:
                st.write(f"- {cand.get('payload', {}).get('name')}")
