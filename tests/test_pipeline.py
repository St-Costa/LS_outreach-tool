"""Smoke test per lib/pipeline: funzioni pure (normalize_state, draft helpers,
derive_interaction_type, derive_effective_state). Niente DB, niente Streamlit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import pipeline  # noqa: E402


def test_normalize_state():
    # stati validi → identità
    for s in pipeline.PIPELINE_STATES:
        assert pipeline.normalize_state(s) == s, f"identità rotta su {s}"
    # vuoto/None → default
    assert pipeline.normalize_state(None) == "da_contattare"
    assert pipeline.normalize_state("") == "da_contattare"
    # stati legacy mappati
    assert pipeline.normalize_state("risposta_ricevuta") == "contattata"
    assert pipeline.normalize_state("meeting_fissato") == "accettata"
    assert pipeline.normalize_state("presentazione_confermata") == "accettata"
    assert pipeline.normalize_state("completata") == "accettata"
    assert pipeline.normalize_state("nessuna_risposta") == "ghostati"
    # stato sconosciuto → default
    assert pipeline.normalize_state("blah_random") == "da_contattare"
    print("normalize_state OK")


def test_draft_helpers():
    sent = {"direction": "inviata", "is_draft": 0}
    draft = {"direction": "inviata", "is_draft": 1}
    received = {"direction": "ricevuta", "is_draft": 0}
    no_draft_field = {"direction": "inviata"}  # legacy: is_draft mancante

    assert pipeline.is_pending_draft(draft) is True
    assert pipeline.is_pending_draft(sent) is False
    assert pipeline.is_pending_draft(received) is False
    assert pipeline.is_pending_draft(no_draft_field) is False  # NULL/missing = non draft

    assert pipeline.is_confirmed_outgoing(sent) is True
    assert pipeline.is_confirmed_outgoing(draft) is False
    assert pipeline.is_confirmed_outgoing(received) is False
    assert pipeline.is_confirmed_outgoing(no_draft_field) is True  # NULL/missing = confermata

    # invariante: is_pending_draft e is_confirmed_outgoing sono mutuamente esclusivi
    # per le interazioni 'inviata'; entrambe False per 'ricevuta'
    for it in (sent, draft, received, no_draft_field):
        assert not (pipeline.is_pending_draft(it) and pipeline.is_confirmed_outgoing(it))
    print("is_pending_draft / is_confirmed_outgoing OK")


def test_derive_interaction_type():
    assert pipeline.derive_interaction_type("inviata", 0) == "prima_mail"
    assert pipeline.derive_interaction_type("inviata", 1) == "follow_up_1"
    assert pipeline.derive_interaction_type("inviata", 2) == "follow_up_2"
    assert pipeline.derive_interaction_type("inviata", 3) == "follow_up_3"
    assert pipeline.derive_interaction_type("inviata", 4) == "follow_up_n"
    assert pipeline.derive_interaction_type("inviata", 17) == "follow_up_n"
    assert pipeline.derive_interaction_type("ricevuta", 0) == "risposta"
    assert pipeline.derive_interaction_type("ricevuta", 5) == "risposta"
    print("derive_interaction_type OK")


def test_derive_effective_state():
    # senza interazioni → vale lo stato manuale (normalizzato)
    assert pipeline.derive_effective_state(None, None) == "da_contattare"
    assert pipeline.derive_effective_state("accettata", None) == "accettata"
    assert pipeline.derive_effective_state("risposta_ricevuta", None) == "contattata"  # legacy

    # ultima outgoing → 'contattata' tranne se manual è ghostati / interessati_futuro
    outgoing = {"direction": "inviata", "is_draft": 0}
    assert pipeline.derive_effective_state("da_contattare", outgoing) == "contattata"
    assert pipeline.derive_effective_state("accettata", outgoing) == "contattata"
    assert pipeline.derive_effective_state("rifiutata", outgoing) == "contattata"
    assert pipeline.derive_effective_state("ghostati", outgoing) == "ghostati"
    assert pipeline.derive_effective_state("interessati_futuro", outgoing) == "interessati_futuro"

    # ultima incoming → vale lo stato manuale
    incoming = {"direction": "ricevuta", "is_draft": 0}
    assert pipeline.derive_effective_state("accettata", incoming) == "accettata"
    assert pipeline.derive_effective_state("rifiutata", incoming) == "rifiutata"
    assert pipeline.derive_effective_state(None, incoming) == "da_contattare"
    print("derive_effective_state OK")


def test_config_consistency():
    # gli stati attualmente attesi (vedi CLAUDE.md gotcha #2)
    expected = {"da_contattare", "contattata", "accettata", "interessati_futuro", "rifiutata", "ghostati"}
    assert set(pipeline.PIPELINE_STATES) == expected, \
        f"PIPELINE_STATES drift: {pipeline.PIPELINE_STATES}"
    # label/emoji/color derivati hanno la stessa chiavi
    assert set(pipeline.PIPELINE_LABELS) == expected
    assert set(pipeline.PIPELINE_EMOJI) == expected
    assert set(pipeline.PIPELINE_COLORS) == expected
    # tutti i target dei LEGACY mapping puntano a stati validi
    for legacy, target in pipeline.LEGACY_STATE_MAP.items():
        assert target in expected, f"legacy {legacy} punta a {target} (non in PIPELINE_STATES)"
    print("config consistency OK")


def main():
    test_normalize_state()
    test_draft_helpers()
    test_derive_interaction_type()
    test_derive_effective_state()
    test_config_consistency()
    print("\n✓ Tutti i test passati")


if __name__ == "__main__":
    main()
