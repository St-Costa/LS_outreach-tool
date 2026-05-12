"""UI Streamlit per il batch follow-up automatico.

Espone `render_batch_button(key_prefix)` da chiamare in `app.py` e
`pages/1_Venue.py`. Tutta la logica è in `lib.batch_followup`; qui c'è solo
il flusso a 3 step gestito via `st.session_state`:

  1. click → calcola classificazioni (regole + LLM router Haiku), mostra preview
  2. conferma → esegue batch con progress bar
  3. mostra risultati + download `.md`

Uso `key_prefix` per consentire al bottone di apparire in più pagine senza
collisioni di chiave widget. Lo stato del batch (preview / results) è in chiavi
session_state globali (non per pagina) — l'utente può iniziare in home e
finire in Venue, lo stato segue.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import streamlit as st

from . import batch_followup as bf


_SS_PREVIEW = "batch_fu_preview"  # list di context dict con `_classification`
_SS_RESULTS = "batch_fu_results"  # list di result dict post-batch
_SS_MD = "batch_fu_md"  # report markdown generato


def _reset_state() -> None:
    for k in (_SS_PREVIEW, _SS_RESULTS, _SS_MD):
        st.session_state.pop(k, None)


def render_batch_button(key_prefix: str = "home") -> None:
    """Render del flusso batch follow-up. Da chiamare in app.py / 1_Venue.py."""
    if _SS_RESULTS in st.session_state:
        _render_results(key_prefix)
        return
    if _SS_PREVIEW in st.session_state:
        _render_preview(key_prefix)
        return
    _render_trigger(key_prefix)


def _render_trigger(key_prefix: str) -> None:
    if st.button(
        "🤖 Follow-up automatici",
        key=f"btn_batch_fu_{key_prefix}",
        help=(
            "Per ogni venue con follow-up dovuto (≥7 giorni dall'ultima mail), "
            "decide automaticamente se serve una rivalutazione web (discovery) o "
            "basta un follow-up diretto, esegue le azioni e salva i draft come "
            "INVIATI nel DB. Poi puoi scaricare un report .md con tutto."
        ),
        use_container_width=True,
    ):
        with st.spinner("Calcolo follow-up dovuti e classificazione…"):
            contexts = bf.compute_overdue_contexts()
            if not contexts:
                st.info("Nessun follow-up dovuto al momento.")
                return
            # Pre-classifica ogni contesto (regole + router LLM) — serve per preview
            progress = st.progress(0.0, text=f"Classificazione 0/{len(contexts)}…")
            for i, ctx in enumerate(contexts):
                ctx["_classification"] = bf.classify(
                    ctx["venue"],
                    ctx["contact"],
                    ctx["interactions"],
                    ctx["days_since"],
                )
                progress.progress(
                    (i + 1) / len(contexts),
                    text=f"Classificazione {i+1}/{len(contexts)}…",
                )
            progress.empty()
            st.session_state[_SS_PREVIEW] = contexts
        st.rerun()


def _render_preview(key_prefix: str) -> None:
    contexts: list[dict] = st.session_state[_SS_PREVIEW]
    n_total = len(contexts)
    n_disc = sum(1 for c in contexts if c["_classification"]["needs_discovery"])
    n_only_fu = n_total - n_disc
    n_disagree = sum(1 for c in contexts if c["_classification"]["disagreement"])

    st.markdown("### 🤖 Anteprima batch follow-up")
    st.markdown(
        f"- **{n_total}** venue con follow-up dovuto\n"
        f"- **{n_only_fu}** richiedono solo follow-up diretto\n"
        f"- **{n_disc}** richiedono rivalutazione web (discovery + follow-up)\n"
        f"- Disaccordi regole↔LLM: **{n_disagree}**"
    )
    # Stima costo grossolana: ogni discovery ≈ 5-10 web_search + analisi Sonnet
    # (~$0.15 cad), ogni FU diretto ≈ draft Sonnet (~$0.03 cad). Il router Haiku
    # è già stato pagato in preview.
    cost_disc = n_disc * 0.15
    cost_fu = n_total * 0.03
    st.caption(
        f"Costo stimato: ~${cost_disc + cost_fu:.2f} "
        f"({n_disc} discovery × ~$0.15 + {n_total} draft × ~$0.03)"
    )

    with st.expander(f"Dettaglio venue ({n_total})", expanded=False):
        for c in contexts:
            v = c["venue"]
            cls = c["_classification"]
            badge = "🔍" if cls["needs_discovery"] else "📨"
            disagree = " ⚠️" if cls["disagreement"] else ""
            flags = ", ".join(cls["rules"]["flags"]) or "—"
            llm = cls.get("llm") or {}
            llm_str = (
                f"LLM: {'✓' if llm.get('needs_discovery') else '✗'} {llm.get('reason', '')}"
                if llm else "LLM: —"
            )
            st.markdown(
                f"{badge}{disagree} **{v['name']}** ({v.get('city') or '—'}) · "
                f"{c['days_since']}gg · regole: {flags} · {llm_str}"
            )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button(
            f"✅ Esegui batch su {n_total} venue",
            key=f"btn_batch_run_{key_prefix}",
            type="primary",
            use_container_width=True,
        ):
            _execute_batch(contexts)
            st.rerun()
    with col_b:
        if st.button(
            "✖️ Annulla",
            key=f"btn_batch_cancel_{key_prefix}",
            use_container_width=True,
        ):
            _reset_state()
            st.rerun()


def _execute_batch(contexts: list[dict]) -> None:
    """Esegue il batch con progress bar visiva e salva i risultati in session."""
    progress = st.progress(0.0, text="Avvio batch…")
    total = len(contexts)

    def cb(done: int, tot: int, msg: str) -> None:
        ratio = done / tot if tot else 1.0
        progress.progress(min(ratio, 1.0), text=msg)

    results = bf.run_batch(contexts, progress_cb=cb)
    progress.empty()

    md = bf.generate_md(results)
    st.session_state[_SS_RESULTS] = results
    st.session_state[_SS_MD] = md
    st.session_state.pop(_SS_PREVIEW, None)
    # Svuota la cache delle venue/last_outgoing così la UI si aggiorna
    st.cache_data.clear()


def _render_results(key_prefix: str) -> None:
    results: list[dict] = st.session_state[_SS_RESULTS]
    md: str = st.session_state[_SS_MD]
    n = len(results)
    n_drafts = sum(1 for r in results if r["draft"])
    n_errors = sum(1 for r in results if r["errors"])
    n_rejected = sum(1 for r in results if r["action_taken"] == "mark_rejected")

    st.success(
        f"✅ Batch completato — {n} venue elaborate · "
        f"{n_drafts} draft salvati · {n_rejected} rifiutate · {n_errors} errori"
    )
    st.warning(
        "⚠️ I draft sono salvati come **mail inviate** nel DB. "
        "Copiali dal report .md e mandali da Aruba subito."
    )

    filename = f"followup_batch_{datetime.now().strftime('%Y-%m-%d_%H%M')}.md"
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.download_button(
            label="📥 Scarica report .md",
            data=md,
            file_name=filename,
            mime="text/markdown",
            key=f"btn_batch_dl_{key_prefix}",
            use_container_width=True,
            type="primary",
        )
    with col_b:
        if st.button(
            "Chiudi",
            key=f"btn_batch_close_{key_prefix}",
            use_container_width=True,
        ):
            _reset_state()
            st.rerun()

    with st.expander("Anteprima report", expanded=False):
        st.markdown(md)
