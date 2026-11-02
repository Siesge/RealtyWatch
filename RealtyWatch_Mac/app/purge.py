"""Очистка базы: полностью или за период — чтобы убрать из статистики данные неудачных сборов
(сайт или парсер отдал неверную выдачу). Перед очисткой база сохраняется в database/backups."""
from . import db, dedupe, history, images, transfer


def _bounds(date_from, date_to):
    """Даты «ГГГГ-ММ-ДД» включительно → границы меток времени; пустая дата — без границы."""
    lo = f"{date_from}T00:00:00" if date_from else ""
    hi = f"{date_to}T23:59:59" if date_to else "9999"
    if lo and hi != "9999" and lo > hi:
        raise ValueError("дата «с» позже даты «по»")
    return lo, hi


def preview(con, date_from=None, date_to=None):
    """Сколько данных затронет очистка за период (ничего не меняет)."""
    lo, hi = _bounds(date_from, date_to)
    one = lambda q, *a: con.execute(q, a).fetchone()[0]  # noqa: E731
    return {
        "runs": one("SELECT COUNT(*) FROM runs WHERE started BETWEEN ? AND ?", lo, hi),
        "snapshots": one("SELECT COUNT(*) FROM snapshots WHERE ts BETWEEN ? AND ?", lo, hi),
        "events": one("SELECT COUNT(*) FROM events WHERE ts BETWEEN ? AND ?", lo, hi),
        "prices": one("SELECT COUNT(*) FROM price_history WHERE ts BETWEEN ? AND ?", lo, hi),
        # объявления, которые видели только в этот период, — удаляются целиком
        "listings": one("SELECT COUNT(*) FROM listings WHERE first_seen >= ? AND last_seen <= ?", lo, hi),
        # снятые в этот период — возвращаются в продажу и проверятся заново в следующем сборе
        "unremoved": one("SELECT COUNT(*) FROM listings WHERE active=0 AND removed_at BETWEEN ? AND ?", lo, hi),
    }


def purge(date_from=None, date_to=None):
    """Удаляет данные, собранные за период. Возвращает итог (как preview) и путь резервной копии."""
    con = db.connect()
    lo, hi = _bounds(date_from, date_to)
    res = preview(con, date_from, date_to)
    con.close()
    res["backup"] = transfer.backup("перед_очисткой")
    con = db.connect()
    gone = [(r["source"], r["source_id"]) for r in con.execute(
        "SELECT source, source_id FROM listings WHERE first_seen >= ? AND last_seen <= ?", (lo, hi))]
    for key in gone:
        for table in ("listings", "price_history", "events", "likes"):
            con.execute(f"DELETE FROM {table} WHERE source=? AND source_id=?", key)
        p = images.local_path(*key)
        if p:
            p.unlink(missing_ok=True)
    con.execute("UPDATE listings SET active=1, removed_at=NULL, removed_reason=NULL, missed=0 "
                "WHERE active=0 AND removed_at BETWEEN ? AND ?", (lo, hi))
    touched = {(r["source"], r["source_id"]) for r in con.execute(
        "SELECT DISTINCT source, source_id FROM price_history WHERE ts BETWEEN ? AND ?", (lo, hi))}
    con.execute("DELETE FROM price_history WHERE ts BETWEEN ? AND ?", (lo, hi))
    # цена объявления после удаления истории за период: видели позже периода — нынешняя цена верна,
    # её запись в истории ставится сразу после периода; не видели — возвращается последняя цена до периода
    for src, sid in touched:
        row = con.execute("SELECT price, area, last_seen FROM listings WHERE source=? AND source_id=?", (src, sid)).fetchone()
        if row is None:
            continue
        hist = con.execute("SELECT ts, price FROM price_history WHERE source=? AND source_id=? ORDER BY ts", (src, sid)).fetchall()
        if row["last_seen"] > hi:
            if not hist or hist[-1]["price"] != row["price"]:
                after = hi[:19] if hi != "9999" else row["last_seen"]
                con.execute("INSERT INTO price_history VALUES(?,?,?,?)", (src, sid, after, row["price"]))
        elif hist:
            price = hist[-1]["price"]
            con.execute("UPDATE listings SET price=?, price_m2=? WHERE source=? AND source_id=?",
                        (price, round(price / row["area"]) if price and row["area"] else None, src, sid))
    con.execute("DELETE FROM events WHERE ts BETWEEN ? AND ?", (lo, hi))
    con.execute("DELETE FROM snapshots WHERE ts BETWEEN ? AND ?", (lo, hi))
    con.execute("DELETE FROM runs WHERE started BETWEEN ? AND ?", (lo, hi))
    con.commit()
    res["objects"] = dedupe.rebuild(con)
    res["snapshots_left"] = history.rebuild_snapshots(con)
    con.close()
    return res


def clear_all():
    """Полная очистка: объявления, история цен, события, проверки рынка, прогоны, избранное и фото.
    Настройки поиска и круг остаются."""
    res = {"backup": transfer.backup("перед_полной_очисткой", with_images=True)}
    con = db.connect()
    res["listings"] = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    for table in ("listings", "price_history", "events", "snapshots", "runs", "likes"):
        con.execute(f"DELETE FROM {table}")
    con.commit()
    con.execute("VACUUM")
    con.close()
    for p in images.IMAGES.rglob("*"):
        if p.is_file():
            p.unlink(missing_ok=True)
    return res
