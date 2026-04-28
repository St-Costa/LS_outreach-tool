"""Nominatim geocoding + cache hardcoded delle città italiane principali per geocoding istantaneo.

Le coordinate sono caricate da `data/city_coords.json` se presente; in fallback
si usa il dict inline in fondo al modulo. Per aggiungere una città: edit del JSON
(prevale a runtime senza modifica codice).
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Optional

import requests

from . import db


_COORDS_FILE = Path(__file__).resolve().parent.parent / "data" / "city_coords.json"


def _load_coords_from_file() -> tuple[Optional[dict], Optional[dict]]:
    """Ritorna (cities, regions) dal JSON o (None, None) se assente/illeggibile."""
    if not _COORDS_FILE.exists():
        return None, None
    try:
        data = json.loads(_COORDS_FILE.read_text(encoding="utf-8"))
        cities = {k.lower(): tuple(v) for k, v in (data.get("cities") or {}).items()}
        regions = {k.lower(): tuple(v) for k, v in (data.get("regions") or {}).items()}
        return cities, regions
    except (json.JSONDecodeError, ValueError, OSError):
        return None, None


# Fallback inline se il JSON è assente (codice originale, mantenuto per safety).
# Lookup case-insensitive, key normalizzata in lowercase.
_CITY_COORDS_FALLBACK: dict[str, tuple[float, float]] = {
    # Trentino-Alto Adige
    "bolzano": (46.4983, 11.3548), "bozen": (46.4983, 11.3548),
    "trento": (46.0667, 11.1167),
    "merano": (46.6713, 11.1654), "meran": (46.6713, 11.1654),
    "bressanone": (46.7150, 11.6571), "brixen": (46.7150, 11.6571),
    "rovereto": (45.8896, 11.0421),
    "riva del garda": (45.8859, 10.8407),
    "arco": (45.9176, 10.8852),
    "pergine valsugana": (46.0626, 11.2376),
    # Veneto
    "verona": (45.4384, 10.9916), "venezia": (45.4408, 12.3155),
    "padova": (45.4064, 11.8768), "treviso": (45.6669, 12.2431),
    "vicenza": (45.5455, 11.5354), "rovigo": (45.0708, 11.7903),
    "belluno": (46.1414, 12.2169),
    # Lombardia
    "milano": (45.4642, 9.1900), "bergamo": (45.6983, 9.6773),
    "brescia": (45.5398, 10.2200), "monza": (45.5845, 9.2744),
    "varese": (45.8206, 8.8251), "como": (45.8081, 9.0852),
    "lecco": (45.8566, 9.3931), "pavia": (45.1847, 9.1582),
    "mantova": (45.1564, 10.7914), "cremona": (45.1335, 10.0226),
    "sondrio": (46.1700, 9.8700),
    # Friuli-Venezia Giulia
    "trieste": (45.6495, 13.7768), "udine": (46.0633, 13.2350),
    "pordenone": (45.9558, 12.6604), "gorizia": (45.9410, 13.6213),
    # Emilia-Romagna
    "bologna": (44.4949, 11.3426), "modena": (44.6471, 10.9252),
    "parma": (44.8015, 10.3279), "reggio emilia": (44.6989, 10.6298),
    "spilamberto": (44.5345, 11.0353), "ravenna": (44.4173, 12.1965),
    "ferrara": (44.8378, 11.6196), "rimini": (44.0594, 12.5683),
    "piacenza": (45.0526, 9.6929), "forlì": (44.2227, 12.0407),
    "cesena": (44.1378, 12.2417),
    # Piemonte
    "torino": (45.0703, 7.6869), "novara": (45.4458, 8.6219),
    "cuneo": (44.3841, 7.5420), "alessandria": (44.9133, 8.6151),
    "asti": (44.9009, 8.2068), "biella": (45.5663, 8.0531),
    "vercelli": (45.3209, 8.4185), "verbania": (45.9216, 8.5511),
    # Liguria
    "genova": (44.4056, 8.9463), "la spezia": (44.1024, 9.8244),
    "imperia": (43.8884, 8.0269), "savona": (44.3091, 8.4775),
    # Toscana
    "firenze": (43.7696, 11.2558), "pisa": (43.7228, 10.4017),
    "siena": (43.3188, 11.3308), "lucca": (43.8430, 10.5050),
    "livorno": (43.5485, 10.3106), "arezzo": (43.4633, 11.8796),
    "grosseto": (42.7596, 11.1135), "prato": (43.8777, 11.0955),
    # Umbria
    "perugia": (43.1107, 12.3908), "terni": (42.5631, 12.6426),
    # Marche
    "ancona": (43.6158, 13.5189), "pesaro": (43.9092, 12.9132),
    "ascoli piceno": (42.8530, 13.5746), "macerata": (43.3007, 13.4533),
    # Lazio
    "roma": (41.9028, 12.4964), "latina": (41.4677, 12.9037),
    "viterbo": (42.4175, 12.1054), "frosinone": (41.6396, 13.3491),
    # Abruzzo
    "l'aquila": (42.3498, 13.3995), "pescara": (42.4584, 14.2159),
    "chieti": (42.3479, 14.1661), "teramo": (42.6589, 13.7042),
    # Molise
    "campobasso": (41.5630, 14.6562),
    # Campania
    "napoli": (40.8518, 14.2681), "salerno": (40.6824, 14.7681),
    "caserta": (41.0723, 14.3322), "benevento": (41.1297, 14.7828),
    "avellino": (40.9145, 14.7935),
    # Basilicata
    "potenza": (40.6402, 15.8055), "matera": (40.6664, 16.6044),
    # Puglia
    "bari": (41.1171, 16.8719), "lecce": (40.3515, 18.1750),
    "taranto": (40.4644, 17.2470), "foggia": (41.4621, 15.5446),
    "brindisi": (40.6396, 17.9447),
    # Calabria
    "catanzaro": (38.9090, 16.5874), "cosenza": (39.2986, 16.2533),
    "reggio calabria": (38.1147, 15.6494),
    # Sicilia
    "palermo": (38.1157, 13.3613), "catania": (37.5079, 15.0830),
    "messina": (38.1938, 15.5540), "siracusa": (37.0755, 15.2866),
    "trapani": (38.0176, 12.5365), "ragusa": (36.9269, 14.7308),
    # Sardegna
    "cagliari": (39.2238, 9.1217), "sassari": (40.7259, 8.5556),
    "olbia": (40.9233, 9.4985), "nuoro": (40.3215, 9.3296),
}

_REGION_COORDS_FALLBACK: dict[str, tuple[float, float]] = {
    "trentino-alto adige": (46.0700, 11.1200),
    "veneto": (45.4350, 12.3300),
    "lombardia": (45.4642, 9.1900),
    "friuli-venezia giulia": (46.0633, 13.2350),
    "emilia-romagna": (44.4949, 11.3426),
    "piemonte": (45.0703, 7.6869),
    "valle d'aosta": (45.7370, 7.3194),
    "liguria": (44.4056, 8.9463),
    "toscana": (43.7696, 11.2558),
    "umbria": (43.1107, 12.3908),
    "marche": (43.6158, 13.5189),
    "lazio": (41.9028, 12.4964),
    "abruzzo": (42.3498, 13.3995),
    "molise": (41.5630, 14.6562),
    "campania": (40.8518, 14.2681),
    "basilicata": (40.6402, 15.8055),
    "puglia": (41.1171, 16.8719),
    "calabria": (38.9090, 16.5874),
    "sicilia": (38.1157, 13.3613),
    "sardegna": (39.2238, 9.1217),
}


_loaded_cities, _loaded_regions = _load_coords_from_file()
CITY_COORDS: dict[str, tuple[float, float]] = _loaded_cities if _loaded_cities else _CITY_COORDS_FALLBACK
REGION_COORDS: dict[str, tuple[float, float]] = _loaded_regions if _loaded_regions else _REGION_COORDS_FALLBACK


# Pattern cercati nel nome venue come ultima risorsa quando city/region mancano
NAME_PATTERN_HINTS: list[tuple[str, str]] = [
    # (substring_lowercase, city_key_in_CITY_COORDS)
    ("alto adige", "bolzano"),
    ("südtirol", "bolzano"),
    ("suedtirol", "bolzano"),
    ("bolzano", "bolzano"),
    ("bozen", "bolzano"),
    ("noi techpark", "bolzano"),
    ("wirtschaftsforum", "bolzano"),
    ("wifi", "bolzano"),
    ("hgv", "bolzano"),
    ("idm", "bolzano"),
    ("camera di commercio bz", "bolzano"),
    ("camcom.bz", "bolzano"),
    ("camcom.tn", "trento"),
    ("camera di commercio tn", "trento"),
    ("trentino", "trento"),
    ("trento", "trento"),
    ("merano", "merano"),
    ("meran", "merano"),
    ("bressanone", "bressanone"),
    ("brixen", "bressanone"),
    ("rovereto", "rovereto"),
    ("riva del garda", "riva del garda"),
]


def fast_geocode_venue(venue: dict) -> Optional[tuple[float, float]]:
    """Lookup istantaneo: città → regione → pattern nel nome → None. Niente HTTP.
    Aggiunge jitter deterministico per evitare overlap di marker nella stessa città."""
    city = (venue.get("city") or "").strip().lower()
    region = (venue.get("region") or "").strip().lower()
    name = (venue.get("name") or "").lower()

    coords = None
    jitter_scale = 0.0
    if city in CITY_COORDS:
        coords = CITY_COORDS[city]
        jitter_scale = 0.012
    elif region in REGION_COORDS:
        coords = REGION_COORDS[region]
        jitter_scale = 0.04
    else:
        # Ultima risorsa: cerca pattern nel nome
        for hint, city_key in NAME_PATTERN_HINTS:
            if hint in name:
                coords = CITY_COORDS.get(city_key)
                jitter_scale = 0.025  # jitter intermedio: indizio meno preciso
                break

    if coords is None:
        return None

    # Jitter deterministico per venue id (così non cambia tra render)
    seed = venue.get("id") or hash(venue.get("name") or "")
    rng = random.Random(seed)
    lat = coords[0] + (rng.random() - 0.5) * jitter_scale
    lon = coords[1] + (rng.random() - 0.5) * jitter_scale
    return (lat, lon)


def autocoord_all_venues() -> int:
    """Geocodifica TUTTE le venue senza coordinate usando solo la cache hardcoded.
    Restituisce il numero di venue aggiornate. Zero chiamate di rete."""
    venues = db.list_venues()
    updated = 0
    for v in venues:
        if v.get("lat") and v.get("lon"):
            continue
        coords = fast_geocode_venue(v)
        if coords:
            db.update_venue(v["id"], {"lat": coords[0], "lon": coords[1]})
            updated += 1
    return updated

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "outreach-tool-stefano/1.0"
RATE_LIMIT_SECONDS = 1.1

_last_request_at: float = 0.0


def _cache_key(query: str) -> str:
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return f"geocode:{h}"


def _build_query(name: Optional[str], address: Optional[str], city: Optional[str], region: Optional[str]) -> str:
    parts = [p for p in (name, address, city, region, "Italia") if p]
    return ", ".join(parts)


def geocode(name: Optional[str] = None, address: Optional[str] = None, city: Optional[str] = None, region: Optional[str] = None) -> Optional[tuple[float, float]]:
    """Return (lat, lon) or None. Caches by query hash; respects Nominatim rate limit."""
    global _last_request_at
    query = _build_query(name, address, city, region)
    if not query.strip(", Italia"):
        return None

    cache_key = _cache_key(query)
    cached = db.get_setting(cache_key)
    if cached is not None:
        try:
            data = json.loads(cached)
            if data is None:
                return None
            return float(data["lat"]), float(data["lon"])
        except Exception:
            pass

    elapsed = time.monotonic() - _last_request_at
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)

    # Timeout 8s + 1 retry: una venue lenta non deve bloccare l'UI per 15s.
    # Nominatim risponde tipicamente <2s; un fallimento isolato è transiente.
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "it"},
                headers={"User-Agent": USER_AGENT, "Accept-Language": "it,en"},
                timeout=8,
            )
            _last_request_at = time.monotonic()
            if resp.status_code != 200:
                return None
            results = resp.json()
            if not results:
                db.set_setting(cache_key, json.dumps(None))
                return None
            first = results[0]
            lat = float(first["lat"])
            lon = float(first["lon"])
            db.set_setting(cache_key, json.dumps({"lat": lat, "lon": lon}))
            return lat, lon
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            _last_request_at = time.monotonic()
            if attempt == 0:
                time.sleep(1.5)
                continue
            return None
        except Exception:
            return None
    return None


def geocode_venue(venue: dict) -> Optional[tuple[float, float]]:
    """Try the most specific lookup first (address+city), then fall back to name+city."""
    if venue.get("address") and venue.get("city"):
        result = geocode(address=venue["address"], city=venue["city"], region=venue.get("region"))
        if result:
            return result
    if venue.get("name") and venue.get("city"):
        return geocode(name=venue["name"], city=venue["city"], region=venue.get("region"))
    if venue.get("city"):
        return geocode(city=venue["city"], region=venue.get("region"))
    return None
