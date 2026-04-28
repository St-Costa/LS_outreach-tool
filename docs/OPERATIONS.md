# Runbook operativo

Procedure pratiche per gestire il tool al di fuori dello sviluppo: setup, backup, restore, troubleshooting.

---

## Setup primo avvio

```bash
cd /mnt/dati/Documents/L\&S\ outreach\ tool
./launch.sh
```

`launch.sh` ([launch.sh](../launch.sh)):
1. Crea `venv/` se mancante e installa `requirements.txt`
2. Controlla se è già attivo su `http://localhost:8501/_stcore/health` → se sì, apre solo il browser
3. Altrimenti avvia Streamlit in background con `nohup`, log redirect in `data/streamlit.log`
4. Attende max 20s il health check, poi `xdg-open` sul browser

Al primo accesso:
1. Apri **Impostazioni** dalla sidebar (`99_⚙_Impostazioni`)
2. Incolla la API key Anthropic e click "Salva"
3. Click "Testa connessione" → deve rispondere `OK — modello claude-sonnet-4-6`
4. Sezione "Importa venue iniziali" → click "Importa ora" (legge `data/source/vanue 1.md` + `vanue 2.md`, idempotente)

---

## Avvio / stop

| Script | Modalità | Quando usarlo |
|---|---|---|
| `./launch.sh` | Background (nohup), apre browser | Uso quotidiano |
| `./run.sh` | Foreground (terminale bloccato) | Sviluppo: vedi log live, Ctrl+C per stop |
| `./stop.sh` | `pkill -f 'streamlit run app.py'` | Ferma il daemon |

Il daemon **non si chiude** chiudendo il browser: usa `./stop.sh`.

---

## Backup database

### Manuale via UI (raccomandato)
**Impostazioni → Database → "Backup ora"** → file in `data/backups/outreach_YYYYMMDD_HHMMSS.db`.

### Manuale via shell
```bash
cp data/outreach.db "data/backups/outreach_$(date +%Y%m%d_%H%M%S).db"
```

### ⚠️ Limitazione nota
**Backup automatico schedulato non implementato** (Fase 2 §2.4 dello spec). Per ora è responsabilità di Stefano fare backup periodici. Per automatizzare con cron locale:

```bash
# crontab -e
0 22 * * * cd "/mnt/dati/Documents/L&S outreach tool" && cp data/outreach.db "data/backups/outreach_$(date +\%Y\%m\%d).db"
```

---

## Restore

```bash
./stop.sh
cp data/backups/outreach_20260420_180000.db data/outreach.db
./launch.sh
```

⚠️ Importante: assicurarsi che **nessuna istanza Streamlit** sia attiva durante la sostituzione (altrimenti SQLite WAL/journal restano fuori sync).

---

## Reset / wipe

### Da UI (con backup automatico)
**Impostazioni → "Zona pericolosa — Reset database"** → digita `RESET` → click. Crea backup pre-reset, cancella DB, ricrea schema + seed.

### Da shell
```bash
./stop.sh
mv data/outreach.db data/outreach_backup_$(date +%F).db
./launch.sh   # init_db() ricrea schema + seed (Luca, Stefano placeholder)
```

---

## Reimport venue iniziali

I file sorgente vivono in `data/source/`:
- `vanue 1.md` — venue Trentino-AA principali
- `vanue 2.md` — venue aggiuntive

Pagina **Impostazioni → "Importa venue iniziali"** → "Importa ora". Idempotente: salta venue già presenti per nome.

Da test/CLI: `python tests/test_importer.py` (usa un DB temporaneo `/tmp/outreach_test.db`).

---

## Master key API

| Cosa | Dettaglio |
|---|---|
| Path | `~/.config/outreach/master.key` |
| Permessi | `0o600` (solo owner read/write), parent dir `0o700` |
| Generata | Al primo accesso (`_ensure_master_key` in [lib/settings.py](../lib/settings.py)) |
| Algoritmo | Fernet (cryptography lib) |
| Storage API key | `settings.anthropic_api_key_enc` (token Fernet base64) |

### Se la master key viene persa
La API key cifrata in DB diventa illeggibile (`InvalidToken` → `get_api_key()` ritorna `None`).

Recovery:
```bash
sqlite3 data/outreach.db "DELETE FROM settings WHERE key='anthropic_api_key_enc';"
```

Poi riapri il tool e reincolla la API key in **Impostazioni**. Una nuova master key viene generata automaticamente.

### Backup della master key
Non è incluso nei backup del DB. Conviene salvarla a parte (es. password manager):
```bash
cat ~/.config/outreach/master.key
```

---

## Log

| Modalità | Dove |
|---|---|
| `./launch.sh` (background) | `data/streamlit.log` (redirect `nohup`) |
| `./run.sh` (foreground) | stdout/stderr terminale |

Tail live durante uso:
```bash
tail -f data/streamlit.log
```

`*.log` è in `.gitignore`.

---

## Troubleshooting

### "Server già attivo" / porta 8501 occupata
```bash
./stop.sh
# oppure forzato:
pkill -9 -f "streamlit run app.py"
# verifica che la porta sia libera:
ss -tln | grep 8501
```

### "API key non valida" o "API key non configurata"
1. **Impostazioni → "Testa connessione"**
2. Se fallisce con `AuthenticationError` → la key è scaduta o errata, reincollala
3. Se fallisce con `InvalidToken` interno (key in DB illeggibile) → vedi sezione Master key sopra

### Discovery non trova nulla
- Controlla scope: troppo ristretto → riduci o passa a "Italia"
- Controlla `max_results`: con 1-2 il modello fa poche query
- Verifica i log: pagina Discovery mostra log step-by-step della run (espandi)
- Se l'errore è di rete/web search: il tool `web_search_20250305` può rate-limitare; riprova dopo qualche minuto

### Database locked
```
sqlite3.OperationalError: database is locked
```
Causa: più istanze Streamlit aperte sullo stesso DB.
```bash
./stop.sh
ps aux | grep streamlit | grep -v grep   # verifica nessun residuo
./launch.sh
```

### Geocoding mancante / venue senza coordinate sulla mappa
Da console Python (con venv attivo):
```python
from lib import geocode
geocode.autocoord_all_venues()
```
Usa cache hardcoded `CITY_COORDS` per ~120 città italiane + Nominatim per il resto. Nominatim ha rate limit 1 req/s.

### Streamlit non si avvia / errori di import
```bash
source venv/bin/activate
python -c "import streamlit, anthropic, folium, pandas, cryptography; print('ok')"
```
Se manca qualcosa:
```bash
pip install -r requirements.txt
```

### `data/source/` non trovata dopo update
Se l'app dice "Nessun file vanue trovato" ma i file sono nella radice del progetto (versione legacy):
- Sposta in `data/source/`: `mkdir -p data/source && mv "vanue 1.md" "vanue 2.md" data/source/`
- `find_default_files()` cerca prima in `data/source/` poi nella radice come fallback

---

## Aggiornamento dipendenze

```bash
source venv/bin/activate
pip install -U -r requirements.txt
python tests/test_importer.py    # smoke test
./stop.sh && ./launch.sh
```

Smoke test atteso:
```
Inserite: 51, Saltate: 0, Errori: 0
Con email:   50/51
Con bozza:   51/51
...
✓ Tutti i test passati
```

---

## Path importanti

| Path | Cosa |
|---|---|
| `data/outreach.db` | SQLite database (escluso da git) |
| `data/backups/*.db` | Backup manuali (esclusi da git) |
| `data/source/vanue *.md` | Sorgenti import iniziale (versionati in git) |
| `data/streamlit.log` | Log run background (escluso da git) |
| `~/.config/outreach/master.key` | Master key Fernet (NON in repo) |
| `.streamlit/config.toml` | Config Streamlit (escluso da git via `.gitignore`) |
| `venv/` | Python venv (escluso da git) |
