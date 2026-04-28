"""Costi LLM — telemetria token e stima costo per le chiamate Anthropic.

I token vengono registrati in `llm_calls` ad ogni call (lib/claude.py).
Qui mostriamo aggregati per task/modello e stima USD usando i listini in
`PRICING_USD_PER_MTOK`. La conversione in EUR usa un tasso impostabile.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import claude, db, ui

st.set_page_config(page_title="Costi LLM", layout="wide")
ui.apply_global_style()
db.init_db()

st.title("Costi LLM")
st.caption(
    "Aggregati di token e stima costo per le chiamate Anthropic. "
    "I listini USD/MTok sono in `lib/claude.PRICING_USD_PER_MTOK`; aggiornarli "
    "se Anthropic li cambia."
)


def _cost_usd(row: dict, model: str) -> float:
    pricing = claude.PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    in_tok = row.get("in_tok") or row.get("input_tokens") or 0
    out_tok = row.get("out_tok") or row.get("output_tokens") or 0
    cr_tok = row.get("cache_read_tok") or row.get("cache_read_tokens") or 0
    cc_tok = row.get("cache_creation_tok") or row.get("cache_creation_tokens") or 0
    return (
        in_tok * pricing["input"] / 1_000_000
        + out_tok * pricing["output"] / 1_000_000
        + cr_tok * pricing["cache_read"] / 1_000_000
        + cc_tok * pricing["cache_creation"] / 1_000_000
    )


col_period, col_rate = st.columns([1, 1])
with col_period:
    days = st.selectbox("Periodo", [7, 30, 90, 365], index=1, format_func=lambda d: f"Ultimi {d} giorni")
with col_rate:
    eur_per_usd = st.number_input(
        "Tasso EUR/USD",
        min_value=0.5, max_value=1.5, value=0.92, step=0.01,
        help="Solo per la conversione visualizzata. Tasso indicativo, aggiornare a piacere.",
    )

summary = db.llm_calls_summary(days=days)
rows = summary["rows"]

if not rows:
    st.info(f"Nessuna chiamata LLM registrata negli ultimi {days} giorni.")
    st.stop()

# Tabella aggregata per task/modello
tbl = []
total_usd = 0.0
total_in = total_out = total_cr = total_cc = 0
for r in rows:
    cost = _cost_usd(r, r["model"])
    total_usd += cost
    total_in += r.get("in_tok") or 0
    total_out += r.get("out_tok") or 0
    total_cr += r.get("cache_read_tok") or 0
    total_cc += r.get("cache_creation_tok") or 0
    tbl.append({
        "Task": r["task"],
        "Modello": r["model"],
        "Chiamate": r["n"],
        "Errori": r["n_errors"],
        "Input tok": r["in_tok"] or 0,
        "Output tok": r["out_tok"] or 0,
        "Cache read tok": r["cache_read_tok"] or 0,
        "Cache create tok": r["cache_creation_tok"] or 0,
        "Costo USD": round(cost, 4),
        "Costo EUR": round(cost * eur_per_usd, 4),
    })

# KPI
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Costo totale (USD)", f"${total_usd:.2f}")
k2.metric("Costo totale (EUR)", f"€{total_usd * eur_per_usd:.2f}")
cache_savings_tok = total_cr  # ogni cache_read è un input "scontato"
k3.metric("Cache read tok", f"{total_cr:,}".replace(",", "."))
k4.metric("Input tok", f"{total_in:,}".replace(",", "."))
k5.metric("Output tok", f"{total_out:,}".replace(",", "."))

# Cache hit ratio: quanto del costo input è stato cachato
total_input_equivalent = total_in + total_cr + total_cc
cache_hit_pct = (total_cr / total_input_equivalent * 100) if total_input_equivalent > 0 else 0
st.caption(
    f"Cache hit: **{cache_hit_pct:.1f}%** dei token input letti da cache. "
    "Più alto = meglio (le call ripetute entro ~5 min beneficiano del prompt cache)."
)

st.divider()

st.subheader("Per task e modello")
df = pd.DataFrame(tbl)
st.dataframe(df, hide_index=True, use_container_width=True)

st.divider()

st.subheader("Ultime chiamate")
calls = db.list_llm_calls(limit=100)
if calls:
    rows_recent = []
    for c in calls:
        cost = _cost_usd(c, c["model"])
        rows_recent.append({
            "ts": c["ts"],
            "task": c["task"],
            "modello": c["model"],
            "in": c["input_tokens"] or 0,
            "out": c["output_tokens"] or 0,
            "cache_read": c["cache_read_tokens"] or 0,
            "ms": c["duration_ms"] or 0,
            "USD": round(cost, 4),
            "errore": (c["error"] or "")[:60],
        })
    st.dataframe(pd.DataFrame(rows_recent), hide_index=True, use_container_width=True)
else:
    st.write("Nessuna chiamata recente.")
