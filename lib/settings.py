"""Storage cifrato Fernet della API key Anthropic.

La master key vive in `~/.config/outreach/master.key` (perms 0o600), generata
al primo accesso. Il token cifrato è memorizzato in `settings.anthropic_api_key_enc`.
Non c'è `.env` né variabile d'ambiente: la key si imposta solo via UI
(pagina Impostazioni). Recovery: vedi `docs/OPERATIONS.md`.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .db import get_setting, set_setting, delete_setting

MASTER_KEY_PATH = Path.home() / ".config" / "outreach" / "master.key"
SETTING_KEY = "anthropic_api_key_enc"


def _ensure_master_key() -> bytes:
    if MASTER_KEY_PATH.exists():
        return MASTER_KEY_PATH.read_bytes().strip()
    MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Fernet.generate_key()
    MASTER_KEY_PATH.write_bytes(key)
    os.chmod(MASTER_KEY_PATH, 0o600)
    return key


def _fernet() -> Fernet:
    return Fernet(_ensure_master_key())


def save_api_key(api_key: str) -> None:
    """Cifra e salva la API key. Stringa vuota = cancella il record dal DB."""
    api_key = (api_key or "").strip()
    if not api_key:
        delete_setting(SETTING_KEY)
        return
    token = _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")
    set_setting(SETTING_KEY, token)


def get_api_key() -> str | None:
    """Decifra la API key dal DB. Ritorna None se assente o se il token non è decifrabile (master key cambiata/persa)."""
    enc = get_setting(SETTING_KEY)
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None


def api_key_status() -> str:
    """Ritorna 'missing' (mai impostata), 'ok' (presente e decifrabile), 'corrupt' (presente ma non decifrabile: master key cambiata/persa)."""
    enc = get_setting(SETTING_KEY)
    if not enc:
        return "missing"
    try:
        _fernet().decrypt(enc.encode("ascii"))
        return "ok"
    except InvalidToken:
        return "corrupt"


def has_api_key() -> bool:
    """True se la API key è presente e decifrabile."""
    return get_api_key() is not None
