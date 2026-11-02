"""История рынка: снимки после каждого прогона (для графиков дашборда), события (для экрана «Изменения»),
восстановление истории по уже собранным данным и повторная проверка «снятых» объявлений."""
import json
import math
import statistics
from collections import defaultdict

from . import db

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
        out.append({"price": best["price"], "area": best["area"], "rooms": best["rooms"], "zone": best["zone"],
                    "ppm": best["price"] / best["area"] if best["price"] and best["area"] else None,
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
    ts = db.now()
    rows = [dict(r) for r in con.execute(
        "SELECT source, source_id, object_id, price, area, rooms, zone FROM listings WHERE active=1 AND excluded IS NULL")]
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


def _write_snapshots(con, points):
    """Восстанавливает снимки рынка на моменты points [(ts, run_id)] по данным базы: какие объявления были
    в продаже на тот момент (появились раньше, сняты позже) и по какой цене (история цен).
    Зоны, отсев и объединение дублей — текущие, поэтому история согласована с нынешними правилами и кругом."""
    rows = [dict(r) for r in con.execute(
        "SELECT source, source_id, object_id, area, rooms, zone, first_seen, removed_at, excluded FROM listings")]
    hist = defaultdict(list)
    for h in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
        hist[(h["source"], h["source_id"])].append((h["ts"], h["price"]))
    rows = [r for r in rows if not r["excluded"]]
    keys = [(r["source"], r["source_id"]) for r in rows]
    pos = [0] * len(rows)   # точки идут по времени: указатель в истории цен каждого объявления только растёт
    prev = None
    for t, run_id in sorted(points):
        live = []
        for i, r in enumerate(rows):
            h = hist[keys[i]]
            while pos[i] < len(h) and h[pos[i]][0] <= t:
                pos[i] += 1
            if not pos[i] or r["first_seen"] > t or (r["removed_at"] and r["removed_at"] <= t):
                continue
            live.append(dict(r, price=h[pos[i] - 1][1]))
        if live:
            data = compute(live, _event_counts(con, prev, t), _event_counts(con, prev, t, zone="A"))
            con.execute("INSERT INTO snapshots(ts, run_id, data) VALUES(?,?,?)", (t, run_id, json.dumps(data, ensure_ascii=False)))
            prev = t
    con.commit()


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
           FROM listings WHERE source=?""", (now, source)).fetchone()
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
                  SUM(zone='A' AND active=0) removed_a FROM listings GROUP BY source""")}
    d1, d7 = _ago(con, 1), _ago(con, 7)
    # две последние проверки целиком — для показателей «изменение к предыдущей проверке» и среза «сейчас»
    recent = [{"ts": r["ts"], **json.loads(r["data"])} for r in con.execute("SELECT ts, data FROM snapshots ORDER BY ts DESC LIMIT 2")][::-1]
    return {"snapshots": snaps, "checks": len(raw), "recent": recent, "last_runs": runs, "sources": per_source,
            "events_24h": changes_summary(con, d1), "events_7d": changes_summary(con, d7),
            "events_24h_a": _event_counts(con, d1, "9999", zone="A"), "events_7d_a": _event_counts(con, d7, "9999", zone="A")}


def _ago(con, days):
    return con.execute("SELECT datetime('now','localtime',?)", (f"-{days} days",)).fetchone()[0].replace(" ", "T")
