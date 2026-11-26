"""Объединение дублей: одинаковые квартиры с разных сайтов (и от разных агентов на одном сайте)
получают общий object_id. Вызывается при каждой записи объявления во время сбора."""
import re

from . import db, images, rules
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
        "description, cluster_key, address, first_seen")


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


def active_manual_merges(con):
    """Активные ручные связки (>= 2 существующих объявлений в группе).
    Возвращает ({group_id: [(source, source_id), ...]}, {(source, source_id) главных})."""
    rows = con.execute("""
        SELECT m.source, m.source_id, m.group_id, m.is_primary, l.first_seen
        FROM manual_merges m
        JOIN listings l ON l.source = m.source AND l.source_id = m.source_id
        ORDER BY m.group_id, m.is_primary DESC, l.first_seen ASC, m.source ASC, m.source_id ASC
    """).fetchall()
    raw = {}
    for r in rows:
        raw.setdefault(r["group_id"], []).append((r["source"], str(r["source_id"])))
    by_group = {}
    primaries = set()
    for gid, members in raw.items():
        if len(members) >= 2:
            by_group[gid] = members
            primaries.add(members[0])
    return by_group, primaries


def _rank_key(r, have_img):
    """Ранжирование объявлений внутри объекта — как buildObjects в интерфейсе."""
    has_img = (r["source"], str(r["source_id"])) in have_img
    score = (0 if r["excluded"] else 8) + (4 if r["active"] else 0) + (2 if has_img else 0) + (1 if r["lat"] is not None else 0)
    return (-score, r["first_seen"] or "", r["source"], str(r["source_id"]))


def _row_sort_key(r):
    """Порядок обхода объявлений при пересчёте, совпадающий с SQL ORDER BY first_seen, source, source_id."""
    return (r["first_seen"] is not None, r["first_seen"] or "", r["source"], str(r["source_id"]))


def _chunked(seq, size=400):
    items = list(seq)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fetch_by_keys(con, keys):
    out = []
    for chunk in _chunked(keys):
        cond = " OR ".join("(source=? AND source_id=?)" for _ in chunk)
        args = [x for pair in chunk for x in pair]
        out.extend(dict(r) for r in con.execute(f"SELECT {COLS} FROM listings WHERE {cond}", args))
    return out


def _fetch_by_oids(con, oids):
    out = []
    for chunk in _chunked(oids, size=800):
        ph = ",".join("?" * len(chunk))
        out.extend(dict(r) for r in con.execute(f"SELECT {COLS} FROM listings WHERE object_id IN ({ph})", list(chunk)))
    return out


def _fetch_by_rf(con, rf_set):
    if not rf_set:
        return [], True
    out = []
    rf_list = list(rf_set)
    for chunk in _chunked(rf_list):
        cond = " OR ".join("(rooms=? AND floor=?)" for _ in chunk)
        args = [x for pair in chunk for x in pair]
        order = " ORDER BY first_seen, source, source_id" if len(rf_list) <= 400 else ""
        out.extend(dict(r) for r in con.execute(f"SELECT {COLS} FROM listings WHERE {cond}{order}", args))
    return out, len(rf_list) <= 400


def _local_img_keys(rows):
    return {(r["source"], str(r["source_id"])) for r in rows if images.local_path(r["source"], str(r["source_id"])) is not None}


def _compute_auto_groups(rows, presorted=False):
    """Чистая автоматическая дедупликация в памяти без учёта ручных объединений."""
    work = list(rows) if presorted else sorted(rows, key=_row_sort_key)
    work = [dict(r, object_id=None) for r in work]
    by_rf = {}       # (rooms, floor) → список уже обработанных объявлений
    by_ycluster = {} # cluster_key Яндекса → список уже обработанных объявлений Яндекса
    groups = {}      # object_id → список объявлений группы
    for me in work:
        seen_other = set()
        cand_rows = []
        if me["rooms"] is not None and me["floor"]:
            lat, lon = me["lat"], me["lon"]
            for o in by_rf.get((me["rooms"], me["floor"]), ()):
                if lat is None or o["lat"] is None or (
                    lat - _DEG <= o["lat"] <= lat + _DEG and lon - _DEG * 1.4 <= o["lon"] <= lon + _DEG * 1.4
                ):
                    k = (o["source"], o["source_id"])
                    if k not in seen_other:
                        seen_other.add(k)
                        cand_rows.append(o)
        if me["source"] == "yandex" and me["cluster_key"]:
            for o in by_ycluster.get(me["cluster_key"], ()):
                k = (o["source"], o["source_id"])
                if k not in seen_other:
                    seen_other.add(k)
                    cand_rows.append(o)
        cands = {}
        for other in cand_rows:
            oid_o = other["object_id"]
            if oid_o:
                if strict_object(me, other):
                    cands[oid_o] = True
                elif near_object(me, other):
                    cands.setdefault(oid_o, False)
        group = [me]
        linked = set()
        for oid_c in sorted(cands, key=lambda o: (not cands[o], -len(groups[o]), o)):
            extra = groups[oid_c]
            if all(compatible(a, b) for a in extra for b in group):
                linked.add(oid_c)
                group.extend(extra)
        oid = min(linked) if linked else f"{me['source']}:{me['source_id']}"
        for old_oid in linked:
            if old_oid != oid:
                groups.pop(old_oid, None)
        for m in group:
            m["object_id"] = oid
        groups[oid] = group
        if me["rooms"] is not None and me["floor"]:
            by_rf.setdefault((me["rooms"], me["floor"]), []).append(me)
        if me["source"] == "yandex" and me["cluster_key"]:
            by_ycluster.setdefault(me["cluster_key"], []).append(me)
    auto_by_key = {(r["source"], str(r["source_id"])): r["object_id"] for r in work}
    return work, groups, auto_by_key


def auto_group_map(con=None, rows=None, target_keys=None):
    """Карта исходных авто-групп {(source, source_id): auto_oid} без учёта manual_merges.
    Если передан target_keys, пересчитывает только (rooms, floor)-кластеры этих объявлений."""
    if target_keys is not None:
        tkeys = {(s, str(i)) for s, i in target_keys}
        if not tkeys:
            return {}
        if rows is not None:
            rf_set = {
                (r["rooms"], r["floor"])
                for r in rows
                if (r["source"], str(r["source_id"])) in tkeys and r["rooms"] is not None and r["floor"]
            }
            if rf_set:
                sub = [r for r in rows if (r["rooms"], r["floor"]) in rf_set]
                _, _, auto_by_key = _compute_auto_groups(sub, presorted=False)
            else:
                auto_by_key = {}
            return {k: auto_by_key.get(k, f"{k[0]}:{k[1]}") for k in tkeys}
        rf_set = set()
        for chunk in _chunked(tkeys):
            cond = " OR ".join("(source=? AND source_id=?)" for _ in chunk)
            args = [x for pair in chunk for x in pair]
            for r in con.execute(f"SELECT rooms, floor FROM listings WHERE {cond}", args):
                if r["rooms"] is not None and r["floor"]:
                    rf_set.add((r["rooms"], r["floor"]))
        sub, presorted = _fetch_by_rf(con, rf_set)
        auto_by_key = _compute_auto_groups(sub, presorted=presorted)[2] if sub else {}
        return {k: auto_by_key.get(k, f"{k[0]}:{k[1]}") for k in tkeys}

    presorted = rows is None
    if rows is None:
        rows = [dict(r) for r in con.execute(f"SELECT {COLS} FROM listings ORDER BY first_seen, source, source_id")]
    if not rows:
        return {}
    _, _, auto_by_key = _compute_auto_groups(rows, presorted=presorted)
    return auto_by_key


def clean_manual_merges(con, group_ids=None):
    """Удаляет осиротевшие записи и вырожденные группы (< 2 исходных авто-групп), следит за единственным is_primary=1."""
    if not con.execute("SELECT 1 FROM manual_merges LIMIT 1").fetchone():
        return
    con.execute("""DELETE FROM manual_merges WHERE NOT EXISTS (
        SELECT 1 FROM listings l WHERE l.source=manual_merges.source AND l.source_id=manual_merges.source_id)""")
    con.execute("""DELETE FROM manual_merges WHERE group_id IN (
        SELECT group_id FROM manual_merges GROUP BY group_id HAVING COUNT(*) < 2)""")
    if group_ids is not None and not group_ids:
        return
    if not con.execute("SELECT 1 FROM manual_merges LIMIT 1").fetchone():
        return
    q = """
        SELECT m.source, m.source_id, m.group_id, m.is_primary, m.ts,
               l.excluded, l.active, l.lat, l.first_seen
        FROM manual_merges m JOIN listings l USING(source, source_id)
    """
    args = []
    if group_ids is not None:
        glist = sorted(group_ids)
        q += f" WHERE m.group_id IN ({','.join('?' * len(glist))})"
        args = glist
    groups = {}
    for r in con.execute(q, args):
        groups.setdefault(r["group_id"], []).append(dict(r))
    if not groups:
        return
    member_keys = {(m["source"], str(m["source_id"])) for ms in groups.values() for m in ms}
    auto_map = auto_group_map(con, target_keys=member_keys)
    for gid, members in groups.items():
        # Если в группе осталась только 1 исходная авто-группа, ручное объединение больше не нужно
        distinct_ags = {auto_map.get((m["source"], str(m["source_id"])), f"{m['source']}:{m['source_id']}") for m in members}
        if len(distinct_ags) < 2:
            con.execute("DELETE FROM manual_merges WHERE group_id=?", (gid,))
            continue
        prim = [m for m in members if m["is_primary"]]
        if len(prim) == 1:
            continue
        if len(prim) > 1:
            keep = max(prim, key=lambda m: (m["ts"] or "", m["source"], str(m["source_id"])))
        else:
            keep = min(members, key=lambda m: _rank_key(m, _local_img_keys(members)))
        con.execute("UPDATE manual_merges SET is_primary=0 WHERE group_id=?", (gid,))
        con.execute("UPDATE manual_merges SET is_primary=1 WHERE group_id=? AND source=? AND source_id=?",
                    (gid, keep["source"], keep["source_id"]))


def _load_neighborhood(con, seed_keys):
    """Загружает минимальную замкнутую окрестность для seed_keys
    (по текущим object_id, ручным связкам и бакетам (rooms, floor))."""
    seeds = {(s, str(i)) for s, i in seed_keys}
    by_group, primaries = active_manual_merges(con)
    key_to_gid = {k: gid for gid, ks in by_group.items() for k in ks}
    row_map = {}
    keys = set(seeds)
    seen_oids = set()
    seen_gids = set()
    seen_rf = set()

    while True:
        missing = keys - row_map.keys()
        if missing:
            for r in _fetch_by_keys(con, missing):
                rk = (r["source"], str(r["source_id"]))
                row_map[rk] = r
        new_oids = {r["object_id"] for r in row_map.values() if r["object_id"] and r["object_id"] not in seen_oids}
        if new_oids:
            seen_oids |= new_oids
            for r in _fetch_by_oids(con, new_oids):
                rk = (r["source"], str(r["source_id"]))
                row_map[rk] = r
                keys.add(rk)
        new_gids = {key_to_gid[k] for k in list(keys) if k in key_to_gid and key_to_gid[k] not in seen_gids}
        if new_gids:
            seen_gids |= new_gids
            for gid in new_gids:
                keys.update(by_group[gid])
        new_rf = {
            (r["rooms"], r["floor"])
            for r in row_map.values()
            if r["rooms"] is not None and r["floor"] and (r["rooms"], r["floor"]) not in seen_rf
        }
        if new_rf:
            seen_rf |= new_rf
            rf_rows, _ = _fetch_by_rf(con, new_rf)
            for r in rf_rows:
                rk = (r["source"], str(r["source_id"]))
                row_map[rk] = r
                keys.add(rk)
        if not (keys - row_map.keys()) and not new_oids and not new_gids and not new_rf:
            break
    return row_map, seeds, by_group, primaries, seen_gids


def _apply_and_commit_slice(con, row_map, seeds, work, groups, by_group, seen_gids):
    """Применяет активные manual_merges к пересчитанной окрестности и обновляет изменившиеся строки."""
    orig_oid = {k: r["object_id"] for k, r in row_map.items()}
    row_by_key = {(r["source"], str(r["source_id"])): r for r in work}
    if seen_gids:
        for gid in sorted(seen_gids):
            if gid not in by_group:
                continue
            oids = {row_by_key[k]["object_id"] for k in by_group[gid] if k in row_by_key}
            if len(oids) <= 1:
                continue
            target = min(oids)
            merged_group = []
            for o in sorted(oids):
                part = groups.pop(o, ())
                for m in part:
                    m["object_id"] = target
                merged_group.extend(part)
            groups[target] = merged_group

    updates = []
    changed_keys = set()
    for r in work:
        k = (r["source"], str(r["source_id"]))
        if r["object_id"] != orig_oid[k]:
            updates.append((r["object_id"], r["source"], r["source_id"]))
            changed_keys.add(k)
    if updates:
        con.executemany("UPDATE listings SET object_id=? WHERE source=? AND source_id=?", updates)
    con.commit()

    objects = con.execute("SELECT COUNT(DISTINCT COALESCE(object_id, source || ':' || source_id)) FROM listings").fetchone()[0]
    touched_oids = {row_by_key[k]["object_id"] for k in (seeds | changed_keys) if k in row_by_key}
    touched_keys = {k for k, r in row_by_key.items() if k in seeds or k in changed_keys or r["object_id"] in touched_oids}
    return objects, touched_keys


def _rebuild_neighborhood(con, seed_keys):
    """Точечный пересчёт object_id для связной окрестности seed_keys."""
    row_map, seeds, by_group, _, seen_gids = _load_neighborhood(con, seed_keys)
    work, groups, _ = _compute_auto_groups(row_map.values(), presorted=False)
    return _apply_and_commit_slice(con, row_map, seeds, work, groups, by_group, seen_gids)


def _by_card_id(con, cols, card_id):
    rows = [dict(r) for r in con.execute(f"SELECT {cols} FROM listings WHERE object_id=?", (card_id,))]
    if rows:
        return rows
    return [dict(r) for r in con.execute(
        f"SELECT {cols} FROM listings WHERE object_id IS NULL AND source || ':' || source_id=?", (card_id,))]


def merge_objects(con, primary_id, secondary_id):
    """Ручное объединение двух карточек: параметры и первенство остаются у первой (primary_id)."""
    primary_id = str(primary_id or "").strip()
    secondary_id = str(secondary_id or "").strip()
    if not primary_id or not secondary_id or primary_id == secondary_id:
        raise ValueError("нужно выбрать две разные карточки")
    cols = "source, source_id, object_id, excluded, active, lat, first_seen"
    p_rows = _by_card_id(con, cols, primary_id)
    s_rows = _by_card_id(con, cols, secondary_id)
    if not p_rows or not s_rows:
        raise ValueError("одна из карточек не найдена в базе")
    p_keys = {(r["source"], str(r["source_id"])) for r in p_rows}
    s_keys = {(r["source"], str(r["source_id"])) for r in s_rows}
    if p_keys == s_keys:
        raise ValueError("нужно выбрать две разные карточки")

    by_group, primaries = active_manual_merges(con)
    prim_row = next((r for r in p_rows if (r["source"], str(r["source_id"])) in primaries), None)
    if prim_row is None:
        prim_row = min(p_rows, key=lambda r: _rank_key(r, _local_img_keys(p_rows)))
    p_src, p_sid = prim_row["source"], str(prim_row["source_id"])

    all_keys = p_keys | s_keys
    old_gids = set()
    for src, sid in list(all_keys):
        row = con.execute("SELECT group_id FROM manual_merges WHERE source=? AND source_id=?", (src, sid)).fetchone()
        if row:
            old_gids.add(row["group_id"])
    for og in old_gids:
        for r in con.execute("SELECT source, source_id FROM manual_merges WHERE group_id=?", (og,)):
            all_keys.add((r["source"], str(r["source_id"])))

    existing_p = con.execute("SELECT group_id FROM manual_merges WHERE source=? AND source_id=?", (p_src, p_sid)).fetchone()
    if existing_p and (p_src, p_sid) in primaries:
        gid = existing_p["group_id"]
    else:
        gid = f"{p_src}:{p_sid}"
        conflict = con.execute("SELECT 1 FROM manual_merges WHERE group_id=?", (gid,)).fetchone()
        if conflict and gid not in old_gids:
            gid = f"{gid}#{db.now()}"

    ts = db.now()
    con.executemany(
        "INSERT OR REPLACE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)",
        [(src, sid, gid, 1 if (src, sid) == (p_src, p_sid) else 0, ts) for src, sid in sorted(all_keys)],
    )
    p_oid = p_rows[0]["object_id"]
    s_oid = s_rows[0]["object_id"]
    if (
        p_oid and s_oid
        and all(r["object_id"] == p_oid for r in p_rows)
        and all(r["object_id"] == s_oid for r in s_rows)
        and len(all_keys) == len(p_keys) + len(s_keys)
    ):
        new_oid = min(p_oid, s_oid)
        other_oid = s_oid if new_oid == p_oid else p_oid
        con.execute("UPDATE listings SET object_id=? WHERE object_id=?", (new_oid, other_oid))
        con.commit()
        objects = con.execute("SELECT COUNT(DISTINCT COALESCE(object_id, source || ':' || source_id)) FROM listings").fetchone()[0]
        touched_keys = all_keys
    else:
        clean_manual_merges(con, group_ids=old_gids | {gid})
        objects, touched_keys = _rebuild_neighborhood(con, all_keys)
        new_oid = con.execute(
            "SELECT COALESCE(object_id, source || ':' || source_id) FROM listings WHERE source=? AND source_id=?",
            (p_src, p_sid),
        ).fetchone()[0]
    return {
        "object_id": new_oid, "group_id": gid, "items": len(all_keys),
        "objects": objects, "manual_merges": len(active_manual_merges(con)[0]),
        "_keys": sorted(touched_keys),
    }


def unmerge_all(con):
    """Сбрасывает все ручные объединения и пересобирает объекты по автоматическим правилам."""
    unmerged = len(active_manual_merges(con)[0])
    con.execute("DELETE FROM manual_merges")
    con.commit()
    objects = rebuild(con)
    return {"unmerged": unmerged, "objects": objects}


def unmerge_object(con, object_id):
    """Точечно разделяет одну объединённую вручную карточку обратно на исходные."""
    object_id = str(object_id or "").strip()
    if not object_id:
        raise ValueError("не указана карточка")
    obj_rows = _by_card_id(con, "source, source_id", object_id)
    if not obj_rows:
        raise ValueError("карточка не найдена в базе")
    obj_keys = {(r["source"], str(r["source_id"])) for r in obj_rows}
    gids = set()
    for src, sid in obj_keys:
        row = con.execute("SELECT group_id FROM manual_merges WHERE source=? AND source_id=?", (src, sid)).fetchone()
        if row:
            gids.add(row["group_id"])
    if not gids:
        raise ValueError("карточка не объединена вручную")
    seed_keys = set(obj_keys)
    for gid in gids:
        for r in con.execute("SELECT source, source_id FROM manual_merges WHERE group_id=?", (gid,)):
            seed_keys.add((r["source"], str(r["source_id"])))
        con.execute("DELETE FROM manual_merges WHERE group_id=?", (gid,))
    for src, sid in obj_keys:
        con.execute("DELETE FROM manual_merges WHERE source=? AND source_id=?", (src, sid))
    con.execute("""DELETE FROM manual_merges WHERE NOT EXISTS (
        SELECT 1 FROM listings l WHERE l.source=manual_merges.source AND l.source_id=manual_merges.source_id)""")
    con.execute("""DELETE FROM manual_merges WHERE group_id IN (
        SELECT group_id FROM manual_merges GROUP BY group_id HAVING COUNT(*) < 2)""")
    objects, touched_keys = _rebuild_neighborhood(con, seed_keys)
    after_oids = set()
    for src, sid in obj_keys:
        row = con.execute(
            "SELECT COALESCE(object_id, source || ':' || source_id) FROM listings WHERE source=? AND source_id=?",
            (src, sid),
        ).fetchone()
        if row:
            after_oids.add(row[0])
    return {
        "parts": len(after_oids), "objects": objects, "manual_merges": len(active_manual_merges(con)[0]),
        "_keys": sorted(touched_keys),
    }


def exclude_from_merge(con, object_id, source, source_id):
    """Исключает из ручного объединения всю исходную авто-группу выбранного объявления."""
    object_id = str(object_id or "").strip()
    source = str(source or "").strip()
    sid = str(source_id or "").strip()
    if not object_id or not source or not sid:
        raise ValueError("нужно указать карточку и объявление")
    obj_rows = _by_card_id(con, "source, source_id, object_id", object_id)
    if not obj_rows:
        raise ValueError("карточка не найдена в базе")
    obj_keys = {(r["source"], str(r["source_id"])) for r in obj_rows}
    if (source, sid) not in obj_keys:
        raise ValueError("объявление не найдено в карточке")

    gids = set()
    gid = None
    prim_gid = None
    for s, i in obj_keys:
        row = con.execute("SELECT group_id, is_primary FROM manual_merges WHERE source=? AND source_id=?", (s, i)).fetchone()
        if row:
            gids.add(row["group_id"])
            if row["is_primary"]:
                prim_gid = row["group_id"]
    if not gids:
        raise ValueError("карточка не объединена вручную")
    gid = prim_gid or sorted(gids)[0]

    gid_members = {
        (r["source"], str(r["source_id"]))
        for r in con.execute(f"SELECT source, source_id FROM manual_merges WHERE group_id IN ({','.join('?' * len(gids))})", list(gids))
    }
    seed_keys = obj_keys | gid_members
    row_map, seeds, _, _, seen_gids = _load_neighborhood(con, seed_keys)
    work, groups, auto_map = _compute_auto_groups(row_map.values(), presorted=False)

    target_ag = auto_map.get((source, sid), f"{source}:{sid}")
    drop_keys = {k for k in obj_keys if auto_map.get(k, f"{k[0]}:{k[1]}") == target_ag}
    keep_keys = obj_keys - drop_keys
    if not keep_keys:
        raise ValueError("в карточке нет других групп для отделения")

    # Удаляем исключённую авто-группу из ручной связки
    for rk in gid_members:
        if rk in drop_keys or auto_map.get(rk, f"{rk[0]}:{rk[1]}") == target_ag:
            con.execute("DELETE FROM manual_merges WHERE source=? AND source_id=?", rk)
    for rk in drop_keys:
        con.execute("DELETE FROM manual_merges WHERE source=? AND source_id=?", rk)

    keep_ags = {auto_map.get(k, f"{k[0]}:{k[1]}") for k in keep_keys}
    if len(keep_ags) < 2:
        con.execute("DELETE FROM manual_merges WHERE group_id=?", (gid,))
        for sk in keep_keys:
            con.execute("DELETE FROM manual_merges WHERE source=? AND source_id=?", sk)
    else:
        ts = db.now()
        existing_prim = [
            sk for sk in keep_keys
            if (con.execute("SELECT is_primary FROM manual_merges WHERE source=? AND source_id=?", sk).fetchone() or [0])[0]
        ]
        if len(existing_prim) == 1:
            prim_keep = existing_prim[0]
        elif len(existing_prim) > 1:
            prim_keep = sorted(existing_prim)[0]
        else:
            keep_ext = [
                dict(r) for r in con.execute(
                    "SELECT source, source_id, excluded, active, lat, first_seen FROM listings WHERE "
                    + " OR ".join("(source=? AND source_id=?)" for _ in keep_keys),
                    [x for pair in sorted(keep_keys) for x in pair],
                )
            ]
            best_k = min(keep_ext, key=lambda m: _rank_key(m, _local_img_keys(keep_ext)))
            prim_keep = (best_k["source"], str(best_k["source_id"]))
        con.executemany(
            "INSERT OR REPLACE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)",
            [(sk[0], sk[1], gid, 1 if sk == prim_keep else 0, ts) for sk in sorted(keep_keys)],
        )

    by_group, primaries = active_manual_merges(con)
    objects, touched_keys = _apply_and_commit_slice(con, row_map, seeds, work, groups, by_group, seen_gids | {gid})
    still_merged = gid in by_group
    if still_merged:
        rem_key = next((k for k in by_group[gid] if k in primaries), by_group[gid][0])
    else:
        rem_key = sorted(keep_keys)[0]
    remaining_object_id = con.execute(
        "SELECT COALESCE(object_id, source || ':' || source_id) FROM listings WHERE source=? AND source_id=?",
        rem_key,
    ).fetchone()[0]
    excluded_object_id = con.execute(
        "SELECT COALESCE(object_id, source || ':' || source_id) FROM listings WHERE source=? AND source_id=?",
        (source, sid),
    ).fetchone()[0]
    return {
        "excluded_keys": [f"{s}:{i}" for s, i in sorted(drop_keys)],
        "excluded_count": len(drop_keys),
        "still_merged": still_merged,
        "remaining_object_id": remaining_object_id,
        "excluded_object_id": excluded_object_id,
        "objects": objects,
        "manual_merges": len(by_group),
        "_keys": sorted(touched_keys),
    }


def assign(con, source, source_id):
    """Определяет объект для объявления; при необходимости сливает объекты в один."""
    me = con.execute(f"SELECT {COLS} FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    if me is None:
        return None
    by_group, _ = active_manual_merges(con)
    key_to_gid = {k: gid for gid, ks in by_group.items() for k in ks}
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
    cands = {}       # объект → совпал строго (иначе — только мягко)
    matched_in = {}  # объект → объявления внутри него, совпавшие с me
    if conds:
        for other in con.execute(q + " OR ".join(conds) + ")", args):
            if other["object_id"] and other["object_id"] not in linked:
                if strict_object(me, other):
                    cands[other["object_id"]] = True
                    matched_in.setdefault(other["object_id"], []).append(other)
                elif near_object(me, other):
                    cands.setdefault(other["object_id"], False)
                    matched_in.setdefault(other["object_id"], []).append(other)
    # Полная связь по характеристикам: объект присоединяется, только если КАЖДОЕ его объявление совместимо
    # с каждым в группе по комнатам, площади, этажу, цене и номерам квартир — иначе цепочка «A≈B, B≈C» склеила бы
    # разные квартиры одной планировки в ЖК. Расположение достаточно подтвердить с одним объявлением (тот же дом).
    def members(oid):
        return con.execute(f"SELECT {COLS} FROM listings WHERE object_id=? AND NOT (source=? AND source_id=?)",
                           (oid, source, source_id)).fetchall()

    me_members = members(me["object_id"]) if me["object_id"] else []
    # Если me состоит в объекте с ручной склейкой, авто-совместимость проверяем с его авто-веткой
    group = [me] + [m for m in me_members if compatible(me, m)]
    members_map = {o: members(o) for o in cands}
    # сначала объекты, совпавшие строго: мягкое совпадение не должно уводить объявление от его точного дубля
    for oid_c in sorted(cands, key=lambda o: (not cands[o], -len(members_map[o]), o)):
        raw_extra = members_map[oid_c]
        if key_to_gid and any((m["source"], str(m["source_id"])) in key_to_gid for m in raw_extra):
            hits = matched_in[oid_c]
            extra = [m for m in raw_extra if any(
                (m["source"] == o["source"] and str(m["source_id"]) == str(o["source_id"])) or compatible(m, o)
                for o in hits
            )]
        else:
            extra = raw_extra
        if extra and all(compatible(a, b) for a in extra for b in group):
            linked.add(oid_c)
            group += extra
    auto_oid = min(linked) if linked else f"{source}:{source_id}"

    # Поверх авто-дедупликации применяем ручные связки: вливаем object_id всех партнёров по manual_merges
    manual_oids = set()
    if key_to_gid:
        seen_keys = {(str(m["source"]), str(m["source_id"])) for m in [me] + me_members}
        for o in linked:
            for m in members_map.get(o, ()):
                seen_keys.add((str(m["source"]), str(m["source_id"])))
        seen_oids = set(linked)
        queue = list(seen_keys)
        while queue:
            k = queue.pop()
            gid = key_to_gid.get(k)
            if not gid:
                continue
            for pk in by_group[gid]:
                if pk not in seen_keys:
                    seen_keys.add(pk)
                    queue.append(pk)
                    row = con.execute("SELECT object_id FROM listings WHERE source=? AND source_id=?", pk).fetchone()
                    p_oid = row["object_id"] if row else None
                    if p_oid and p_oid not in seen_oids:
                        seen_oids.add(p_oid)
                        manual_oids.add(p_oid)
                        for m in con.execute("SELECT source, source_id FROM listings WHERE object_id=?", (p_oid,)):
                            mk = (m["source"], str(m["source_id"]))
                            if mk not in seen_keys:
                                seen_keys.add(mk)
                                queue.append(mk)

    all_linked = linked | manual_oids
    oid = min(all_linked | {auto_oid}) if manual_oids else auto_oid
    con.execute("UPDATE listings SET object_id=? WHERE source=? AND source_id=?", (oid, source, source_id))
    for other in all_linked - {oid}:
        con.execute("UPDATE listings SET object_id=? WHERE object_id=?", (oid, other))
    return oid


def rebuild(con):
    """Полный пересчёт объектов в памяти (один проход и пакетный UPDATE только изменившихся строк)."""
    rows = [dict(r) for r in con.execute(f"SELECT {COLS} FROM listings ORDER BY first_seen, source, source_id")]
    if not rows:
        return 0
    orig = {(r["source"], str(r["source_id"])): r["object_id"] for r in rows}
    work, groups, _ = _compute_auto_groups(rows, presorted=True)

    # Поверх автоматической дедупликации применяем ручные объединения
    by_group, _ = active_manual_merges(con)
    if by_group:
        row_by_key = {(r["source"], str(r["source_id"])): r for r in work}
        for gid in sorted(by_group):
            oids = {row_by_key[k]["object_id"] for k in by_group[gid] if k in row_by_key}
            if len(oids) <= 1:
                continue
            target = min(oids)
            merged_group = []
            for o in sorted(oids):
                part = groups.pop(o, ())
                for m in part:
                    m["object_id"] = target
                merged_group.extend(part)
            groups[target] = merged_group

    updates = [
        (r["object_id"], r["source"], r["source_id"])
        for r in work
        if r["object_id"] != orig[(r["source"], str(r["source_id"]))]
    ]
    if updates:
        con.executemany("UPDATE listings SET object_id=? WHERE source=? AND source_id=?", updates)
    con.commit()
    return len(groups)
