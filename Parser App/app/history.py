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


def compute(rows, events=None):
    """Сводка по подходящим (не отсеянным) активным объявлениям; «Ялта» — зоны A и B."""
    objs = _objects(rows)
    city = [o for o in objs if o["zone"] in ("A", "B")]
    data = {"city": _stats(city), "all": _stats(objs),
            "zones": {z: _stats([o for o in objs if o["zone"] == z]) for z in ZONES},
            "rooms": {str(k): _stats([o for o in city if o["rooms"] == k]) for k in ROOMS},
            "sources": {s: sum(1 for o in city if s in o["sources"]) for s in ("cian", "avito", "yandex")},
            "multi": sum(1 for o in city if len(o["sources"]) > 1)}
    if events is not None:
        data["events"] = events
    return data


def _event_counts(con, since, until):
    q = "SELECT kind, old_value, new_value FROM events WHERE baseline=0 AND ts>? AND ts<=?"
    c = {"new": 0, "removed": 0, "returned": 0, "price_down": 0, "price_up": 0}
    for r in con.execute(q, (since or "", until)):
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
    data = compute(rows, _event_counts(con, prev["ts"] if prev else None, ts))
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
        rows = [dict(r) for r in con.execute(
            "SELECT source, source_id, object_id, area, rooms, zone, first_seen, removed_at, excluded FROM listings")]
        hist = defaultdict(list)
        for h in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
            hist[(h["source"], h["source_id"])].append((h["ts"], h["price"]))
        prev = None
        for t in ends:
            live = []
            for r in rows:
                if r["excluded"] or r["first_seen"] > t or (r["removed_at"] and r["removed_at"] <= t):
                    continue
                ph = [p for ts, p in hist[(r["source"], r["source_id"])] if ts <= t]
                if ph:
                    live.append(dict(r, price=ph[-1]))
            if live:
                data = compute(live, _event_counts(con, prev, t))
                con.execute("INSERT INTO snapshots(ts, run_id, data) VALUES(?,?,?)", (t, None, json.dumps(data, ensure_ascii=False)))
                prev = t
        con.commit()


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


def changes(con, days=7, include_baseline=False):
    q = """SELECT e.*, l.object_id, l.title, l.url, l.address, l.zone, l.rooms, l.area, l.floor, l.floors, l.price AS price_now,
                  l.active, l.removed_reason, l.excluded, l.photo
           FROM events e JOIN listings l ON l.source=e.source AND l.source_id=e.source_id
           WHERE e.ts >= replace(datetime('now', 'localtime', ?), ' ', 'T')""" +("" if include_baseline else " AND e.baseline=0") + " ORDER BY e.ts DESC"
    return [dict(r) for r in con.execute(q, (f"-{int(days)} days",))]


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


def dashboard(con):
    snaps = [{"ts": r["ts"], **json.loads(r["data"])} for r in con.execute("SELECT ts, data FROM snapshots ORDER BY ts")]
    runs = {}
    for r in con.execute("SELECT * FROM runs ORDER BY id"):
        runs[r["source"]] = dict(r)
    per_source = {r["source"]: dict(r) for r in con.execute(
        """SELECT source, COUNT(*) total, SUM(active) active, SUM(active=1 AND excluded IS NULL) passing,
                  SUM(active=0) removed, MIN(first_seen) since FROM listings GROUP BY source""")}
    return {"snapshots": snaps, "last_runs": runs, "sources": per_source,
            "events_24h": changes_summary(con, _ago(con, 1)),
            "events_7d": changes_summary(con, _ago(con, 7))}


def _ago(con, days):
    return con.execute("SELECT datetime('now','localtime',?)", (f"-{days} days",)).fetchone()[0].replace(" ", "T")
