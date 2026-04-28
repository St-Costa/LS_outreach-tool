"""Smoke test del CRUD Enti (organizzatori) e relazioni con venue/contatti."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db  # noqa: E402


def main():
    test_db_path = Path("/tmp/outreach_test_orgs.db")
    if test_db_path.exists():
        test_db_path.unlink()
    db.DB_PATH = test_db_path

    db.init_db()

    # 1. Roundtrip insert/get
    oid = db.insert_organizer({
        "name": "Distretto Rotary 2060",
        "type": "network",
        "region": "Veneto",
        "language": "IT",
        "description": "Distretto rotariano del Triveneto.",
    })
    org = db.get_organizer(oid)
    assert org and org["name"] == "Distretto Rotary 2060", "Roundtrip insert/get fallito"
    assert org["type"] == "network"
    print("1. insert_organizer + get_organizer OK")

    # 2. get_organizer_by_name
    org_by_name = db.get_organizer_by_name("Distretto Rotary 2060")
    assert org_by_name and org_by_name["id"] == oid
    print("2. get_organizer_by_name OK")

    # 3. update_organizer
    db.update_organizer(oid, {"hq_city": "Padova", "notes": "appunto"})
    org2 = db.get_organizer(oid)
    assert org2["hq_city"] == "Padova" and org2["notes"] == "appunto"
    print("3. update_organizer OK")

    # 4. list_organizers + filtri
    db.insert_organizer({"name": "Confindustria Vicenza", "type": "associazione", "region": "Veneto"})
    db.insert_organizer({"name": "Politecnico Milano", "type": "universita", "region": "Lombardia"})
    all_orgs = db.list_organizers()
    assert len(all_orgs) == 3
    veneto = db.list_organizers({"region": "Veneto"})
    assert len(veneto) == 2
    networks = db.list_organizers({"type": "network"})
    assert len(networks) == 1
    search = db.list_organizers({"search": "rotary"})
    assert len(search) == 1
    print("4. list_organizers + filtri OK")

    # 5. set_venue_organizer + get_organizer_for_venue + get_venues_for_organizer
    vid_a = db.insert_venue({"name": "Rotary Club Padova", "type": "service_club", "region": "Veneto"})
    vid_b = db.insert_venue({"name": "Rotary Club Vicenza", "type": "service_club", "region": "Veneto"})
    vid_c = db.insert_venue({"name": "Venue Orfana", "type": "altro"})
    db.set_venue_organizer(vid_a, oid)
    db.set_venue_organizer(vid_b, oid)
    org_for_a = db.get_organizer_for_venue(vid_a)
    assert org_for_a and org_for_a["id"] == oid
    siblings = db.get_venues_for_organizer(oid)
    assert {v["id"] for v in siblings} == {vid_a, vid_b}
    print("5. set/get venue↔organizer OK")

    # 6. count_venues_by_organizer
    counts = db.count_venues_by_organizer()
    assert counts.get(oid) == 2
    print("6. count_venues_by_organizer OK")

    # 7. list_orphan_venues
    orphans = db.list_orphan_venues()
    orphan_ids = {v["id"] for v in orphans}
    assert vid_c in orphan_ids
    assert vid_a not in orphan_ids and vid_b not in orphan_ids
    print("7. list_orphan_venues OK")

    # 8. Junction contatti↔ente (link/unlink, simmetria)
    cid = db.insert_contact({"first_name": "Maria", "last_name": "Rossi", "role": "presidente"})
    db.link_organizer_contact(oid, cid)
    by_org = db.get_contacts_for_organizer(oid)
    by_contact = db.get_organizers_for_contact(cid)
    assert len(by_org) == 1 and by_org[0]["id"] == cid
    assert len(by_contact) == 1 and by_contact[0]["id"] == oid
    db.unlink_organizer_contact(oid, cid)
    assert db.get_contacts_for_organizer(oid) == []
    print("8. link/unlink organizer_contact + simmetria OK")

    # 9. delete_organizer: non elimina venue ma le NULLifica, non elimina contatti
    db.link_organizer_contact(oid, cid)
    db.delete_organizer(oid)
    assert db.get_organizer(oid) is None
    # Le venue restano ma orfane
    assert db.get_venue(vid_a) is not None
    assert db.get_organizer_for_venue(vid_a) is None
    # Il contatto resta
    assert db.get_contact(cid) is not None
    # Junction ripulita
    assert db.get_organizers_for_contact(cid) == []
    print("9. delete_organizer: NULL su venue, contatti preservati OK")

    print("\nTutti i test passati ✅")


if __name__ == "__main__":
    main()
