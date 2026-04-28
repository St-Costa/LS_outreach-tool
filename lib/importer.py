"""Parser dei markdown sorgente (`vanue 1.md`, `vanue 2.md`) in record venue.

Eseguito una volta all'avvio iniziale (pagina Impostazioni → "Importa venue iniziali")
o dai test. Idempotente: salta venue già presenti per nome. Inferisce automaticamente
type, lingua, città/regione, deadline dal contenuto delle sezioni markdown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import db


VENUE_HEADING = re.compile(r"^## (\d+)\.\s+(.+?)\s*$", re.MULTILINE)
SEND_TO = re.compile(r"\*\*Inviare a:\*\*\s*(.+?)$", re.MULTILINE)
SUBJECT = re.compile(r"\*\*Oggetto:\*\*\s*(.+?)$", re.MULTILINE)
ITALIC_CONTEXT = re.compile(r"^\*([^\*][^\n]*?)\*\s*$", re.MULTILINE)


CITY_MAP = {
    "bolzano": ("Bolzano", "BZ", "Trentino-Alto Adige"),
    "bozen": ("Bolzano", "BZ", "Trentino-Alto Adige"),
    "merano": ("Merano", "BZ", "Trentino-Alto Adige"),
    "meran": ("Merano", "BZ", "Trentino-Alto Adige"),
    "bressanone": ("Bressanone", "BZ", "Trentino-Alto Adige"),
    "brixen": ("Bressanone", "BZ", "Trentino-Alto Adige"),
    "trento": ("Trento", "TN", "Trentino-Alto Adige"),
    "rovereto": ("Rovereto", "TN", "Trentino-Alto Adige"),
    "riva del garda": ("Riva del Garda", "TN", "Trentino-Alto Adige"),
    "verona": ("Verona", "VR", "Veneto"),
    "spilamberto": ("Spilamberto", "MO", "Emilia-Romagna"),
}


TYPE_KEYWORDS = [
    (("rotary",), "service_club"),
    (("lions",), "service_club"),
    (("bni",), "service_club"),
    (("tedx",), "tedx"),
    (("confindustria", "confesercenti", "cna ", "cna "), "associazione"),
    (("hgv", "unione commercio", "consorzio", "bauernbund", "raiffeisen", "rete al femminile", "mountex"), "associazione"),
    (("camera di commercio", "camcom", "wifi", "accademia d'impresa", "wirtschaftskammer"), "ente_camerale"),
    (("università", "unibz", "soi", "clab"), "universita"),
    (("fiera", "klimahouse", "interpoma", "hotel fair", "beam", "hospitality —"), "fiera"),
    (("noi techpark", "mind ", "hub innovazione", "hit ", "drinbz", "impact hub", "startbase", "manifattura", "trentino sviluppo", "trentino startup"), "hub_innovazione"),
    (("sparkasse", "volksbank", "raiffeisenverband"), "banca"),
    (("idm", "tsm",), "ente_pubblico"),
    (("brandnamic", "fruitecom", "webmotion"), "agenzia"),
    (("slush",), "evento_startup"),
    (("digital connect", "ai breakfast", "sfscon", "wirtschaftsforum", "festival dell'economia"), "evento"),
]


ANGLE_KEYWORDS = [
    (("storytelling puro",), "storytelling_puro"),
    (("ai puro",), "ai_puro"),
    (("ai +", "ai e storytelling", "ai e automazione", "automazione marketing"), "ai_storytelling"),
    (("storytelling + ai", "storytelling e ai"), "ai_storytelling"),
    (("collaborazione", "partnership"), "collaborazione"),
    (("bilanciato", "misto"), "misto"),
]


@dataclass
class ParsedVenue:
    number: int
    name: str
    email: Optional[str]
    context: Optional[str]
    subject: Optional[str]
    body: Optional[str]
    raw: str = ""


def _split_sections(text: str) -> list[ParsedVenue]:
    matches = list(VENUE_HEADING.finditer(text))
    sections: list[ParsedVenue] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        sections.append(
            ParsedVenue(
                number=int(m.group(1)),
                name=m.group(2).strip(),
                email=None,
                context=None,
                subject=None,
                body=None,
                raw=chunk,
            )
        )
    return sections


def _extract_email(send_to_value: str) -> Optional[str]:
    if not send_to_value:
        return None
    s = send_to_value.strip()
    if s.lower().startswith("nessuna mail"):
        return None
    # Email pragmatica: local part liberale, dominio con label senza punti consecutivi,
    # TLD finale di almeno 2 lettere alfabetiche (rifiuta "test@test", "test@a.b", "x@y.123").
    m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}", s)
    return m.group(0) if m else None


def _detect_language(body: str, context: str, name: str) -> str:
    text = (body or "") + "\n" + (context or "") + "\n" + (name or "")
    low = text.lower()
    if re.search(r"\b(hello|dear|hi there|best,?$|best regards)\b", body or "", re.IGNORECASE):
        return "EN"
    if re.search(r"\b(sehr geehrte|guten tag|mit freundlichen)\b", body or "", re.IGNORECASE):
        return "DE"
    if "tedesco" in low or "germanofona" in low or "bilingue" in low:
        return "IT/DE"
    if "inglese" in low and "lingua ufficiale" in low:
        return "EN"
    return "IT"


def _detect_type(name: str, context: str) -> Optional[str]:
    s = (name + " " + (context or "")).lower()
    for keywords, value in TYPE_KEYWORDS:
        for kw in keywords:
            if kw in s:
                return value
    return None


def _detect_angle(context: str) -> Optional[str]:
    if not context:
        return None
    low = context.lower()
    m = re.search(r"angolo[:\s]+([^.]+)", low)
    src = m.group(1) if m else low
    for keywords, value in ANGLE_KEYWORDS:
        for kw in keywords:
            if kw in src:
                return value
    return None


def _detect_city_region(name: str, context: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    s = (name + " " + (context or "")).lower()
    for key, (city, prov, region) in CITY_MAP.items():
        if key in s:
            return city, prov, region
    return None, None, None


def _detect_funding_type(context: str) -> Optional[str]:
    if not context:
        return None
    low = context.lower()
    if any(k in low for k in ("camera di commercio", "ente in-house", "agenzia provinciale", "fondazione hit", "trentino sviluppo")):
        return "pubblico"
    if any(k in low for k in ("rotary", "lions", "associazione", "bauernbund", "consorzio", "rete")):
        return "associazione"
    if any(k in low for k in ("cooperativ", "raiffeisen")):
        return "cooperativa"
    if any(k in low for k in ("agenzia", "agency", "azienda", "spa", "srl", "fiera bolzano")):
        return "privato"
    return None


def _detect_deadline(context: str) -> tuple[Optional[str], Optional[str]]:
    if not context:
        return None, None
    patterns = [
        r"(?:fino al|chiude|aperta fino al|deadline)\s+(\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+\d{4})",
        r"(\d{1,2}-\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+\d{4})",
        r"(\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+\d{4})",
    ]
    for p in patterns:
        m = re.search(p, context, re.IGNORECASE)
        if m:
            return m.group(1), None
    return None, None


def _extract_body(raw: str) -> Optional[str]:
    """Body: text between the first standalone '---' that follows '**Oggetto:**' and the next '---'."""
    # Find Oggetto position
    m = SUBJECT.search(raw)
    if not m:
        return None
    after = raw[m.end():]
    parts = re.split(r"^---\s*$", after, flags=re.MULTILINE)
    # parts[0] = blank between Oggetto and the first ---
    # parts[1] = body
    if len(parts) < 2:
        return None
    body = parts[1].strip()
    return body or None


def parse_markdown(text: str) -> list[ParsedVenue]:
    sections = _split_sections(text)
    for sec in sections:
        st = SEND_TO.search(sec.raw)
        if st:
            sec.email = _extract_email(st.group(1))
        ctx = ITALIC_CONTEXT.search(sec.raw)
        if ctx:
            sec.context = ctx.group(1).strip()
        sub = SUBJECT.search(sec.raw)
        if sub:
            sec.subject = sub.group(1).strip()
        sec.body = _extract_body(sec.raw)
    return sections


def to_venue_record(parsed: ParsedVenue, source: str = "import-md") -> dict:
    name = parsed.name
    context = parsed.context or ""
    body = parsed.body or ""
    city, province, region = _detect_city_region(name, context)
    deadline_text, deadline_date = _detect_deadline(context)
    notes_lines: list[str] = []
    if parsed.context:
        notes_lines.append(f"[Contesto] {parsed.context}")
    if parsed.subject and parsed.body:
        notes_lines.append(
            f"[Bozza pre-importata]\nOggetto: {parsed.subject}\n\n{parsed.body}"
        )
    elif parsed.subject:
        notes_lines.append(f"[Oggetto pre-importato] {parsed.subject}")
    return {
        "name": name,
        "type": _detect_type(name, context),
        "address": None,
        "city": city,
        "province": province,
        "region": region,
        "email": parsed.email,
        "language": _detect_language(body, context, name),
        "funding_type": _detect_funding_type(context),
        "angle": _detect_angle(context),
        "notes": "\n\n".join(notes_lines) if notes_lines else None,
        "deadline_text": deadline_text,
        "deadline_date": deadline_date,
        "source": source,
        "pipeline_status": "da_contattare",
    }


def import_files(paths: list[Path]) -> dict:
    """Import venue files. Idempotent: skips venues whose name is already in DB."""
    inserted: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for path in paths:
        if not path.exists():
            errors.append(f"File non trovato: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        parsed_list = parse_markdown(text)
        for parsed in parsed_list:
            if not parsed.name:
                continue
            existing = db.get_venue_by_name(parsed.name)
            if existing:
                skipped.append(parsed.name)
                continue
            record = to_venue_record(parsed)
            try:
                db.insert_venue(record)
                inserted.append(parsed.name)
            except Exception as e:
                errors.append(f"{parsed.name}: {e}")
    return {"inserted": inserted, "skipped": skipped, "errors": errors}


def find_default_files(base_dir: Path) -> list[Path]:
    """Cerca i markdown sorgente in `base_dir/data/source/` (preferito) e poi `base_dir/` (fallback legacy). Dedup per nome."""
    names = ["vanue 1.md", "vanue 2.md", "venue 1.md", "venue 2.md"]
    search_dirs = [base_dir / "data" / "source", base_dir]
    found: list[Path] = []
    seen: set[str] = set()
    for d in search_dirs:
        for n in names:
            p = d / n
            if p.exists() and n not in seen:
                found.append(p)
                seen.add(n)
    return found
