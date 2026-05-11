"""Smoke test per lib/settings: round-trip Fernet, perms master.key, recovery.

Tutto isolato in tempdir: NON tocca mai ~/.config/outreach/ né il DB reale.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TEMPDIRS: list[Path] = []


def _isolated_env():
    """Ridireziona master.key e DB_PATH in tempdir per evitare side-effect."""
    tmp = Path(tempfile.mkdtemp(prefix="outreach_settings_test_"))
    _TEMPDIRS.append(tmp)
    from lib import db
    from lib import settings

    db.DB_PATH = tmp / "test.db"
    settings.MASTER_KEY_PATH = tmp / "master.key"
    db.init_db()
    return tmp, db, settings


def test_master_key_creation_and_perms():
    tmp, _db, settings = _isolated_env()
    assert not settings.MASTER_KEY_PATH.exists()
    key = settings._ensure_master_key()
    assert settings.MASTER_KEY_PATH.exists(), "master.key non creata"
    assert len(key) > 0
    # Perms 0o600 (solo owner read/write)
    mode = stat.S_IMODE(os.stat(settings.MASTER_KEY_PATH).st_mode)
    assert mode == 0o600, f"perms attesi 0o600, trovati {oct(mode)}"
    # Idempotenza: seconda chiamata legge la chiave esistente, non la rigenera
    key2 = settings._ensure_master_key()
    assert key == key2, "master.key rigenerata invece di letta"
    print(f"master.key creation + perms 0o600 OK ({tmp})")


def test_roundtrip_encrypt_decrypt():
    _tmp, _db, settings = _isolated_env()
    secret = "sk-ant-test-abc-1234567890"
    settings.save_api_key(secret)
    assert settings.get_api_key() == secret, "round-trip rotto"
    assert settings.has_api_key() is True
    assert settings.api_key_status() == "ok"
    print("Fernet round-trip OK")


def test_save_empty_deletes():
    _tmp, _db, settings = _isolated_env()
    settings.save_api_key("sk-ant-something")
    assert settings.has_api_key()
    settings.save_api_key("")  # stringa vuota = cancella
    assert settings.get_api_key() is None
    assert settings.has_api_key() is False
    assert settings.api_key_status() == "missing"
    # Anche solo whitespace conta come "vuota"
    settings.save_api_key("sk-ant-x")
    settings.save_api_key("   ")
    assert settings.get_api_key() is None
    print("save_api_key('') cancella record OK")


def test_recovery_when_master_key_lost():
    """Se la master key viene rigenerata (es. utente la perde e ne crea una nuova),
    il token salvato diventa indecifrabile: api_key_status='corrupt', get_api_key=None.
    Nessuna eccezione propagata."""
    _tmp, _db, settings = _isolated_env()
    settings.save_api_key("sk-ant-original")
    assert settings.api_key_status() == "ok"

    # Simula perdita: rigenera la master.key
    from cryptography.fernet import Fernet
    settings.MASTER_KEY_PATH.write_bytes(Fernet.generate_key())
    os.chmod(settings.MASTER_KEY_PATH, 0o600)

    assert settings.api_key_status() == "corrupt", "status dovrebbe essere 'corrupt'"
    assert settings.get_api_key() is None, "get_api_key dovrebbe ritornare None, non eccezione"
    assert settings.has_api_key() is False
    print("Recovery con master.key persa OK (no exception, status='corrupt')")


def test_status_missing_when_no_key_set():
    _tmp, _db, settings = _isolated_env()
    assert settings.api_key_status() == "missing"
    assert settings.get_api_key() is None
    assert settings.has_api_key() is False
    print("status='missing' su DB fresco OK")


def main():
    try:
        test_master_key_creation_and_perms()
        test_roundtrip_encrypt_decrypt()
        test_save_empty_deletes()
        test_recovery_when_master_key_lost()
        test_status_missing_when_no_key_set()
        print("\n✓ Tutti i test passati")
    finally:
        for d in _TEMPDIRS:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
