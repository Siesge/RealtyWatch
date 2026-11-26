"""Очистка базы: полностью, за период или по отдельным прогонам — чтобы убрать из статистики данные
неудачных сборов (сайт или парсер отдал неверную выдачу). Перед очисткой база сохраняется в database/backups."""
import json
from datetime import datetime

from . import db, dedupe, history, images, lan_sync, rules, runlog, settings, transfer


def _bounds(date_from, date_to):
    """Даты «ГГГГ-ММ-ДД» включительно → границы меток времени; пустая дата — без границы."""
    lo = f"{date_from}T00:00:00" if date_from else ""
    hi = f"{date_to}T23:59:59" if date_to else "9999"
    if lo and hi != "9999" and lo > hi:
        raise ValueError("дата «с» позже даты «по»")
    return lo, hi


def _q(ids):
    return ",".join("?" * len(ids))


def _window_counts(con, lo, hi, source=None):
    """Сколько объявлений и записей цен затронет очистка окна (по одному сайту, если задан)."""
    s, a = (" AND source=?", (source,)) if source else ("", ())
    non_imp = (" AND NOT EXISTS (SELECT 1 FROM listings l WHERE l.source=price_history.source "
               "AND l.source_id=price_history.source_id AND COALESCE(l.imported_only, 0)=1)")
    one = lambda q, *p: con.execute(q, p).fetchone()[0]  # noqa: E731
    return {
        "prices": one(f"SELECT COUNT(*) FROM price_history WHERE ts BETWEEN ? AND ?{s}{non_imp}", lo, hi, *a),
        # объявления, которые видели только в это окно, — удаляются целиком (кроме выборочного импорта)
        "listings": one(f"SELECT COUNT(*) FROM listings WHERE first_seen >= ? AND last_seen <= ? AND COALESCE(imported_only, 0)=0{s}", lo, hi, *a),
        # снятые в это окно — возвращаются в продажу и проверятся заново в следующем сборе
        "unremoved": one(f"SELECT COUNT(*) FROM listings WHERE active=0 AND removed_at BETWEEN ? AND ? AND COALESCE(imported_only, 0)=0{s}", lo, hi, *a),
    }


def preview(con, date_from=None, date_to=None):
    """Сколько данных затронет очистка за период (ничего не меняет)."""
    lo, hi = _bounds(date_from, date_to)
    one = lambda q, *a: con.execute(q, a).fetchone()[0]  # noqa: E731
    return {
        "runs": one("SELECT COUNT(*) FROM runs WHERE started BETWEEN ? AND ?", lo, hi),
        "snapshots": one("SELECT COUNT(*) FROM snapshots WHERE ts BETWEEN ? AND ?", lo, hi),
        "events": one("SELECT COUNT(*) FROM events WHERE ts BETWEEN ? AND ?", lo, hi),
        **_window_counts(con, lo, hi),
    }


def _reexclude(con, src, sid, filters):
    """Заново считает отсев объявления. С «уехавшей» ценой неудачного сбора объявление могло выпасть
    из поиска («цена 4 000 000»), и после возврата цены отсев нужно пересчитать — иначе объявление
    останется вне статистики и графиков до следующего сбора или кнопки «Сохранить и пересчитать»."""
    row = con.execute("SELECT * FROM listings WHERE source=? AND source_id=?", (src, sid)).fetchone()
    if row is None:
        return
    item = dict(row)
    item["flags"] = {}
    reasons = rules.evaluate(item, filters)
    con.execute("UPDATE listings SET excluded=?, flags=? WHERE source=? AND source_id=?",
                ("; ".join(reasons) or None, json.dumps(item["flags"], ensure_ascii=False), src, sid))


def _delete_window(con, lo, hi, source=None):
    """Удаляет собранное в окне [lo, hi]: объявления, увиденные только в нём (вместе с фото, историей цен
    и избранным), записи цен окна; снятые в окне возвращает в продажу. Прогоны, события и снимки рынка
    удаляются отдельно: по периоду — в purge(), по номерам прогонов — в purge_runs()."""
    s, a = (" AND source=?", (source,)) if source else ("", ())
    non_imp = (" AND NOT EXISTS (SELECT 1 FROM listings l WHERE l.source=price_history.source "
               "AND l.source_id=price_history.source_id AND COALESCE(l.imported_only, 0)=1)")
    gone = [(r["source"], r["source_id"]) for r in con.execute(
        f"SELECT source, source_id FROM listings WHERE first_seen >= ? AND last_seen <= ? AND COALESCE(imported_only, 0)=0{s}", (lo, hi, *a))]
    for key in gone:
        for table in ("listings", "price_history", "events", "likes", "dislikes", "notes"):
            con.execute(f"DELETE FROM {table} WHERE source=? AND source_id=?", key)
        p = images.local_path(*key)
        if p:
            p.unlink(missing_ok=True)
    con.execute(f"UPDATE listings SET active=1, removed_at=NULL, removed_reason=NULL, missed=0 "
                f"WHERE active=0 AND removed_at BETWEEN ? AND ? AND COALESCE(imported_only, 0)=0{s}", (lo, hi, *a))
    touched = {(r["source"], r["source_id"]) for r in con.execute(
        f"SELECT DISTINCT source, source_id FROM price_history WHERE ts BETWEEN ? AND ?{s}{non_imp}", (lo, hi, *a))}
    con.execute(f"DELETE FROM price_history WHERE ts BETWEEN ? AND ?{s}{non_imp}", (lo, hi, *a))
    # цена объявления после удаления истории за период: видели позже периода — нынешняя цена верна,
    # её запись в истории ставится сразу после периода; не видели — возвращается последняя цена до периода
    filters = settings.load()["filters"]
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
            _reexclude(con, src, sid, filters)


def purge(date_from=None, date_to=None):
    """Удаляет данные, собранные за период. Возвращает итог (как preview) и путь резервной копии."""
    con = db.connect()
    lo, hi = _bounds(date_from, date_to)
    res = preview(con, date_from, date_to)
    con.close()
    res["backup"] = transfer.backup("перед_очисткой")
    con = db.connect()
    _delete_window(con, lo, hi)
    con.execute("DELETE FROM events WHERE ts BETWEEN ? AND ?", (lo, hi))
    con.execute("DELETE FROM snapshots WHERE ts BETWEEN ? AND ?", (lo, hi))
    con.execute("DELETE FROM runs WHERE started BETWEEN ? AND ?", (lo, hi))
    con.commit()
    dedupe.clean_manual_merges(con)
    con.commit()
    res["objects"] = dedupe.rebuild(con)
    res["snapshots_left"] = history.rebuild_snapshots(con)
    con.close()
    return res


# ---------- отдельные прогоны ----------

def _launch_of(con):
    """Номер запуска для каждого прогона: по явному launch_key (включая продолженные прогоны),
    а для записей без launch_key — по времени старта в пределах LAUNCH_GAP без повтора сайта."""
    out, by_lkey, start, seen, idx = {}, {}, None, set(), 0
    for r in con.execute("SELECT id, source, started, launch_key FROM runs WHERE started IS NOT NULL ORDER BY started, id"):
        lk = r["launch_key"]
        if lk:
            if lk not in by_lkey:
                idx += 1
                by_lkey[lk] = idx
                start = None
                seen = set()
            out[r["id"]] = by_lkey[lk]
            continue
        cur = datetime.fromisoformat(r["started"])
        if start is None or (cur - start).total_seconds() > history.LAUNCH_GAP or r["source"] in seen:
            idx += 1
            start = cur
            seen = set()
        seen.add(r["source"])
        out[r["id"]] = idx
    return out


def launches(con, date_from=None, date_to=None, limit=400):
    """Запуски сбора за период, новые сверху: что собрал каждый сайт и чем закончил.
    Один запуск — одна точка на графиках дашборда, поэтому выбирают для удаления именно запуски."""
    lo, hi = _bounds(date_from, date_to)
    rows = [dict(r) for r in con.execute(
        "SELECT id, source, mode, started, finished, status, pages, found, new, changed, removed, message, "
        "declared, collected, resume_state "
        "FROM runs WHERE started BETWEEN ? AND ? ORDER BY started, id", (lo, hi))]
    have_log = runlog.existing_ids()
    idx, out = _launch_of(con), []
    by_key = {}
    for r in rows:
        raw_res = r.pop("resume_state", None)
        r["can_continue"] = bool(raw_res) and r["status"] in ("error", "blocked", "partial", "stopped")
        r["has_log"] = r["id"] in have_log
        k = idx.get(r["id"])
        if k is not None and k in by_key:
            by_key[k]["runs"].append(r)
        else:
            grp = {"key": k, "started": r["started"], "runs": [r]}
            out.append(grp)
            if k is not None:
                by_key[k] = grp
    for g in out:
        g["finished"] = max((x["finished"] or "" for x in g["runs"]), default="")
        g["collected"] = sum(((x["collected"] if x["collected"] is not None else x["found"]) or 0) for x in g["runs"])
        g["bad"] = any(x["status"] != "done" for x in g["runs"])
    out.reverse()
    return out[:limit]


def _run_windows(con, ids):
    """Для каждого прогона — сайт и окно времени, в котором он писал данные. У прерванного прогона
    финиша нет: окно тянется до старта следующего прогона того же сайта (дальше писал уже он)."""
    out = []
    if not ids:
        return out
    for r in con.execute(f"SELECT id, source, started, finished FROM runs WHERE id IN ({_q(ids)}) ORDER BY started, id", ids):
        hi = r["finished"]
        if not hi:
            nxt = con.execute("SELECT MIN(started) FROM runs WHERE source=? AND started>?",
                              (r["source"], r["started"])).fetchone()[0]
            hi = nxt or db.now()
        out.append({"id": r["id"], "source": r["source"], "lo": r["started"], "hi": hi})
    return out


def _snapshot_moves(con, ids):
    """Что станет со снимками рынка удаляемых прогонов. Снимок принадлежит запуску, а не сайту: если
    от запуска остаётся хоть один прогон, точка графика переезжает на него и пересчитывается по базе;
    если удаляют весь запуск, точка исчезает. Возвращает ({старый прогон: новый}, сколько точек исчезнет)."""
    idx, gone = _launch_of(con), set(ids)
    survivors = {}
    for rid, launch in idx.items():
        if rid not in gone:
            survivors.setdefault(launch, rid)
    moves, lost = {}, 0
    for r in con.execute("SELECT run_id, COUNT(*) n FROM snapshots WHERE run_id IS NOT NULL GROUP BY run_id"):
        if r["run_id"] in gone:
            keep = survivors.get(idx.get(r["run_id"]))
            if keep:
                moves[r["run_id"]] = keep
            else:
                lost += r["n"]
    return moves, lost


def runs_preview(con, ids):
    """Сколько данных затронет удаление выбранных прогонов (ничего не меняет)."""
    windows = _run_windows(con, ids)
    ids = [w["id"] for w in windows]
    res = {"runs": len(ids), "prices": 0, "listings": 0, "unremoved": 0,
           "events": con.execute(f"SELECT COUNT(*) FROM events WHERE run_id IN ({_q(ids)})", ids).fetchone()[0] if ids else 0}
    for w in windows:
        c = _window_counts(con, w["lo"], w["hi"], w["source"])
        for k in ("prices", "listings", "unremoved"):
            res[k] += c[k]
    res["snapshots"] = _snapshot_moves(con, ids)[1]
    return res


def purge_runs(ids):
    """Удаляет выбранные прогоны: их события, записи цен и объявления, увиденные только в них.
    Остальные прогоны того же запуска остаются, и точка графика остаётся вместе с ними."""
    con = db.connect()
    windows = _run_windows(con, ids)
    ids = [w["id"] for w in windows]
    if not ids:
        con.close()
        raise ValueError("прогоны не найдены — возможно, их уже удалили")
    res = runs_preview(con, ids)
    moves = _snapshot_moves(con, ids)[0]
    con.close()
    res["backup"] = transfer.backup("перед_удалением_прогонов")
    con = db.connect()
    for w in windows:
        _delete_window(con, w["lo"], w["hi"], w["source"])
    con.execute(f"DELETE FROM events WHERE run_id IN ({_q(ids)})", ids)
    for old, new in moves.items():
        con.execute("UPDATE snapshots SET run_id=? WHERE run_id=?", (new, old))
    con.execute(f"DELETE FROM snapshots WHERE run_id IN ({_q(ids)})", ids)
    con.execute(f"DELETE FROM runs WHERE id IN ({_q(ids)})", ids)
    con.commit()
    dedupe.clean_manual_merges(con)
    con.commit()
    res["objects"] = dedupe.rebuild(con)
    res["snapshots_left"] = history.rebuild_snapshots(con)
    con.close()
    res["logs"] = runlog.drop(ids)  # подробные журналы: открывать их больше нечем
    return res


def clear_all():
    """Полная очистка: объявления, история цен, события, проверки рынка, прогоны, импорты, избранное и фото.
    Настройки поиска, круг и доверенные устройства (sync_peers) остаются."""
    res = {"backup": transfer.backup("перед_полной_очисткой", with_images=True)}
    con = db.connect()
    res["listings"] = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    for table in (
        "listings", "price_history", "events", "snapshots", "runs",
        "likes", "dislikes", "notes", "manual_merges",
        "import_batches", "import_items", "imported_notes", "sync_outbox", "sync_history",
    ):
        con.execute(f"DELETE FROM {table}")
    con.commit()
    con.execute("VACUUM")
    con.close()
    for folder in (images.IMAGES, db.DB_FILE.parent / "import_images", db.DB_FILE.parent / "import_staging"):
        if folder.exists():
            for p in folder.rglob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
    lan_sync.reset_runtime_cache()
    return res
