"""Объединение дублей: одинаковые квартиры с разных сайтов (и от разных агентов на одном сайте)
получают общий object_id. Вызывается при каждой записи объявления во время сбора."""
import re

from .geo import haversine_m

MAX_AREA_DIFF = 1.0     # м²
MAX_DISTANCE = 150      # м — разные сайты по-разному геокодируют адрес
MAX_PRICE_DIFF = 0.10   # 10% — цена у разных агентов отличается
_DEG = 0.0025           # ~250 м: грубый отбор кандидатов по координатам

_FLAT_NO = re.compile(r"\bкв(?:артир[аы])?\.?\s*№?\s*(\d{1,4})\b", re.I)
_BLOCK_NO = re.compile(r"\b(?:корпус[ае]?|корп\.)\s*№?\s*(\d{1,3})\b", re.I)

COLS = "source, source_id, object_id, rooms, area, floor, floors, lat, lon, price, is_new_building, description, cluster_key"


def _num(rx, text):
    m = rx.search(text or "")
    return m.group(1) if m else None


def same_object(a, b):
    """Похожи ли два объявления на одну квартиру."""
    if a["source"] == "yandex" == b["source"] and a["cluster_key"] and a["cluster_key"] == b["cluster_key"]:
        return True  # Яндекс сам склеил дубли
    if a["rooms"] is None or a["rooms"] != b["rooms"] or not a["area"] or not b["area"]:
        return False
    if abs(a["area"] - b["area"]) > MAX_AREA_DIFF:
        return False
    if not (a["floor"] and b["floor"] and a["floor"] == b["floor"]):
        return False
    if a["floors"] and b["floors"] and a["floors"] != b["floors"]:
        return False
    if a["lat"] is None or b["lat"] is None or haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) > MAX_DISTANCE:
        return False
    if a["price"] and b["price"] and abs(a["price"] - b["price"]) / max(a["price"], b["price"]) > MAX_PRICE_DIFF:
        return False
    if a["is_new_building"] or b["is_new_building"]:
        # В новостройке одинаковые планировки на одном этаже в разных корпусах — разные квартиры
        for rx in (_FLAT_NO, _BLOCK_NO):
            x, y = _num(rx, a["description"]), _num(rx, b["description"])
            if x and y and x != y:
                return False
        if a["source"] == b["source"]:
            fa, fb = _num(_FLAT_NO, a["description"]), _num(_FLAT_NO, b["description"])
            if not (fa and fa == fb):
                return False  # у застройщика на одном сайте без номера квартиры не склеиваем
    return True


def assign(con, source, source_id):
    """Определяет объект для объявления; при необходимости сливает объекты в один."""
    me = con.execute(f"SELECT {COLS} FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    if me is None:
        return None
    linked = {me["object_id"]} if me["object_id"] else set()
    q = f"SELECT {COLS} FROM listings WHERE NOT (source=? AND source_id=?) AND ("
    args = [source, source_id]
    conds = []
    if me["lat"] is not None and me["rooms"] is not None and me["floor"]:
        conds.append("(rooms=? AND floor=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?)")
        args += [me["rooms"], me["floor"], me["lat"] - _DEG, me["lat"] + _DEG, me["lon"] - _DEG * 1.4, me["lon"] + _DEG * 1.4]
    if source == "yandex" and me["cluster_key"]:
        conds.append("(source='yandex' AND cluster_key=?)")
        args.append(me["cluster_key"])
    if conds:
        for other in con.execute(q + " OR ".join(conds) + ")", args):
            if other["object_id"] and same_object(me, other):
                linked.add(other["object_id"])
    oid = min(linked) if linked else f"{source}:{source_id}"
    con.execute("UPDATE listings SET object_id=? WHERE source=? AND source_id=?", (oid, source, source_id))
    for other in linked - {oid}:
        con.execute("UPDATE listings SET object_id=? WHERE object_id=?", (oid, other))
    return oid


def rebuild(con):
    """Полный пересчёт объектов (после импорта, смены правил)."""
    con.execute("UPDATE listings SET object_id=NULL")
    keys = con.execute("SELECT source, source_id FROM listings ORDER BY first_seen, source, source_id").fetchall()
    for k in keys:
        assign(con, k["source"], k["source_id"])
    con.commit()
    return con.execute("SELECT COUNT(DISTINCT object_id) FROM listings").fetchone()[0]
