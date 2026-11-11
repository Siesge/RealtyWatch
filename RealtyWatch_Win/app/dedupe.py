"""Объединение дублей: одинаковые квартиры с разных сайтов (и от разных агентов на одном сайте)
получают общий object_id. Вызывается при каждой записи объявления во время сбора."""
import re

from . import rules
from .geo import haversine_m

MAX_AREA_DIFF = 1.0     # м²
MAX_DISTANCE = 150      # м — разные сайты по-разному геокодируют адрес
MAX_PRICE_DIFF = 0.10   # 10% — цена у разных агентов отличается
# Версия правил: при её смене объекты в базе пересобираются при запуске программы
RULES_VERSION = 2
# «Мягкое» совпадение — одна квартира вторичного жилья на РАЗНЫХ сайтах, у которой сайты расходятся в одном
# признаке: площадь (один считает с балконом, другой без: 45 и 47,7; 55,1 и 57) или этажность дома (2/3, 9/11 —
# цоколь, мансарда, ошибка ввода). Остальное при этом должно совпадать почти точно: тот же дом, тот же этаж
# и комнаты, цена в пределах 2%. В новостройках не применяется: там на одном этаже бывает несколько
# одинаковых квартир по одной цене.
NEAR_DISTANCE = 60      # м — тот же дом
NEAR_PRICE_DIFF = 0.02
SOFT_AREA_DIFF = 3.0    # м² или
SOFT_AREA_SHARE = 0.06  # 6% площади — что больше
EXACT_AREA_DIFF = 0.5   # при расхождении в этажности площадь должна совпасть
_DEG = 0.0025           # ~250 м: грубый отбор кандидатов по координатам

_FLAT_NO = re.compile(r"\bкв(?:артир[аы])?\.?\s*№?\s*(\d{1,4})\b", re.I)
_BLOCK_NO = re.compile(r"\b(?:корпус[ае]?|корп\.)\s*№?\s*(\d{1,3})\b", re.I)

COLS = ("source, source_id, object_id, rooms, area, floor, floors, lat, lon, price, is_new_building, build_year, "
        "description, cluster_key, address")


def _num(rx, text):
    m = rx.search(text or "")
    return m.group(1) if m else None


_ADDR_JUNK = re.compile(
    r"россия|республика крым|крым республика|респ\.? крым|муниципальный округ ялта|городской округ ялта|г\.\s*о\.\s*ялта"
    r"|ялта г\.?|г\.\s*ялта|\bялта\b|\bг\.")
_ADDR_TYPES = re.compile(
    r"\b(улица|ул|переулок|пер|проспект|пр-кт|просп|шоссе|ш|набережная|наб|проезд|бульвар|б-р|тупик|спуск|дом|д)\b\.?")
_ADDR = re.compile(r"([а-я][а-я\-\. ]{2,}?)[\s,]+(\d+[а-я]?)\b")


def address_key(address):
    """«Крым Республика, Ялта г., Киевская ул., д.9» и «ул. Киевская, 9» → «киевская|9»."""
    a = _ADDR_TYPES.sub(" ", _ADDR_JUNK.sub(" ", rules.norm(address)))
    m = _ADDR.search(a)
    if not m:
        return None
    return f"{re.sub(r'[^а-я]', '', m.group(1))[:8]}|{m.group(2)}"


# Признаки новостройки в тексте: отметки сайта «новостройка» нет у Авито вовсе, а у ЦИАН и Яндекса недавно
# сданные дома часто числятся вторичкой. Мягкое совпадение там опасно — одинаковые квартиры на одном этаже
_NEW_TEXT = re.compile(r"новостро|жк|жилой комплекс|застройщик|сдач[аи] дома|сдан в|ввод в эксплуатацию|черновая|"
                       r"черновой|предчистов|без отделки|white ?box|вайт ?бокс|клубный дом|дду|переуступк", re.I)
NEW_SINCE = 2005        # дом этого года постройки или новее — считаем новым


def _new(r):
    """Новостройка или дом, похожий на неё: отметка сайта, год постройки или слова в описании."""
    if r["is_new_building"] or (r["build_year"] or 0) >= NEW_SINCE:
        return True
    return bool(_NEW_TEXT.search(r["description"] or ""))


def _area_close(a, b, soft):
    lim = max(SOFT_AREA_DIFF, SOFT_AREA_SHARE * max(a["area"], b["area"])) if soft else MAX_AREA_DIFF
    return abs(a["area"] - b["area"]) <= lim


def _same_house(a, b):
    if a["lat"] is not None and b["lat"] is not None:
        return haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= NEAR_DISTANCE
    ka = address_key(a["address"])
    return bool(ka) and ka == address_key(b["address"])


def near_object(a, b, location=True):
    """Мягкое совпадение (см. NEAR_*): одна квартира вторичного жилья на разных сайтах, которые расходятся
    в площади или в этажности дома — но не в том и другом сразу."""
    if a["source"] == b["source"] or _new(a) or _new(b):
        return False
    if a["rooms"] is None or a["rooms"] != b["rooms"] or not a["area"] or not b["area"]:
        return False
    if not (a["floor"] and a["floor"] == b["floor"]):
        return False
    if not (a["price"] and b["price"]) or abs(a["price"] - b["price"]) / max(a["price"], b["price"]) > NEAR_PRICE_DIFF:
        return False
    if location and not _same_house(a, b):
        return False
    floors_same = not (a["floors"] and b["floors"] and a["floors"] != b["floors"])
    if floors_same:
        return _area_close(a, b, soft=True)
    return abs(a["area"] - b["area"]) <= EXACT_AREA_DIFF


def compatible(a, b):
    """Могут ли два объявления одного будущего объекта быть одной квартирой — проверка «каждый с каждым» при
    слиянии объектов. Для вторичного жилья этажность дома не сравнивается (сайты в ней расходятся чаще, чем
    в чём-либо ещё), а площадь — с мягким допуском; в новостройках — те же строгие правила, что и для пары."""
    if _new(a) or _new(b):
        return same_object(a, b, location=False)
    if a["rooms"] is None or a["rooms"] != b["rooms"] or not a["area"] or not b["area"]:
        return False
    if not (a["floor"] and a["floor"] == b["floor"]) or not _area_close(a, b, soft=True):
        return False
    return not (a["price"] and b["price"] and abs(a["price"] - b["price"]) / max(a["price"], b["price"]) > MAX_PRICE_DIFF)


def same_object(a, b, location=True):
    """Похожи ли два объявления на одну квартиру.
    Метка дублей Яндекса (cluster_key) лишь помогает найти кандидатов: в новостройках она объединяет
    одинаковые планировки разных квартир, поэтому все проверки ниже обязательны и для неё."""
    return strict_object(a, b, location) or near_object(a, b, location)


def strict_object(a, b, location=True):
    """Строгое совпадение: те же комнаты, этаж и этажность, площадь ±1 м², адрес в пределах 150 м, цена ±10%."""
    if a["rooms"] is None or a["rooms"] != b["rooms"] or not a["area"] or not b["area"]:
        return False
    if abs(a["area"] - b["area"]) > MAX_AREA_DIFF:
        return False
    if not (a["floor"] and b["floor"] and a["floor"] == b["floor"]):
        return False
    if a["floors"] and b["floors"] and a["floors"] != b["floors"]:
        return False
    if not location:
        pass  # расположение уже подтверждено с другим объявлением объекта
    elif a["lat"] is not None and b["lat"] is not None:
        if haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) > MAX_DISTANCE:
            return False
    else:
        # нет координат (у Авито — пока не открыта страница объявления): нужен совпадающий адрес «улица + дом»
        ka = address_key(a["address"])
        if not ka or ka != address_key(b["address"]):
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
    if me["rooms"] is not None and me["floor"]:
        if me["lat"] is not None:
            conds.append("(rooms=? AND floor=? AND ((lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?) OR lat IS NULL))")
            args += [me["rooms"], me["floor"], me["lat"] - _DEG, me["lat"] + _DEG, me["lon"] - _DEG * 1.4, me["lon"] + _DEG * 1.4]
        else:
            conds.append("(rooms=? AND floor=?)")  # сравнение по адресу — в same_object
            args += [me["rooms"], me["floor"]]
    if source == "yandex" and me["cluster_key"]:
        conds.append("(source='yandex' AND cluster_key=?)")
        args.append(me["cluster_key"])
    cands = {}  # объект → совпал строго (иначе — только мягко)
    if conds:
        for other in con.execute(q + " OR ".join(conds) + ")", args):
            if other["object_id"] and other["object_id"] not in linked:
                if strict_object(me, other):
                    cands[other["object_id"]] = True
                elif near_object(me, other):
                    cands.setdefault(other["object_id"], False)
    # Полная связь по характеристикам: объект присоединяется, только если КАЖДОЕ его объявление совместимо
    # с каждым в группе по комнатам, площади, этажу, цене и номерам квартир — иначе цепочка «A≈B, B≈C» склеила бы
    # разные квартиры одной планировки в ЖК. Расположение достаточно подтвердить с одним объявлением (тот же дом).
    def members(oid):
        return con.execute(f"SELECT {COLS} FROM listings WHERE object_id=? AND NOT (source=? AND source_id=?)",
                           (oid, source, source_id)).fetchall()

    group = [me] + (members(me["object_id"]) if me["object_id"] else [])
    # сначала объекты, совпавшие строго: мягкое совпадение не должно уводить объявление от его точного дубля
    for oid_c in sorted(cands, key=lambda o: (not cands[o], -len(members(o)), o)):
        extra = members(oid_c)
        if all(compatible(a, b) for a in extra for b in group):
            linked.add(oid_c)
            group += extra
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
