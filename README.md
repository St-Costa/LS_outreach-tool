<p align="center">
  <img src="assets/logo.svg" alt="L&S Outreach Tool" width="360">
</p>

# L&S Outreach Tool

Tool desktop **single-user** per outreach B2B di Luca Nesler & Stefano (speaker/formatori).
Stack: Streamlit + SQLite + Claude Sonnet 4.6 + Folium. UI e codice in italiano.

Il tool **non invia email**: drafta i messaggi, l'utente li copia e invia da Aruba,
poi reincolla le risposte ricevute per chiudere il loop.

## Quick start

```bash
./launch.sh      # daemon in background su :8501, apre il browser
./run.sh         # foreground (per dev / log live)
./stop.sh        # ferma il daemon
```

Setup deps:

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -U -r requirements.txt
```

API key Claude: configurabile da `Impostazioni` nell'app (cifrata Fernet,
master key in `~/.config/outreach/master.key`). **Niente `.env`, niente env var.**

## Test

```bash
python tests/test_importer.py
python tests/test_organizers.py
python tests/test_pipeline.py
python tests/test_settings.py
```

Non c'è pytest: sono script eseguibili che usano DB temporanei.

## Documentazione

- [CLAUDE.md](CLAUDE.md) — quick reference per chi (umani o Claude Code) lavora sul repo: stack, gotchas critici, convenzioni.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — moduli, flussi end-to-end, decisioni di design.
- [docs/SCHEMA.md](docs/SCHEMA.md) — schema DB completo, FK, migrazioni, stati pipeline.
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — backup, restore, troubleshooting, recovery master key.
- [docs/legacy/SPEC_2026-04.md](docs/legacy/SPEC_2026-04.md) — spec funzionale originale (archiviata).
