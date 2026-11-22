"""История рынка: снимок после каждого запуска сбора (точка на графиках дашборда), события (для экрана «Изменения»),
восстановление истории по уже собранным данным и повторная проверка «снятых» объявлений."""
import bisect
import heapq
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime

from . import db, dedupe

ZONES = ("A", "B", "C")
ROOMS = (1, 2, 3)


def _med(xs):
    return round(statistics.median(xs)) if xs else None


def _mean(xs):
    return round(sum(xs) / len(xs)) if xs else None


def _objects(rows):
    """Объявления → объекты: цена объекта — минимальная из его активных подходящих объявлений."""
    objs = defaultdict(list)
    for r in rows:
        objs[r["object_id"] or f"{r['source']}:{r['source_id']}"].append(r)
    out = []
    for items in objs.values():
        best = min(items, key=lambda r: r["price"] or 10**12)
        head = next((r for r in items if r.get("manual_primary")), best)
        out.append({"price": best["price"], "area": head["area"], "rooms": head["rooms"], "zone": head["zone"],
                    "ppm": best["price"] / head["area"] if best["price"] and head["area"] else None,
                    "sources": {r["source"] for r in items}})
    return out


def _stats(objs):
    prices = [o["price"] for o in objs if o["price"]]
    ppm = [o["ppm"] for o in objs if o["ppm"]]
    return {"count": len(objs), "median_price": _med(prices), "mean_price": _mean(prices),
            "median_ppm": _med(ppm), "mean_ppm": _mean(ppm), "median_area": _med([o["area"] for o in objs if o["area"]])}


def _part(objs):
    """Срез по набору объектов: всё вместе, по комнатности, по сайтам, на нескольких сайтах."""
    return {"all": _stats(objs),
            "rooms": {str(k): _stats([o for o in objs if o["rooms"] == k]) for k in ROOMS},
            "sources": {s: sum(1 for o in objs if s in o["sources"]) for s in ("cian", "avito", "yandex")},
            "multi": sum(1 for o in objs if len(o["sources"]) > 1)}


def compute(rows, events=None, events_a=None):
    """Сводка по подходящим (не отсеянным) активным объявлениям; «Ялта» — зоны A и B.
    a — то же только внутри круга (зона A): для режима дашборда «только внутри круга»."""
    objs = _objects(rows)
    city = [o for o in objs if o["zone"] in ("A", "B")]
    part = _part(city)
    data = {"city": part["all"], "all": _stats(objs),
            "zones": {z: _stats([o for o in objs if o["zone"] == z]) for z in ZONES},
            "rooms": part["rooms"], "sources": part["sources"], "multi": part["multi"],
            "a": _part([o for o in objs if o["zone"] == "A"])}
    if events is not None:
        data["events"] = events
    if events_a is not None:
        data["events_a"] = events_a
    return data


def _event_counts(con, since, until, zone=None):
    q = "SELECT e.kind, e.old_value, e.new_value FROM events e"
    if zone:
        q += " JOIN listings l ON l.source=e.source AND l.source_id=e.source_id AND l.zone=?"
    q += " WHERE e.baseline=0 AND e.ts>? AND e.ts<=?"
    c = {"new": 0, "removed": 0, "returned": 0, "price_down": 0, "price_up": 0}
    for r in con.execute(q, ((zone,) if zone else ()) + (since or "", until)):
        if r["kind"] == "price":
            c["price_down" if (r["new_value"] or 0) < (r["old_value"] or 0) else "price_up"] += 1
        elif r["kind"] in c:
            c[r["kind"]] += 1
    return c


def snapshot(con, run_id=None):
    ensure_snapshots_fresh(con)
    ts = db.now()
    rows = [dict(r) for r in con.execute(
        "SELECT source, source_id, object_id, price, area, rooms, zone FROM listings "
        "WHERE active=1 AND excluded IS NULL AND COALESCE(imported_only, 0)=0")]
    _, primaries = dedupe.active_manual_merges(con)
    if primaries:
        for r in rows:
            r["manual_primary"] = (r["source"], str(r["source_id"])) in primaries
    prev = con.execute("SELECT ts FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    since = prev["ts"] if prev else None
    data = compute(rows, _event_counts(con, since, ts), _event_counts(con, since, ts, zone="A"))
    con.execute("INSERT INTO snapshots(ts, run_id, data) VALUES(?,?,?)", (ts, run_id, json.dumps(data, ensure_ascii=False)))
    con.commit()
    return data


def changes_summary(con, since):
    """Изменения рынка с момента since."""
    return _event_counts(con, since, "9999")


def runs_summary(con, run_ids):
    """Изменения рынка, найденные указанными прогонами (для уведомления после запуска)."""
    c = {"new": 0, "removed": 0, "returned": 0, "price_down": 0, "price_up": 0}
    if not run_ids:
        return c
    q = f"SELECT kind, old_value, new_value FROM events WHERE baseline=0 AND run_id IN ({','.join('?' * len(run_ids))})"
    for r in con.execute(q, list(run_ids)):
        if r["kind"] == "price":
            c["price_down" if (r["new_value"] or 0) < (r["old_value"] or 0) else "price_up"] += 1
        elif r["kind"] in c:
            c[r["kind"]] += 1
    return c


def backfill(con):
    """Восстанавливает события и снимки для данных, собранных до появления истории."""
    if not con.execute("SELECT 1 FROM events LIMIT 1").fetchone():
        first_done = {r["source"]: r["f"] for r in con.execute(
            "SELECT source, MIN(finished) f FROM runs WHERE status='done' GROUP BY source")}
        for r in con.execute("SELECT source, source_id, first_seen FROM listings"):
            base = int(not first_done.get(r["source"]) or r["first_seen"] <= first_done[r["source"]])
            hist = con.execute("SELECT ts, price FROM price_history WHERE source=? AND source_id=? ORDER BY ts",
                               (r["source"], r["source_id"])).fetchall()
            db.event(con, r["source"], r["source_id"], "new", new=hist[0]["price"] if hist else None, baseline=base, ts=r["first_seen"])
            for a, b in zip(hist, hist[1:]):
                if a["price"] != b["price"]:
                    db.event(con, r["source"], r["source_id"], "price", a["price"], b["price"], ts=b["ts"])
        con.commit()
    if not con.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone():
        # точка на каждую «пачку» завершённых прогонов: цены — по истории на тот момент
        ends = sorted({r["finished"][:15] + "9:59" for r in con.execute(
            "SELECT finished FROM runs WHERE status IN ('done','stopped') AND finished IS NOT NULL")})
        _write_snapshots(con, [(t, None) for t in ends])
    else:
        last = con.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if '"a":' not in last["data"]:  # снимки до появления среза «внутри круга» — пересобрать
            rebuild_snapshots(con)
    merge_launches(con)  # история, снятая по снимку на каждый сайт, сводится к одной точке на запуск


class _StatBucket:
    """Поддерживает статистику среза с ленивой синхронизацией и кэшированием неизменных снимков."""

    __slots__ = ("count", "sum_price", "_p", "_ppm", "_a", "_add_p", "_add_ppm", "_add_a",
                 "_rem_p", "_rem_ppm", "_rem_a", "_dirty", "_cache")

    def __init__(self):
        self.count = 0
        self.sum_price = 0
        self._p = []
        self._ppm = []
        self._a = []
        self._add_p = []
        self._add_ppm = []
        self._add_a = []
        self._rem_p = []
        self._rem_ppm = []
        self._rem_a = []
        self._dirty = False
        self._cache = None

    def add(self, price, ppm, area):
        self.count += 1
        self._dirty = True
        self._cache = None
        if price:
            self._add_p.append(price)
            self.sum_price += price
        if ppm:
            self._add_ppm.append(ppm)
        if area:
            self._add_a.append(area)

    def remove(self, price, ppm, area):
        self.count -= 1
        self._dirty = True
        self._cache = None
        if price:
            self._rem_p.append(price)
            self.sum_price -= price
        if ppm:
            self._rem_ppm.append(ppm)
        if area:
            self._rem_a.append(area)

    @staticmethod
    def _sync(arr, adds, rems):
        if not adds and not rems:
            return arr
        if rems:
            # Пока удалений заметно меньше длины массива, C memmove (pop) и bisect быстрее словаря в Python
            if not adds and len(rems) <= max(16, len(arr) >> 3):
                for v in rems:
                    arr.pop(bisect.bisect_left(arr, v))
                rems.clear()
                return arr
            cnt = {}
            for v in rems:
                cnt[v] = cnt.get(v, 0) + 1
            rems.clear()
            kept = []
            get_cnt = cnt.get
            for x in arr:
                c = get_cnt(x)
                if c:
                    if c == 1:
                        del cnt[x]
                    else:
                        cnt[x] = c - 1
                else:
                    kept.append(x)
            arr = kept
            if adds:
                if cnt:
                    for x in adds:
                        c = get_cnt(x)
                        if c:
                            if c == 1:
                                del cnt[x]
                            else:
                                cnt[x] = c - 1
                        else:
                            arr.append(x)
                else:
                    arr.extend(adds)
                arr.sort()
                adds.clear()
            return arr
        if arr and len(adds) <= 12:
            for v in adds:
                bisect.insort(arr, v)
            adds.clear()
            return arr
        arr.extend(adds)
        arr.sort()
        adds.clear()
        return arr

    @staticmethod
    def _med_sorted(xs):
        n = len(xs)
        if not n:
            return None
        m = n >> 1
        return round(xs[m] if n & 1 else (xs[m - 1] + xs[m]) / 2)

    def dump(self):
        if self._cache is not None:
            return self._cache
        if self._dirty:
            self._p = self._sync(self._p, self._add_p, self._rem_p)
            self._ppm = self._sync(self._ppm, self._add_ppm, self._rem_ppm)
            self._a = self._sync(self._a, self._add_a, self._rem_a)
            self._dirty = False
        np = len(self._p)
        npm = len(self._ppm)
        self._cache = {
            "count": self.count,
            "median_price": self._med_sorted(self._p),
            "mean_price": round(self.sum_price / np) if np else None,
            "median_ppm": self._med_sorted(self._ppm),
            "mean_ppm": round(sum(self._ppm) / npm) if npm else None,
            "median_area": self._med_sorted(self._a),
        }
        return self._cache


def _apply_bucket_delta(state, sign, b_all, b_city, b_z, b_city_r, b_zA_r, city_src, a_src, city_multi, a_multi):
    price, ppm, area, rooms, zone, srcs = state
    fn = b_all.add if sign > 0 else b_all.remove
    fn(price, ppm, area)
    zb = b_z.get(zone)
    if zb is not None:
        (zb.add if sign > 0 else zb.remove)(price, ppm, area)
    if zone == "A" or zone == "B":
        (b_city.add if sign > 0 else b_city.remove)(price, ppm, area)
        rb = b_city_r.get(rooms)
        if rb is not None:
            (rb.add if sign > 0 else rb.remove)(price, ppm, area)
        for s in srcs:
            if s in city_src:
                city_src[s] += sign
        if len(srcs) > 1:
            city_multi += sign
    if zone == "A":
        rb_a = b_zA_r.get(rooms)
        if rb_a is not None:
            (rb_a.add if sign > 0 else rb_a.remove)(price, ppm, area)
        for s in srcs:
            if s in a_src:
                a_src[s] += sign
        if len(srcs) > 1:
            a_multi += sign
    return city_multi, a_multi


_REMOVED = object()


def _write_snapshots(con, points):
    """Восстанавливает снимки рынка на моменты points [(ts, run_id)] по данным базы: какие объявления были
    в продаже на тот момент (появились раньше, сняты позже) и по какой цене (история цен).
    Зоны, отсев и объединение дублей — текущие, поэтому история согласована с нынешними правилами и кругом."""
    sorted_pts = sorted(points)
    if not sorted_pts:
        con.execute("DELETE FROM meta WHERE key='snapshots_dirty'")
        con.commit()
        return

    _, primaries = dedupe.active_manual_merges(con)
    hist = defaultdict(list)
    unsorted_hist = set()
    for source, sid, ts_h, price in con.execute("SELECT source, source_id, ts, price FROM price_history"):
        k = (source, sid)
        hl = hist[k]
        if hl and ts_h < hl[-1][0]:
            unsorted_hist.add(k)
        hl.append((ts_h, price))
    for k in unsorted_hist:
        hist[k].sort(key=lambda x: x[0])

    obj_items = defaultdict(list)
    for src, sid, oid_raw, area, rooms, zone, fs_raw, rem_raw in con.execute(
        "SELECT source, source_id, object_id, area, rooms, zone, first_seen, removed_at "
        "FROM listings WHERE (excluded IS NULL OR excluded = '') AND COALESCE(imported_only, 0)=0"
    ):
        h = hist.get((src, sid))
        if not h:
            continue
        fs = fs_raw or ""
        rem = rem_raw or ""
        birth = fs if fs > h[0][0] else h[0][0]
        if rem and rem <= birth:
            continue
        k = 0
        nh = len(h)
        while k < nh and h[k][0] <= birth:
            k += 1
        tl = [(birth, h[k - 1][1])]
        for idx_h in range(k, nh):
            ts_h, pr_h = h[idx_h]
            if rem and ts_h >= rem:
                break
            if ts_h == tl[-1][0]:
                tl[-1] = (ts_h, pr_h)
            elif pr_h != tl[-1][1]:
                tl.append((ts_h, pr_h))
        if rem:
            tl.append((rem, _REMOVED))
        oid = oid_raw or f"{src}:{sid}"
        obj_items[oid].append((src, area, rooms, zone, (src, str(sid)) in primaries, tl))

    objects = []
    obj_ts_list = []
    obj_pos_list = []
    heap = []
    for items in obj_items.values():
        ts_set = {ts_e for it in items for ts_e, _ in it[5]}
        ots = sorted(ts_set)
        oid_idx = len(objects)
        objects.append(items)
        obj_ts_list.append(ots)
        obj_pos_list.append([0] * len(items))
        heapq.heappush(heap, (ots[0], oid_idx, 0))

    cur_state = [None] * len(objects)
    b_all = _StatBucket()
    b_city = _StatBucket()
    b_z = {z: _StatBucket() for z in ZONES}
    b_city_r = {k: _StatBucket() for k in ROOMS}
    b_zA_r = {k: _StatBucket() for k in ROOMS}
    city_src = {"cian": 0, "avito": 0, "yandex": 0}
    a_src = {"cian": 0, "avito": 0, "yandex": 0}
    city_multi = 0
    a_multi = 0

    ev_rows = con.execute("""
        SELECT e.ts, e.kind, e.old_value, e.new_value, l.zone
        FROM events e LEFT JOIN listings l ON l.source=e.source AND l.source_id=e.source_id
        WHERE e.baseline=0 AND e.ts > '' ORDER BY e.ts
    """).fetchall()
    ev_idx = 0
    n_ev = len(ev_rows)

    heappop = heapq.heappop
    heappush = heapq.heappush
    out_rows = []
    for t, run_id in sorted_pts:
        while heap and heap[0][0] <= t:
            _, oid_idx, step_i = heappop(heap)
            ots = obj_ts_list[oid_idx]
            n_ots = len(ots)
            while step_i + 1 < n_ots and ots[step_i + 1] <= t:
                step_i += 1
            if step_i + 1 < n_ots:
                heappush(heap, (ots[step_i + 1], oid_idx, step_i + 1))

            items = objects[oid_idx]
            lpos = obj_pos_list[oid_idx]
            best_price = None
            best_key = 10**12
            best_idx = -1
            head_idx = -1
            srcs = []
            for li, (src, _area, _rooms, _zone, is_prim, tl) in enumerate(items):
                ntl = len(tl)
                p = lpos[li]
                while p + 1 < ntl and tl[p + 1][0] <= t:
                    p += 1
                lpos[li] = p
                if tl[0][0] > t:
                    continue
                pr = tl[p][1]
                if pr is _REMOVED:
                    continue
                srcs.append(src)
                pk = pr or 10**12
                if best_idx < 0 or pk < best_key:
                    best_idx = li
                    best_key = pk
                    best_price = pr
                if is_prim and head_idx < 0:
                    head_idx = li

            if best_idx < 0:
                new_state = None
            else:
                if head_idx < 0:
                    head_idx = best_idx
                _, area, rooms, zone, _, _ = items[head_idx]
                ppm = best_price / area if best_price and area else None
                new_state = (best_price, ppm, area, rooms, zone, frozenset(srcs))

            old_state = cur_state[oid_idx]
            if new_state == old_state:
                continue
            if old_state is not None:
                city_multi, a_multi = _apply_bucket_delta(
                    old_state, -1, b_all, b_city, b_z, b_city_r, b_zA_r, city_src, a_src, city_multi, a_multi
                )
            if new_state is not None:
                city_multi, a_multi = _apply_bucket_delta(
                    new_state, 1, b_all, b_city, b_z, b_city_r, b_zA_r, city_src, a_src, city_multi, a_multi
                )
            cur_state[oid_idx] = new_state

        if b_all.count:
            ev_all = {"new": 0, "removed": 0, "returned": 0, "price_down": 0, "price_up": 0}
            ev_a = {"new": 0, "removed": 0, "returned": 0, "price_down": 0, "price_up": 0}
            while ev_idx < n_ev and ev_rows[ev_idx][0] <= t:
                _, kind, old_v, new_v, ez = ev_rows[ev_idx]
                if kind == "price":
                    kname = "price_down" if (new_v or 0) < (old_v or 0) else "price_up"
                    ev_all[kname] += 1
                    if ez == "A":
                        ev_a[kname] += 1
                elif kind in ev_all:
                    ev_all[kind] += 1
                    if ez == "A":
                        ev_a[kind] += 1
                ev_idx += 1
            data = {
                "city": b_city.dump(),
                "all": b_all.dump(),
                "zones": {z: b_z[z].dump() for z in ZONES},
                "rooms": {str(k): b_city_r[k].dump() for k in ROOMS},
                "sources": dict(city_src),
                "multi": city_multi,
                "a": {
                    "all": b_z["A"].dump(),
                    "rooms": {str(k): b_zA_r[k].dump() for k in ROOMS},
                    "sources": dict(a_src),
                    "multi": a_multi,
                },
                "events": ev_all,
                "events_a": ev_a,
            }
            out_rows.append((t, run_id, json.dumps(data, ensure_ascii=False)))
    if out_rows:
        con.executemany("INSERT INTO snapshots(ts, run_id, data) VALUES(?,?,?)", out_rows)
    con.execute("DELETE FROM meta WHERE key='snapshots_dirty'")
    con.commit()


LAUNCH_GAP = 120  # с: прогоны одного запуска стартуют вместе, медленный сайт лишь заканчивает позже


def _launch_points(con):
    """Моменты графика: по одной точке на запуск. Снимки, снятые после разных сайтов одного запуска,
    сводятся в последний из них — к этому моменту отработали все сайты и рынок виден целиком.
    Запуск узнаём по явному launch_key или по времени старта прогонов."""
    rows = con.execute("""SELECT s.ts, s.run_id, r.started, r.launch_key FROM snapshots s
                          LEFT JOIN runs r ON r.id = s.run_id ORDER BY s.ts""").fetchall()
    out, key, cur_lk = [], None, None
    for r in rows:
        lk = r["launch_key"]
        if lk:
            if out and cur_lk == lk:
                out[-1] = (r["ts"], r["run_id"])
            else:
                out.append((r["ts"], r["run_id"]))
                cur_lk = lk
                key = None
            continue
        cur_lk = None
        cur = r["started"] or r["ts"]
        if key is not None and 0 <= (datetime.fromisoformat(cur) - datetime.fromisoformat(key)).total_seconds() <= LAUNCH_GAP:
            out[-1] = (r["ts"], r["run_id"])  # тот же запуск: точка переезжает на последний снимок
        else:
            out.append((r["ts"], r["run_id"]))
            key = cur
    return out


def merge_launches(con):
    """Сводит снимки одного запуска в один и пересобирает их по базе. Нужно один раз — для истории,
    снятой до того, как снимок стал одним на запуск. Возвращает, сколько точек убрано."""
    points = _launch_points(con)
    before = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    if len(points) >= before:
        return 0
    con.execute("DELETE FROM snapshots")
    _write_snapshots(con, points)
    return before - con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]


def mark_snapshots_dirty(con):
    """Откладывает пересчёт снимков до обращения к дашборду, новому снимку или бэкапу."""
    con.execute("INSERT OR REPLACE INTO meta VALUES('snapshots_dirty', '1')")
    con.commit()
    return con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]


def ensure_snapshots_fresh(con):
    """Синхронизирует отложенные снимки, если они помечены устаревшими."""
    if con.execute("SELECT 1 FROM meta WHERE key='snapshots_dirty'").fetchone():
        rebuild_snapshots(con)


def rebuild_snapshots(con):
    """Пересобирает всю историю снимков на те же моменты времени: после перемещения круга (зоны пересчитаны),
    очистки данных за период или для старых снимков без среза «внутри круга». Возвращает число снимков."""
    points = [(r["ts"], r["run_id"]) for r in con.execute("SELECT ts, run_id FROM snapshots ORDER BY ts")]
    con.execute("DELETE FROM snapshots")
    _write_snapshots(con, points)
    return con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]


def recheck_removed(scrapers, cfg, log=lambda m: None):
    """Повторная проверка «снятых» по их страницам на сайтах; ложно снятые возвращаются."""
    from .net import Blocked, IpBanned, pause
    from .scrapers.base import Scraper

    con = db.connect()
    back = confirmed = 0
    for name, cls in scrapers.items():
        if cls.verify is Scraper.verify:  # без проверки по странице (Авито)
            continue
        sc = cls(cfg, log, None, lambda **k: None)
        rows = [dict(r) for r in con.execute("SELECT * FROM listings WHERE source=? AND active=0", (name,))]
        for r in rows:
            try:
                state, reason, price = sc.verify(r)
            except (Blocked, IpBanned) as e:
                log(f"{sc.label}: перепроверка прервана — сайт ограничил доступ ({e})")
                break
            if state == "active":
                con.execute("UPDATE listings SET active=1, missed=0, removed_at=NULL, removed_reason=NULL WHERE source=? AND source_id=?",
                            (r["source"], r["source_id"]))
                db.event(con, r["source"], r["source_id"], "returned", new=price or r["price"],
                         note="перепроверка: объявление активно на сайте — было ошибочно помечено снятым", baseline=1)
                if price:
                    db.set_price(con, r["source"], r["source_id"], price, "цена со страницы объявления")
                back += 1
            elif state == "removed":
                con.execute("UPDATE listings SET removed_reason=?, removed_at=COALESCE(removed_at, ?) WHERE source=? AND source_id=?",
                            (reason, db.now(), r["source"], r["source_id"]))
                confirmed += 1
            con.commit()
            pause((0.8, 1.8) if name != "avito" else sc.delay)
        sc.close()
    con.close()
    log(f"перепроверка снятых: возвращено {back}, снятие подтверждено {confirmed}")
    return {"returned": back, "confirmed": confirmed}


def last_update(con):
    """Последнее обновление: последний законченный сбор каждого сайта."""
    return [dict(r) for r in con.execute(
        """SELECT id, source, status, started, finished FROM runs WHERE id IN (
               SELECT MAX(id) FROM runs WHERE finished IS NOT NULL AND status != 'running' GROUP BY source)
           ORDER BY source""")]


def changes(con, days=7, include_baseline=False, hours=None, last=False):
    """События рынка за период: последние `days` дней, последние `hours` часов или (last=True)
    найденные последним законченным сбором каждого сайта."""
    q = """SELECT e.*, l.object_id, l.title, l.url, l.address, l.zone, l.rooms, l.area, l.floor, l.floors, l.price AS price_now,
                  l.active, l.removed_reason, l.excluded, l.photo
           FROM events e JOIN listings l ON l.source=e.source AND l.source_id=e.source_id WHERE """
    if last:
        ids = [r["id"] for r in last_update(con)]
        if not ids:
            return []
        q += f"e.run_id IN ({','.join('?' * len(ids))})"
        args = ids
    else:
        q += "e.ts >= replace(datetime('now', 'localtime', ?), ' ', 'T')"
        args = [f"-{float(hours)} hours" if hours else f"-{int(days)} days"]
    q += ("" if include_baseline else " AND e.baseline=0") + " ORDER BY e.ts DESC"
    return [dict(r) for r in con.execute(q, args)]


# Средний срок экспозиции вторичной квартиры в РФ в 2025 г.: ~100 дней по крупным регионам (Домклик),
# 110 дней по миллионникам, 83–137 по городам (Яндекс Недвижимость, I кв. 2025). Курортный рынок медленнее —
# берём 120 дней как стартовую оценку, пока своя статистика не накопилась.
PRIOR_LIFETIME_DAYS = 120
PRIOR_WEIGHT = 10        # «вес» стартовой оценки в подтверждённых снятиях
LIFETIME_RANGE = (45, 365)


def lifetime_days(con, source):
    """Средний срок жизни объявления на сайте, дней. Оценка для экспоненциального срока жизни с учётом
    ещё не снятых: (дни наблюдения всех объявлений) / (подтверждённые снятия), сглаженная к PRIOR_LIFETIME_DAYS.
    Возвращает (τ, снятий в статистике)."""
    now = db.now()
    r = con.execute(
        """SELECT SUM(active=0 AND removed_at IS NOT NULL) n,
                  SUM(MAX(0, julianday(CASE WHEN active=0 AND removed_at IS NOT NULL THEN removed_at ELSE ? END)
                             - julianday(first_seen))) days
           FROM listings WHERE source=? AND COALESCE(imported_only, 0)=0""", (now, source)).fetchone()
    n, days = r["n"] or 0, r["days"] or 0.0
    tau = (days + PRIOR_LIFETIME_DAYS * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT)
    return min(max(tau, LIFETIME_RANGE[0]), LIFETIME_RANGE[1]), n


def expected_gone(gaps_days, tau):
    """Сколько объявлений ожидаемо снимут за прошедшее у каждого время: Σ (1 − e^(−t/τ))."""
    return sum(1 - math.exp(-max(g, 0) / tau) for g in gaps_days)


MAX_POINTS = 500  # точек на графиках дашборда за период


def _thin(snaps, limit=MAX_POINTS):
    """Не больше limit точек: соседние проверки объединяются в одну (последнюю в группе),
    события группы суммируются — график «Движение рынка» ничего не теряет."""
    if len(snaps) <= limit:
        return snaps
    step = math.ceil(len(snaps) / limit)
    out = []
    for i in range(0, len(snaps), step):
        group = snaps[i:i + step]
        last = dict(group[-1])
        for key in ("events", "events_a"):
            if any(key in s for s in group):
                total = defaultdict(int)
                for s in group:
                    for k, v in (s.get(key) or {}).items():
                        total[k] += v or 0
                last[key] = dict(total)
        out.append(last)
    return out


def dashboard(con, days=None):
    """Сводка для дашборда; days — период графиков (None — всё время), не больше MAX_POINTS точек."""
    ensure_snapshots_fresh(con)
    q, args = "SELECT ts, data FROM snapshots", ()
    if days:
        q, args = q + " WHERE ts >= ?", (_ago(con, days),)
    raw = [{"ts": r["ts"], **json.loads(r["data"])} for r in con.execute(q + " ORDER BY ts", args)]
    snaps = _thin(raw)
    runs = {}
    for r in con.execute("SELECT * FROM runs ORDER BY id"):
        runs[r["source"]] = dict(r)
    per_source = {r["source"]: dict(r) for r in con.execute(
        """SELECT source, COUNT(*) total, SUM(active) active, SUM(active=1 AND excluded IS NULL) passing,
                  SUM(active=0) removed, MIN(first_seen) since,
                  SUM(zone='A' AND active=1) active_a, SUM(zone='A' AND active=1 AND excluded IS NULL) passing_a,
                  SUM(zone='A' AND active=0) removed_a
           FROM listings WHERE COALESCE(imported_only, 0)=0 GROUP BY source""")}
    d1, d7 = _ago(con, 1), _ago(con, 7)
    # две последние проверки целиком — для показателей «изменение к предыдущей проверке» и среза «сейчас»
    recent = [{"ts": r["ts"], **json.loads(r["data"])} for r in con.execute("SELECT ts, data FROM snapshots ORDER BY ts DESC LIMIT 2")][::-1]
    return {"snapshots": snaps, "checks": len(raw), "recent": recent, "last_runs": runs, "sources": per_source,
            "events_24h": changes_summary(con, d1), "events_7d": changes_summary(con, d7),
            "events_24h_a": _event_counts(con, d1, "9999", zone="A"), "events_7d_a": _event_counts(con, d7, "9999", zone="A")}


def _ago(con, days):
    return con.execute("SELECT datetime('now','localtime',?)", (f"-{days} days",)).fetchone()[0].replace(" ", "T")
