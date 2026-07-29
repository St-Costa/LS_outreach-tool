"""Smoke test for importer on venue 1.md / venue 2.md."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, importer  # noqa: E402

BASE = Path(__file__).resolve().parent.parent


def main():
    test_db_path = Path("/tmp/outreach_test.db")
    if test_db_path.exists():
        test_db_path.unlink()
    db.DB_PATH = test_db_path

    db.init_db()

    files = importer.find_default_files(BASE)
    assert files, "Nessun file venue trovato"
    print(f"File trovati: {[f.name for f in files]}")

    result = importer.import_files(files)
    inserted = len(result["inserted"])
    print(f"Inserite: {inserted}, Saltate: {len(result['skipped'])}, Errori: {len(result['errors'])}")
    assert inserted >= 50, f"Atteso >=50 venue, importate {inserted}"

    venues = db.list_venues()
    with_email = sum(1 for v in venues if v.get("email"))
    with_subject = sum(1 for v in venues if v.get("notes") and "[Bozza pre-importata]" in (v["notes"] or ""))
    with_city = sum(1 for v in venues if v.get("city"))
    with_type = sum(1 for v in venues if v.get("type"))

    print(f"Con email:   {with_email}/{inserted}")
    print(f"Con bozza:   {with_subject}/{inserted}")
    print(f"Con città:   {with_city}/{inserted}")
    print(f"Con tipo:    {with_type}/{inserted}")

    assert with_email / inserted >= 0.95, "Almeno 95% delle venue dovrebbe avere email"
    assert with_subject / inserted >= 0.95, "Almeno 95% delle venue dovrebbe avere bozza pre-importata"
    assert with_type / inserted >= 0.85, "Almeno 85% delle venue dovrebbe avere tipo inferito"

    # Idempotenza
    result2 = importer.import_files(files)
    assert len(result2["inserted"]) == 0, "Re-import dovrebbe essere no-op"
    assert len(result2["skipped"]) == inserted, "Tutte saltate al secondo import"
    print("Idempotenza OK")

    # Specifiche venue note
    rotary = db.get_venue_by_name("Rotary Club Bolzano-Bozen")
    assert rotary, "Rotary Club Bolzano-Bozen non trovato"
    assert rotary.get("type") == "service_club", f"Tipo sbagliato: {rotary.get('type')}"
    assert rotary.get("email") == "bolzano-bozen@rotary2060.org", f"Email sbagliata: {rotary.get('email')}"
    print("Rotary Bolzano OK")

    sfscon = db.get_venue_by_name("SFScon — Free Software Conference (NOI Techpark)")
    assert sfscon, "SFScon non trovato"
    print("SFScon OK")

    test_db_path.unlink()
    print("\n✓ Tutti i test passati")


if __name__ == "__main__":
    main()
