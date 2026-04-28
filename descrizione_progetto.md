# Outreach Intelligence Tool — descrizione completa

> Documento sorgente pensato per essere dato in pasto a un LLM che produrrà la copy del sito.
> Lingua: italiano. Tono: tecnico ma divulgativo. Contiene il "cosa", il "come" e il "perché" del progetto.

---

## 1. In una frase

Un **CRM intelligente desktop, single-user, offline-first**, costruito attorno a un LLM (Claude Sonnet 4.6) per gestire l'outreach B2B di due speaker/formatori (Luca Nesler e Stefano Costa) verso club di servizio, fiere, hub di innovazione, atenei e network locali in Italia.

## 2. Il problema che risolve

L'outreach a freddo per speaking engagement ha caratteristiche scomode:

- **Volume medio-basso** (centinaia, non decine di migliaia di venue): non giustifica una piattaforma SaaS enterprise, ma è troppo per un foglio Excel.
- **Forte personalizzazione richiesta**: ogni mail deve dimostrare di aver capito *quel* club, *quel* festival, *quell'* ateneo. Le mail "AI-slop" generiche bruciano la relazione al primo invio.
- **Storico cross-venue rilevante**: chi ha già parlato a un Rotary di Trento ha argomenti diversi da spendere se contatta un Rotary di Bolzano. La memoria delle interazioni passate è il capitale principale.
- **Canali frammentati**: email, DM Instagram, DM LinkedIn, telefono, di persona. Nessuna piattaforma li unisce in una conversation history coerente.
- **Tempi lunghi tra invio e risposta**: settimane, a volte mesi. Servono follow-up pianificati, non spam.

I tool esistenti (HubSpot, Pipedrive, Lemlist) sono pensati per cicli di vendita SaaS B2B con volumi e scripting industriali. Per due persone che vendono la propria voce a 3-4 eventi al mese, sono macchinari sproporzionati e sbagliati di tono.

## 3. La soluzione, in alto livello

Un'**applicazione desktop locale** (Python + Streamlit) che:

1. Mantiene un database delle venue contattate, dei contatti, delle interazioni inviate e ricevute.
2. Prima di ogni mail, costruisce un **dossier denso** (storico, ente madre, venue sorelle, casi simili) e lo passa a un LLM che redige un draft tarato su tono, angolo e canale.
3. **Non invia nulla automaticamente**: l'utente copia il draft in Aruba webmail / LinkedIn / Instagram, lo invia, e poi reincolla la versione definitiva e la risposta ricevuta.
4. Il sistema **analizza la risposta** (sentiment, segnali di accettazione/rifiuto/proposta meeting) e aggiorna lo stato della pipeline.
5. Suggerisce **follow-up con timing intelligente** basato sul ritardo dell'ultima mail e sul tono dello scambio.
6. Permette **discovery di nuove venue** tramite LLM con web search integrato (Anthropic web search tool), filtrato sul profilo del progetto e sull'ente regionale di interesse.

L'utente resta nel loop di ogni invio. Il tool è un copilota, non un autopilota.

## 4. Caratteristiche distintive (le scelte non ovvie)

### 4.1 Offline-first, single-machine, single-user
Niente cloud, niente login, niente multi-tenant. L'unico utente è Stefano sul suo desktop Linux. Database SQLite a singolo file (~MB), backup = `cp` di un file. Niente daemon, niente downtime.

### 4.2 L'intelligenza vive nei prompt, non in modelli custom
Il valore non sta in fine-tuning, in pipeline ML proprietarie o in retrieval vector DB. Sta nei **dossier** che vengono assemblati prima di ogni call all'LLM:

- Profilo del progetto (mission, offerta, target, esclusioni, differenziatori).
- Profili degli speaker (bio, skill, esperienze passate citabili, lingue).
- Storico completo della venue corrente (mail inviate, risposte ricevute).
- **Storico di venue simili** (matching su tipo + regione + tag): se ho già scritto a 3 Rotary del Triveneto, l'LLM sa cosa ha funzionato e cosa no.
- **Contesto dell'ente madre**: se la venue è un club locale di Rotary 2060, il dossier include le altre venue di quel distretto e il loro stato pipeline.
- **Linee guida email letterali**: un file `email_guidelines.md` di ~600 righe con blacklist di parole vietate, regole anti-allucinazione, divieti di auto-celebrazione, pattern AI-slop da eliminare. Viene letto **fresh ad ogni call**, modificarlo ha effetto immediato.

### 4.3 Anti-AI-slop come prima cittadina
Il file delle linee guida è il documento più curato del progetto. Esempi di regole codificate:

- **Regola Zero**: vietato inventare, dedurre o "rendere più specifico" qualunque dettaglio fattuale (clienti, settori, numeri, città) non letteralmente presente nei profili.
- **Blacklist letterale** di parole vietate (~40 termini): "esattamente", "tangibile", "valorizzare", "su misura", "all'avanguardia", "innovativo", "in linea con", e così via.
- **Pattern autofellatio** vietati: nessun claim auto-celebrativo del tipo "con ottimi risultati", "siamo orgogliosi di", "il nostro track record".
- **Pattern AI-slop "frequenza/tempo adozione"** vietati: nessuna promessa inventata sull'uso quotidiano/settimanale del deliverable.

Ogni body generato viene controllato dall'LLM stesso contro queste regole prima dell'emissione. L'effetto è che i draft suonano scritti da una persona con cognizione, non da un sales bot.

### 4.4 Pipeline a 5 stati invece di 8
La spec iniziale prevedeva 8 stati granulari (`risposta_ricevuta`, `meeting_fissato`, `presentazione_confermata`, `completata`, ecc.). In pratica la granularità extra non veniva usata: l'esito reale è quasi sempre binario (accettato / rifiutato / ghosting). Il sistema oggi ha 5 stati: `da_contattare`, `contattata`, `accettata`, `rifiutata`, `ghostati`. Il dettaglio meeting/presentazione vive nelle note e nella history delle interazioni.

### 4.5 `organizers` come livello esplicito
Aggiunti dopo la prima versione, perché la realtà ha bussato: molte venue non sono enti isolati ma **club locali di un'organizzazione madre** (un Rotary di un certo distretto, un dipartimento di un ateneo, un evento ricorrente di un network). Il modello `organizers` permette di:
- Riconoscere venue sorelle e usarne lo storico nei dossier.
- Centralizzare le info dell'ente (sito, contatti centrali) senza duplicarle su ogni venue.
- Filtrare venue orfane per pulizie e organizzazione.

### 4.6 Discovery LLM con web search persistente
La pagina Discovery permette all'utente di lanciare query tipo *"trova hub di innovazione in Trentino-Alto Adige interessati a temi AI/storytelling per imprese"*. Il sistema:
- Apre una run in DB (sopravvive a riavvii Streamlit).
- Invoca Claude Sonnet 4.6 con il tool `web_search_20250305` (versione scelta esplicitamente per evitare un bug noto delle versioni più nuove con `pause_turn`).
- Logga ogni query di ricerca step-by-step nel DB → l'utente può rivedere le run passate con replay del log.
- Ritorna candidate strutturate (nome, tipo, città, regione, lingua, contatto, email, sito, fit score, deadline, angolo suggerito).
- L'utente accetta o scarta candidate; le accettate diventano venue con un draft di prima mail già pronto in stato `is_draft=1` (pending conferma).

Una run può durare minuti e fare anche centinaia di search totali (cap a 300, max 10 round di `pause_turn`, fino a 24k token per round).

### 4.7 `is_draft` come pattern di sicurezza contro l'LLM auto-illuso
Ogni interazione outbound ha un flag `is_draft`. Le mail generate dalla discovery o dall'utente partono come `is_draft=1`. Solo la conferma esplicita ("Salva come inviato") le marca `is_draft=0`. Questo flag è critico in tre punti:

- Il **conteggio mail outbound** esclude i draft → la pipeline non avanza finché l'utente non conferma.
- Il **dossier passato all'LLM** esclude i draft → l'LLM non crede che la mail sia già stata inviata.
- Il calcolo del **last outgoing per i follow-up** esclude i draft → niente suggerimenti di follow-up basati su mail mai partite.

Un dettaglio piccolo ma centrale: senza questo flag, il sistema generava follow-up del tipo "ricolleghiamoci dopo la mia mail della scorsa settimana" su mail che esistevano solo come draft locale.

### 4.8 Encryption Fernet della API key
La API key Anthropic non sta in `.env`, in env var, in `st.secrets`. È **cifrata Fernet** in una tabella `settings` del DB; la master key vive in `~/.config/outreach/master.key` con permessi `0o600`. Motivi:
- Il tool gira in cwd arbitrario, non c'è una shell di processo definita per `.env`.
- La master key in `~/.config/` non finisce mai in cartelle sincronizzate (Dropbox, iCloud) per errore.
- La key non finisce mai in dump del DB se il file viene condiviso.

Se l'utente perde la master key, deve reincollare la API key in UI. Trade-off accettato.

### 4.9 Geocoding ibrido (cache hardcoded + fallback Nominatim)
La mappa Folium delle venue richiede coordinate. Nominatim (OSM) è rate-limited a 1 req/s e timeoutta volentieri. La soluzione: dict hardcoded `CITY_COORDS` con ~120 città italiane principali, fallback Nominatim solo per città non in cache. Riduce traffico esterno, flakiness, e fa partire la prima visualizzazione mappa in <1s anche con 200 venue.

### 4.10 Niente invio email integrato — scelta deliberata
Il tool **drafta** ma non spedisce. Tre motivi:
1. Aruba SMTP è instabile e gli SDK Python richiedono credenziali a parte.
2. Inviare da un canale "automatico" perde tono personale (mittente, firma, header).
3. L'utente è già loggato in Aruba/LinkedIn/Instagram nel browser → il copy/paste è un attrito di 5 secondi a fronte di una flessibilità di canale enorme.

Il sistema in compenso traccia rigorosamente quale mail è stata effettivamente inviata (l'utente reincolla la versione finale, che può differire dal draft) e mantiene l'`llm_draft` originale come audit trail.

## 5. Stack tecnico

| Layer | Tecnologia | Note |
|---|---|---|
| Linguaggio | Python 3.12 | Type hints PEP 484 completi, `from __future__ import annotations` ovunque |
| UI | Streamlit ≥1.36 | Single-page-app multi-pagina, sidebar pilotata da numerazione filename |
| LLM | Claude Sonnet 4.6 (Anthropic API) | Tutte le call con `output_config.format=json_schema`, system prompt con cache_control ephemeral sul blocco speakers |
| Web search | Anthropic web search tool `web_search_20250305` | Loop `pause_turn` fino a 10 round, cap 300 search totali |
| DB | SQLite (singolo file) | 13 tabelle, FK abilitate, migrazioni idempotenti in `init_db()` |
| Mappa | Folium + streamlit-folium | Marker colorati per stato pipeline, filtri per regione/tipo/stato |
| Crypto | `cryptography` (Fernet) | Cifratura API key |
| Geocoding | Nominatim (OSM) + cache hardcoded | ~120 città IT precablate |
| Test | Smoke test eseguibili come script | No pytest, no framework |
| Deploy | Bash script locale | `launch.sh` (background daemon su :8501), `run.sh` (foreground), `stop.sh` |

## 6. Struttura dati (essenziale)

13 tabelle. Le centrali:

- **`venues`**: il cuore. Una riga per ente da contattare. Campi: nome, tipo (`service_club`, `fiera`, `coworking`, `universita`, `hub_innovazione`...), città/provincia/regione, lat/lon, email, sito, social, lingua, funding type, angolo proposto, descrizione, note, deadline, source (`import-md`, `manual`, `llm-discovery`), stato pipeline, acceptance score, FK opzionale a un `organizer_id`.
- **`contacts`**: persone. Many-to-many con `venues` e `organizers`. Include `suggested_tone` (formale/cordiale/informale/tecnico) e `interests_json` (array di topic).
- **`interactions`**: log di ogni mail/messaggio. Campi: timestamp, canale (`email`, `ig_dm`, `li_dm`, `fb_dm`, `phone`, `in_person`), direzione (`inviata`/`ricevuta`), tipo derivato automaticamente (`prima_mail`, `follow_up_1..n`, `risposta`), oggetto, `content` (testo finale), `llm_draft` (draft originale per audit), `pipeline_status_after`, `is_draft`.
- **`organizers`**: enti madre. Stessi campi di una venue ma a un livello sopra.
- **`speakers`**: singleton (Luca, Stefano). Bio, skills, esperienze, lingue.
- **`project_profile`**: singleton id=1. Mission, offering, target ideale, esclusioni, differenziatori. Iniettato in ogni prompt.
- **`tags` + `venue_tags`**: tag liberi per cluster (assegnati da LLM o manuali, sempre lowercase). Usati dall'algoritmo "find similar venues".
- **`discovery_runs`** + **`discovery_candidates`**: persistenza delle sessioni di scoperta LLM, con log replay e link alle venue create via marker `[Da discovery {run_id}]` nelle note.

## 7. Pagine dell'interfaccia

| Pagina | Funzione |
|---|---|
| **Home** | KPI dashboard pipeline (5 metriche per stato), banner follow-up dovuti (≥7 giorni con severità ≥14), deadline ravvicinate nei prossimi 7 giorni |
| **Profilo** | Editor strategia progetto + bio/skills speaker (Luca, Stefano) in tab |
| **Venue** | Kanban per stato pipeline; edit mode con conversation view, arricchimento LLM (deduce tipo/angolo/lingua dal sito), link a Ente, gestione tag |
| **Contatti** | CRUD persone con ricerca fulltext, link a venue/enti |
| **Outreach** (hidden) | Chat per venue: storico, draft LLM, refine in linguaggio naturale, conferma "salva come inviato", paste risposte ricevute con analisi automatica |
| **Enti** | CRUD organizers + venue sorelle + contatti centrali |
| **Discovery** | LLM + web search per nuove venue. Run history persistente con log replay step-by-step |
| **Mappa** | Folium con marker colorati per stato pipeline + filtri |
| **Costi LLM** | Tracking token usage e spesa per modello/feature |
| **Statistiche** | Metriche aggregate sulla pipeline e tassi di risposta |
| **Cerca** | Search globale cross-tabella |
| **Impostazioni** | API key (save/test/delete con cifratura Fernet), import iniziali da markdown, backup DB, export bulk delle mail di una run |

## 8. Flussi end-to-end principali

### 8.1 Da zero a prima mail inviata

1. L'utente lancia una **discovery** sulla pagina dedicata, scope "Trentino-Alto Adige", max 8 risultati.
2. L'LLM con web search trova 8 candidate (es. festival letterari, hub coworking, club service) con metadati strutturati e fit score.
3. L'utente esamina le candidate e ne **accetta 5**. Per ognuna il sistema crea:
   - Una venue in DB con `source='llm-discovery'` e marker `[Da discovery {run_id}]` nelle note.
   - Un contatto se la candidate aveva una persona referente.
   - Un'interazione `prima_mail` in stato `is_draft=1` con il body già generato.
4. L'utente apre la venue, va in "Conversazione", legge il draft.
5. Se necessario, invia un feedback in linguaggio naturale ("più informale", "togli il riferimento a X", "accorciala del 30%") → l'LLM raffina il draft mantenendo i vincoli delle email guidelines.
6. L'utente conferma "Salva come inviato" → il flag `is_draft` passa a 0, lo stato venue diventa `contattata`.
7. L'utente apre Aruba webmail, copia subject + body, invia.

### 8.2 Tracking risposta e follow-up

1. Una settimana dopo, l'utente riceve una risposta. La incolla nella textarea della pagina Outreach della venue corrispondente.
2. L'LLM analizza la risposta: sentiment, segnali di proposta meeting, segnali di rifiuto, info chiave estratte, stato pipeline suggerito.
3. La risposta viene salvata come interaction `direction='ricevuta'`. Lo stato venue si aggiorna automaticamente (es. `accettata` se l'analisi rileva una proposta concreta).
4. Dopo 7+ giorni senza nuove interazioni, la home segnala la venue tra i "follow-up dovuti".
5. L'utente clicca "Suggerisci follow-up". L'LLM valuta il timing (può anche dire "aspetta ancora N giorni, non insistere ora") e, se approva, genera un draft di follow-up con tono adattivo (più sollecito al primo follow-up, più "chiusura cortese" al terzo).
6. Il tipo interaction (`follow_up_1`, `follow_up_2`, ...) viene derivato automaticamente dal conteggio di mail outbound già inviate.

## 9. Decisioni di architettura, in pillole

- **SQLite invece di Postgres**: volumi attesi <1k venue, <10k interazioni. SQLite gestisce questo carico con margine. Backup = `cp`. Niente network, niente daemon.
- **Streamlit invece di Next.js / Electron**: l'utente è uno, il deploy è locale, il time-to-feature è critico. Streamlit dà un'UI decente con zero codice frontend.
- **Niente pydantic**: `@dataclass` standard library + type hints sono sufficienti. Niente dipendenze magiche.
- **Niente test framework**: solo smoke test eseguibili come script (`python tests/test_importer.py`). Test manuali sufficienti per single-user.
- **Niente library di logging**: ogni operazione è triggerata da un click in Streamlit; `st.success`, `st.error`, `st.toast` sono il logging UI. Per debug post-mortem, file `data/streamlit.log` catturato da `nohup` in `launch.sh`.
- **Linee guida email come file editabile a runtime**: cambiare `email_guidelines.md` ha effetto immediato senza restart. È intenzionale: la curatela del prompt è un'attività continua e non deve passare da un deploy.
- **Italiano come lingua di prima classe**: commenti, variabili, UI, prompt, output JSON dell'LLM. Non c'è uno strato di "internazionalizzazione" da gestire.

## 10. Numeri e stato attuale (aprile 2026)

- 9/9 punti della spec MVP implementati.
- 4 punti della Fase 2 ancora da fare: ingestion slide PPTX/PDF, dashboard metriche interne in UI, discovery schedulata, backup automatico schedulato.
- Codebase: ~5k righe Python, 13 tabelle SQLite, 11 pagine Streamlit, 8 moduli `lib/`.
- Modello LLM: Claude Sonnet 4.6, costo medio per draft prima mail ~0.02-0.05 USD (con prompt caching attivo sul blocco speakers).

## 11. Cosa lo rende interessante da raccontare

- **Specificità di dominio estrema**: non è "un altro CRM AI", è un tool tagliato addosso a un caso d'uso preciso (due speaker che fanno outreach a club di servizio italiani). Le scelte tecniche riflettono esattamente quel caso.
- **Anti-AI-slop come ingegneria di prompt**: il file delle linee guida è un manifesto su come fare scrivere in italiano a un LLM senza farlo suonare come un LLM. Tre livelli di blacklist, regola zero anti-allucinazione, pattern di self-celebration vietati.
- **L'utente nel loop ad ogni invio**: la copia/incollata manuale non è una limitazione, è una scelta di design. L'errore costoso (mandare una mail brutta a un cliente) è meglio prevenirlo con un attrito umano che con un classifier automatico.
- **Memoria cross-venue come capitale**: il dossier ricco passato all'LLM ad ogni call è il vero "secret sauce". Più il sistema viene usato, più il contesto si arricchisce, più i draft diventano calibrati.
- **Decisioni in negativo**: il progetto è altrettanto definito da quello che **non** ha. Niente cloud, niente auth, niente multi-tenant, niente invio integrato, niente test framework, niente CI/CD, niente Docker. Tutte scelte motivate dal contesto single-user.

---

## Appendice — input suggerito per la copy del sito

Quando passi questo documento a un LLM per generare la descrizione, considera di chiedere output specializzati:

- **Tagline** (1 riga): es. *"Un CRM intelligente offline per chi vende la propria voce, non un SaaS."*
- **Descrizione breve** (~80 parole) per la card del progetto in lista.
- **Descrizione lunga** (~300 parole) per la pagina dedicata.
- **3-5 bullet di "cosa lo rende interessante"** per highlight tecnici.
- **Stack tecnico in formato badge** (Python, Streamlit, SQLite, Claude Sonnet, Folium).

Tono consigliato per la copy: **pragmatico, specifico, anti-marketing**. Evitare gli stessi pattern che il tool stesso vieta nei suoi draft (niente "innovativo", niente "su misura", niente "all'avanguardia"). La voce del progetto e la voce del sito che lo presenta dovrebbero coincidere.
