"""Геозона и классификация объявлений:
A — внутри зоны (красный круг), B — остальной город Ялта, C — посёлки Большой Ялты.
Объявления за пределами Ялтинского округа (Партенит и т.п.) помечаются как outside."""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Населённые пункты Ялтинского округа, которые не относятся к самому городу
_SETTLEMENTS = [
    "кореиз", "гаспра", "ливадия", "массандра", "гурзуф", "алупка", "симеиз", "форос", "восход",
    "никита", "виноградное", "отрадное", "куйбышево", "береговое", "кацивели", "понизовка",
    "партизанское", "санаторное", "краснокаменка", "даниловка", "загорское", "охотничье",
    "советское", "запрудное", "голубой залив", "оползневое", "парковое", "олива", "николаевка",
    "мисхор", "ореанда", "горное", "дачное", "курпаты", "семидворье", "соколиное", "партенит",
]
_SETTLEMENT_RE = re.compile(
    r"\b(?:п\.?\s?г\.?\s?т\.?|посёлок|поселок|село|пос\.|с\.)\s*[\w\-]+|\b(" + "|".join(_SETTLEMENTS) + r")\b", re.I)
_CITY_RE = re.compile(r"\bг\.?\s*Ялта\b|\bЯлта\s+г\b|город\s+Ялта|(?:^|,)\s*Ялта\s*(?:,|$)", re.I)


@lru_cache(maxsize=None)
def _polygon(name):
    try:
        return json.loads((_HERE / name).read_text(encoding="utf-8"))["polygon"]
    except (OSError, ValueError, KeyError):
        return None


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _in_polygon(lat, lon, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_zone(lat, lon, zone):
    if lat is None or lon is None:
        return False
    if zone.get("polygon"):
        return _in_polygon(lat, lon, zone["polygon"])
    c = zone["center"]
    return haversine_m(lat, lon, c[0], c[1]) <= zone["radius_m"]


def other_settlement(address):
    """Название посёлка, если адрес явно не в городе Ялта."""
    if not address:
        return None
    m = _SETTLEMENT_RE.search(address)
    return m.group(0).strip() if m else None


def outside_district(item):
    """True, если координаты вне Ялтинского округа (сайты иногда подмешивают Алушту и др.)."""
    lat, lon = item.get("lat"), item.get("lon")
    poly = _polygon("yalta_district.json")
    if lat is None or lon is None or not poly:
        return "алушта" in (item.get("address") or "").lower()
    return not _in_polygon(lat, lon, poly)


def classify(item, zone):
    if in_zone(item.get("lat"), item.get("lon"), zone):
        return "A"
    address = item.get("address") or ""
    if item.get("settlement_outside") or other_settlement(address):
        return "C"
    if _CITY_RE.search(address):
        return "B"
    lat, lon = item.get("lat"), item.get("lon")
    city = _polygon("yalta_city.json")
    if lat is not None and lon is not None and city:
        return "B" if _in_polygon(lat, lon, city) else "C"
    return "B"
