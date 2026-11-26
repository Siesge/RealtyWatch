"""Выборочный обмен карточками (.rwcards.zip): экспорт, предпросмотр, слой «Импорт»,
проверка актуальности объявлений, дедупликация повторов и многопользовательские заметки."""
import json
import re
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import about, db, dedupe, geo, history, images, rules, settings

FORMAT = "realtywatch-cards"
VERSION = 1
EXT = ".rwcards.zip"
SOURCES = ("cian", "avito", "yandex")
SOURCE_LABEL = {"cian": "ЦИАН", "avito": "Авито", "yandex": "Яндекс"}
LIKES_MODES = ("all", "preserve", "none")

# Пороги низкого покрытия выдачи — согласованы с web/app.js (runLowCoverage),
# чтобы сбойный/обрезанный прогон никогда не помечал отсутствующие квартиры как stale.
_MIN_COVERAGE = {"cian": 0.85, "yandex": 0.95}
_VALID_IMG_EXTS = ("webp", "jpg", "jpeg", "png", "avif")
_RENO_RANK = {"design": 6, "euro": 5, "cosmetic": 4, "finish": 3, "old": 2, "any": 1}

# Может подменяться в тестах; по умолчанию вычисляется динамически от db.DB_FILE
IMPORT_IMAGES = None


def import_images_dir():
    """Папка локальных превью-фото для неактуальных (stale) карточек импорта."""
    return Path(IMPORT_IMAGES) if IMPORT_IMAGES else (db.DB_FILE.parent / "import_images")


def import_staging_dir():
    """Временная папка для .rwcards.zip, загруженных через браузерный интерфейс."""
    return db.DB_FILE.parent / "import_staging"


def stage_uploaded_bundle(data: bytes, filename: str = ""):
    """Сохраняет загруженный из браузера .rwcards.zip и сразу формирует предпросмотр."""
    if not data:
        raise ValueError("выбранный файл пуст")
    if len(data) > 250 * 1024 * 1024:
        raise ValueError("файл слишком большой (больше 250 МБ)")
    raw_name = Path(filename or "import.rwcards.zip").name
    safe = re.sub(r'[\\/:*?"<>|]+', "_", raw_name).strip(" .") or "import.rwcards.zip"
    if not safe.lower().endswith(".zip"):
        safe += EXT
    sdir = import_staging_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    target = sdir / safe
    if target.exists():
        target = sdir / f"{uuid.uuid4().hex[:6]}_{safe}"
    target.write_bytes(data)
    try:
        prev = preview_cards_zip(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"path": str(target), "preview": prev}


def _safe_sid(source_id):
    return re.sub(r'[\\/:*?"<>|]+', "_", str(source_id))


def import_image_path(source, source_id):
    """Путь к локальному фото: сначала основная база images, затем архив import_images."""
    if source not in SOURCES:
        return None
    p = images.local_path(source, str(source_id))
    if p is not None:
        return p
    folder = import_images_dir() / source
    if not folder.exists():
        return None
    sid = _safe_sid(source_id)
    for ext in _VALID_IMG_EXTS:
        cand = folder / f"{sid}.{ext}"
        if cand.is_file():
            return cand
    return None


def default_name(kind="cards"):
    """Имя файла .rwcards.zip по умолчанию для сохранения из диалога или папки exports."""
    tag = "избранное" if kind == "favorites" else "карточки"
    return f"RealtyWatch_{tag}_{datetime.now().strftime('%Y-%m-%d_%H%M')}{EXT}"


def _own_con(con):
    if con is not None:
        return con, False
    return db.connect(), True


def _parse_json(raw_val, default):
    if raw_val is None:
        return default
    if isinstance(raw_val, (dict, list)):
        return raw_val
    try:
        val = json.loads(raw_val)
        return val if isinstance(val, type(default)) else default
    except (TypeError, ValueError):
        return default


def _db_freshness(con):
    """Свежесть локальной базы отправителя по каждому сайту."""
    out = {}
    for s in SOURCES:
        r = con.execute(
            "SELECT MAX(finished) FROM runs WHERE source=? AND status IN ('done','partial') AND finished IS NOT NULL",
            (s,),
        ).fetchone()
        ts = r[0] if r and r[0] else None
        if not ts:
            r2 = con.execute("SELECT MAX(last_seen) FROM listings WHERE source=?", (s,)).fetchone()
            ts = r2[0] if r2 and r2[0] else None
        out[s] = ts
    return out


def _clean_listing_for_export(row, liked_set, img_path):
    """Готовит словарь объявления для cards.json без тяжёлого raw-дампa."""
    src = row["source"]
    sid = str(row["source_id"])
    raw_obj = _parse_json(row.get("raw"), {})
    flags = _parse_json(row.get("flags"), {})
    clean = {}
    for k, v in row.items():
        if k == "raw":
            continue
        clean[k] = v
    clean["source_id"] = sid
    clean["flags"] = flags
    clean["apartments"] = bool(raw_obj.get("apartments") or raw_obj.get("isApartments"))
    clean["liked"] = (src, sid) in liked_set
    if img_path is not None and img_path.is_file():
        clean["image_arc"] = f"images/{src}/{_safe_sid(sid)}{img_path.suffix.lower()}"
    else:
        clean["image_arc"] = None
    return clean


def _build_card_summary(items, flags, manual_merged, apartments):
    """Компактная карточная сводка для snapshot_json и автономного рендера."""
    p = items[0]
    live = [i for i in items if i.get("active", 1)]
    prices = [i["price"] for i in (live or items) if i.get("price") is not None]
    price = min(prices) if prices else None
    area = p.get("area")
    reno = sorted((i.get("renovation") for i in items if i.get("renovation")), key=lambda x: -_RENO_RANK.get(x, 0))
    addr = (
        (p.get("address") or next((i.get("address") for i in items if i.get("address")), ""))
        if manual_merged
        else (
            sorted((i.get("address") for i in items if i.get("address")), key=len, reverse=True)[0]
            if any(i.get("address") for i in items)
            else ""
        )
    )
    first_seen = min((i.get("first_seen") or "" for i in items), default="")
    last_seen = max((i.get("last_seen") or "" for i in items), default="")
    return {
        "title": p.get("title") or "",
        "address": addr or "",
        "zone": p.get("zone") or "B",
        "rooms": p.get("rooms"),
        "area": area,
        "floor": p.get("floor"),
        "floors": p.get("floors"),
        "price": price,
        "price_max": max(prices) if prices else None,
        "price_m2": round(price / area) if price and area else p.get("price_m2"),
        "renovation": (p.get("renovation") or (reno[0] if reno else None)) if manual_merged
        else (p.get("renovation") if (p.get("excluded") or not reno) else reno[0]),
        "apartments": apartments,
        "is_new_building": any(i.get("is_new_building") for i in items),
        "seller_types": sorted({i["seller_type"] for i in items if i.get("seller_type")}),
        "flags": flags,
        "sources": sorted({i["source"] for i in items}),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "active": any(i.get("active", 1) for i in items),
        "excluded": p.get("excluded") if all(i.get("excluded") for i in items) else None,
    }


def export_cards_zip(dest, object_ids=None, liked_only=False, kind="cards", con=None):
    """Упаковывает выбранные карточки или избранное в переносимый архив .rwcards.zip."""
    con, own = _own_con(con)
    try:
        ident = settings.ensure_device_identity(con)
        rows = [dict(r) for r in con.execute("SELECT * FROM listings")]
        for r in rows:
            r["source_id"] = str(r["source_id"])
            r["object_id"] = r["object_id"] or f"{r['source']}:{r['source_id']}"

        liked = {(r["source"], str(r["source_id"])) for r in con.execute("SELECT source, source_id FROM likes")}
        if object_ids is not None:
            want = {str(x) for x in object_ids if x}
            if not want:
                raise ValueError("не выбрано ни одной карточки для экспорта")
            target_oids = want
        elif liked_only or kind == "favorites":
            target_oids = {r["object_id"] for r in rows if (r["source"], r["source_id"]) in liked}
            if not target_oids:
                raise ValueError("в избранном пусто")
        else:
            target_oids = {r["object_id"] for r in rows}

        sel_rows = [r for r in rows if r["object_id"] in target_oids]
        if not sel_rows:
            raise ValueError("нет карточек для экспорта")

        sel_keys = {(r["source"], r["source_id"]) for r in sel_rows}
        hist = defaultdict(list)
        for r in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
            k = (r["source"], str(r["source_id"]))
            if k in sel_keys:
                hist[k].append([r["ts"], r["price"]])

        notes = {
            (r["source"], str(r["source_id"])): (r["text"] or "", r["ts"] or "")
            for r in con.execute("SELECT source, source_id, text, ts FROM notes")
            if (r["source"], str(r["source_id"])) in sel_keys
        }
        imp_notes = defaultdict(dict)
        for r in con.execute(
            "SELECT source, source_id, origin_id, origin_name, text, ts, updated_at FROM imported_notes "
            "WHERE COALESCE(text, '') != ''"
        ):
            k = (r["source"], str(r["source_id"]))
            if k in sel_keys:
                prev = imp_notes[k].get(r["origin_id"])
                ts_cur = r["updated_at"] or r["ts"] or ""
                if prev is None or ts_cur > (prev.get("updated_at") or prev.get("ts") or ""):
                    imp_notes[k][r["origin_id"]] = {
                        "origin_id": r["origin_id"],
                        "origin_name": r["origin_name"] or "",
                        "text": r["text"] or "",
                        "ts": r["ts"] or "",
                        "updated_at": r["updated_at"] or r["ts"] or "",
                    }

        by_group, primaries = dedupe.active_manual_merges(con)
        merged_keys = {k for ks in by_group.values() for k in ks}
        mm_rows = defaultdict(list)
        for r in con.execute("SELECT source, source_id, group_id, is_primary, ts FROM manual_merges"):
            k = (r["source"], str(r["source_id"]))
            if k in sel_keys:
                mm_rows[r["group_id"]].append(
                    {
                        "source": r["source"],
                        "source_id": str(r["source_id"]),
                        "group_id": r["group_id"],
                        "is_primary": int(r["is_primary"] or 0),
                        "ts": r["ts"] or "",
                    }
                )

        have_img = {}
        for r in sel_rows:
            k = (r["source"], r["source_id"])
            have_img[k] = import_image_path(*k)

        groups = defaultdict(list)
        for r in sel_rows:
            groups[r["object_id"]].append(r)

        cards = []
        img_entries = {}
        total_listings = 0
        for oid, items in groups.items():
            img_keys = {k for k in ((i["source"], i["source_id"]) for i in items) if have_img.get(k) is not None}
            items.sort(
                key=lambda i: (
                    (i["source"], i["source_id"]) not in primaries,
                    *dedupe._rank_key(i, img_keys),
                )
            )
            p = items[0]
            manual_merged = any((i["source"], i["source_id"]) in merged_keys for i in items)
            card_gids = {
                r["group_id"]
                for i in items
                for gid, ks in by_group.items()
                if (i["source"], i["source_id"]) in ks
                for r in mm_rows.get(gid, ())
            }
            card_mm = [row for gid in sorted(card_gids) for row in mm_rows.get(gid, ())]
            # Берём заметку с главного объявления при ручной склейке либо самую свежую по объекту
            p_note, p_nts = notes.get((p["source"], p["source_id"]), ("", ""))
            if manual_merged and p_note.strip():
                note_text, note_ts = p_note.strip(), p_nts
            else:
                best_nts, best_nt = max(
                    ((notes.get((i["source"], i["source_id"]), ("", ""))[1],
                      notes.get((i["source"], i["source_id"]), ("", ""))[0])
                     for i in items if notes.get((i["source"], i["source_id"]), ("", ""))[0].strip()),
                    default=("", ""),
                )
                note_text, note_ts = best_nt.strip(), best_nts

            chain_map = {}
            for i in items:
                k = (i["source"], i["source_id"])
                for origin, nobj in imp_notes.get(k, {}).items():
                    prev = chain_map.get(origin)
                    cur_ts = nobj.get("updated_at") or nobj.get("ts") or ""
                    if prev is None or cur_ts > (prev.get("updated_at") or prev.get("ts") or ""):
                        chain_map[origin] = nobj

            listing_rows = []
            ph_rows = []
            flags = {}
            apartments = False
            for i in items:
                k = (i["source"], i["source_id"])
                img_p = have_img.get(k)
                cleaned = _clean_listing_for_export(i, liked, img_p)
                flags.update(cleaned.get("flags") or {})
                apartments = apartments or bool(cleaned.get("apartments"))
                listing_rows.append(cleaned)
                if cleaned.get("image_arc") and img_p is not None:
                    img_entries[cleaned["image_arc"]] = img_p
                ph_rows.extend([[k[0], k[1], ts_p, pr_p] for ts_p, pr_p in hist.get(k, [])])
                total_listings += 1

            summary = _build_card_summary(listing_rows, flags, manual_merged, apartments)
            cards.append(
                {
                    "export_card_id": oid,
                    "primary_key": f"{p['source']}:{p['source_id']}",
                    "member_keys": [f"{i['source']}:{i['source_id']}" for i in listing_rows],
                    "liked": any(i["liked"] for i in listing_rows),
                    "manual_merged": manual_merged,
                    "manual_group_id": sorted(card_gids)[0] if card_gids else None,
                    "manual_merges_rows": card_mm,
                    "note": note_text,
                    "note_ts": note_ts,
                    "imported_notes_chain": list(chain_map.values()),
                    "listing_rows": listing_rows,
                    "price_history_rows": ph_rows,
                    "card_summary": summary,
                }
            )

        cards.sort(key=lambda c: c["card_summary"].get("first_seen") or "", reverse=True)
        bundle_id = str(uuid.uuid4())
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "bundle_id": bundle_id,
            "exported_at": db.now(),
            "kind": kind,
            "app_version": about._app_version(),
            "sender": ident,
            "db_freshness": _db_freshness(con),
            "counts": {
                "cards": len(cards),
                "listings": total_listings,
                "images": len(img_entries),
            },
        }

        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest_path, "w") as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2), zipfile.ZIP_DEFLATED)
            z.writestr("cards.json", json.dumps({"cards": cards}, ensure_ascii=False), zipfile.ZIP_DEFLATED)
            for arc, fpath in sorted(img_entries.items()):
                z.write(fpath, arc, compress_type=zipfile.ZIP_STORED)

        return {
            "path": str(dest_path),
            "bundle_id": bundle_id,
            "cards": len(cards),
            "listings": total_listings,
            "images": len(img_entries),
            "sender": ident,
        }
    finally:
        if own:
            con.close()


def _load_bundle_zip(z):
    """Проверяет структуру .rwcards.zip и возвращает (manifest, cards)."""
    names = set(z.namelist())
    if "manifest.json" not in names or "cards.json" not in names:
        raise ValueError("неверный пакет карточек: внутри нет manifest.json или cards.json")
    try:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        cards_doc = json.loads(z.read("cards.json").decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"повреждён JSON в пакете карточек: {e}") from e
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("выбранный файл не является пакетом карточек RealtyWatch (.rwcards.zip)")
    ver = manifest.get("version")
    if ver != VERSION:
        raise ValueError(f"неподдерживаемая версия формата карточек: {ver}")
    cards = cards_doc.get("cards") if isinstance(cards_doc, dict) else None
    if not isinstance(cards, list):
        raise ValueError("повреждён cards.json: отсутствует список карточек")
    return manifest, cards


def _healthy_done_runs(con):
    """Время последнего успешного полного прогона получателя по каждому сайту."""
    out = {}
    for r in con.execute(
        "SELECT source, finished, declared, collected FROM runs WHERE status='done' AND finished IS NOT NULL"
    ):
        src = r["source"]
        if src not in SOURCES:
            continue
        dec, col = r["declared"], r["collected"]
        min_cov = _MIN_COVERAGE.get(src)
        if min_cov and dec and col is not None and col < min_cov * dec:
            continue  # просевшее покрытие — такому прогону нельзя доверять для пометки stale
        fin = r["finished"] or ""
        if fin > out.get(src, ""):
            out[src] = fin
    return out


def _within_search_scope(item, filters):
    """True, только если объявление гарантированно попадает в поисковый запрос скраперов получателя."""
    if geo.outside_district(item):
        return False
    rooms_f = filters.get("rooms") or []
    rooms = item.get("rooms")
    if rooms_f:
        if rooms is None or rooms not in rooms_f:
            return False
    area_min = filters.get("area_min")
    area = item.get("area")
    if area_min:
        if area is None or area < area_min:
            return False
    price = item.get("price")
    if price is None:
        return False
    if price < filters.get("price_min", 0) or price > filters.get("price_max", 10**12):
        return False
    return True


def _check_listing_stale(inc_row, local_row, fresh_run_ts, filters):
    """Определяет, потеряло ли объявление актуальность к моменту импорта."""
    src = inc_row.get("source") or ""
    inc_ts = str(inc_row.get("last_seen") or "")
    inc_active = bool(inc_row.get("active", 1))

    # Случай 1: у получателя это объявление уже снято, и локальные данные не старее входящих
    if local_row is not None and not local_row["active"]:
        loc_ts = str(local_row.get("removed_at") or local_row.get("last_seen") or "")
        if not inc_active or not inc_ts or loc_ts >= inc_ts:
            when = loc_ts[:16].replace("T", " ")
            reason = f"Снято с продажи в вашей базе ({when})" if when else "Снято с продажи в вашей базе"
            if local_row.get("removed_reason"):
                reason += f": {local_row['removed_reason']}"
            return True, reason

    # Случай 3: объявление уже было снято на устройстве отправителя
    if not inc_active:
        if local_row is not None and local_row["active"] and str(local_row.get("last_seen") or "") > inc_ts:
            return False, None  # на этом компьютере прогон свежее и видел объявление живым
        reason = "Снято с продажи на устройстве отправителя"
        if inc_row.get("removed_reason"):
            reason += f": {inc_row['removed_reason']}"
        return True, reason

    # Случай 2: у получателя объявления нет, но был более свежий полный прогон этого сайта в тех же фильтрах
    if local_row is None and fresh_run_ts and inc_ts and fresh_run_ts > inc_ts and _within_search_scope(inc_row, filters):
        when = fresh_run_ts[:16].replace("T", " ")
        label = SOURCE_LABEL.get(src, src)
        return True, f"Отсутствует в более свежей загрузке {label} от {when}"

    return False, None


def _norm_note(text):
    return str(text or "").strip()[:2000]


def _listing_data_changed(inc_row, prev_item, prev_snap_listing, loc_row, card_was_stale, card_now_stale):
    """Проверяет, изменились ли рыночные данные объявления с прошлого импорта."""
    if card_was_stale and not card_now_stale:
        return True
    if not card_now_stale and prev_item.get("status") != "stale" and loc_row is None:
        return True  # ранее импортированное живое объявление было удалено у получателя — восстановить
    inc_ts = str(inc_row.get("last_seen") or "")
    prev_ts = str(prev_item.get("sender_last_seen") or "")
    inc_act = 1 if inc_row.get("active", 1) else 0
    prev_act = 1 if prev_item.get("sender_active", 1) else 0
    if loc_row is not None and not loc_row["active"] and inc_act:
        loc_ts = str(loc_row.get("removed_at") or loc_row.get("last_seen") or "")
        if inc_ts > loc_ts:
            return True
    if inc_ts and prev_ts and inc_ts < prev_ts:
        return False
    if inc_row.get("price") != prev_item.get("sender_price"):
        return True
    if inc_act != prev_act:
        return True
    for fld in ("description", "title", "address", "renovation"):
        inc_v = str(inc_row.get(fld) or "").strip()
        prev_v = str(prev_snap_listing.get(fld) or "").strip()
        if inc_v != prev_v and (inc_v or prev_v):
            return True
    return False


def _analyze_bundle(z, con, origin_override=None, source_file=""):
    """Единый детерминированный разбор пакета (и для предпросмотра, и для применения импорта)."""
    manifest, raw_cards = _load_bundle_zip(z)
    sender = manifest.get("sender") or {}
    origin_id = str(
        (origin_override or {}).get("origin_id")
        or sender.get("device_id")
        or f"file:{manifest.get('bundle_id') or 'unknown'}"
    ).strip()
    origin_name = str(
        (origin_override or {}).get("origin_name")
        or sender.get("device_name")
        or "Импорт из файла"
    ).strip()

    cfg = settings.load()
    filters = cfg.get("filters") or {}
    fresh_runs = _healthy_done_runs(con)

    local_listings = {}
    for r in con.execute("SELECT * FROM listings"):
        d = dict(r)
        d["source_id"] = str(d["source_id"])
        local_listings[(d["source"], d["source_id"])] = d

    prev_imp = {}
    for r in con.execute(
        "SELECT id, batch_id, export_card_id, source, source_id, status, sender_active, "
        "sender_last_seen, sender_price, snapshot_json FROM import_items ORDER BY id ASC"
    ):
        prev_imp[(r["source"], str(r["source_id"]))] = dict(r)

    existing_origin_notes = {}
    for r in con.execute(
        "SELECT source, source_id, text FROM imported_notes WHERE origin_id=?",
        (origin_id,),
    ):
        existing_origin_notes[(r["source"], str(r["source_id"]))] = _norm_note(r["text"])

    by_source = {
        s: {"listings": 0, "new": 0, "matched": 0, "stale": 0, "duplicate": 0}
        for s in SOURCES
    }
    counts = {
        "total_cards": len(raw_cards),
        "total_listings": 0,
        "new_cards": 0,
        "matched_cards": 0,
        "duplicate_cards": 0,
        "stale_cards": 0,
        "updated_notes": 0,
        "multi_site_cards": 0,
        "manual_merged_cards": 0,
    }
    analyzed_cards = []

    for idx, card in enumerate(raw_cards):
        raw_rows = card.get("listing_rows") or []
        lrows = [r for r in raw_rows if r.get("source") in SOURCES and r.get("source_id") is not None]
        if not lrows:
            continue
        card_id = str(card.get("export_card_id") or f"{lrows[0]['source']}:{lrows[0]['source_id']}")
        counts["total_listings"] += len(lrows)
        if len({r["source"] for r in lrows}) > 1:
            counts["multi_site_cards"] += 1
        if card.get("manual_merged"):
            counts["manual_merged_cards"] += 1

        item_checks = []
        for r in lrows:
            src = r["source"]
            sid = str(r["source_id"])
            k = (src, sid)
            loc = local_listings.get(k)
            is_stale, reason = _check_listing_stale(r, loc, fresh_runs.get(src), filters)
            item_checks.append(
                {
                    "key": k,
                    "row": r,
                    "local": loc,
                    "is_stale": is_stale,
                    "stale_reason": reason,
                }
            )

        all_stale = all(ic["is_stale"] for ic in item_checks)
        stale_reasons = []
        for ic in item_checks:
            if ic["stale_reason"] and ic["stale_reason"] not in stale_reasons:
                stale_reasons.append(ic["stale_reason"])
        card_stale_reason = "; ".join(stale_reasons) if all_stale else None

        inc_note = _norm_note(card.get("note"))
        keys = [ic["key"] for ic in item_checks]
        all_prev_imported = all(k in prev_imp for k in keys)

        # Заметка отправителя считается изменённой при появлении, редактировании или очистке
        prev_origin_note = ""
        for k in keys:
            if k in existing_origin_notes and existing_origin_notes[k]:
                prev_origin_note = existing_origin_notes[k]
                break
        if not prev_origin_note and all_prev_imported:
            # На случай если заметка была на неактуальной карточке из прошлого батча (не попавшей в listings)
            for k in keys:
                snap = _parse_json(prev_imp[k].get("snapshot_json"), {})
                if snap.get("note"):
                    prev_origin_note = _norm_note(snap.get("note"))
                    break
        note_changed = inc_note != prev_origin_note

        data_changed = False
        if all_prev_imported:
            card_was_stale = all(prev_imp[k].get("status") == "stale" for k in keys)
            for ic in item_checks:
                p_item = prev_imp[ic["key"]]
                p_snap = _parse_json(p_item.get("snapshot_json"), {})
                if _listing_data_changed(
                    ic["row"],
                    p_item,
                    p_snap.get("listing") or {},
                    ic["local"],
                    card_was_stale,
                    all_stale,
                ):
                    data_changed = True
                    break

        if all_prev_imported:
            if not data_changed and not note_changed:
                status = "duplicate"
                counts["duplicate_cards"] += 1
            elif data_changed and not all_stale:
                status = "matched"
                counts["matched_cards"] += 1
                if note_changed:
                    counts["updated_notes"] += 1
            elif note_changed:
                status = "stale" if all_stale else "note_updated"
                if all_stale:
                    counts["stale_cards"] += 1
                counts["updated_notes"] += 1
            else:
                status = "stale"
                counts["stale_cards"] += 1
        else:
            if all_stale:
                status = "stale"
                counts["stale_cards"] += 1
                if note_changed and any(ic["local"] is not None for ic in item_checks):
                    counts["updated_notes"] += 1
            elif any(ic["local"] is not None for ic in item_checks):
                status = "matched"
                counts["matched_cards"] += 1
                if note_changed:
                    counts["updated_notes"] += 1
            else:
                status = "new"
                counts["new_cards"] += 1
                if note_changed:
                    counts["updated_notes"] += 1

        b_stat_key = "duplicate" if status == "duplicate" else ("matched" if status == "note_updated" else status)
        for ic in item_checks:
            s_entry = by_source[ic["key"][0]]
            s_entry["listings"] += 1
            s_entry[b_stat_key] += 1

        analyzed_cards.append(
            {
                "idx": idx,
                "export_card_id": card_id,
                "card": card,
                "items": item_checks,
                "status": status,
                "all_stale": all_stale,
                "stale_reason": card_stale_reason,
                "inc_note": inc_note,
                "note_changed": note_changed,
                "data_changed": data_changed,
            }
        )

    counts["total_cards"] = len(analyzed_cards)
    all_duplicates = bool(analyzed_cards) and (
        counts["new_cards"] == 0
        and counts["matched_cards"] == 0
        and counts["stale_cards"] == 0
        and counts["updated_notes"] == 0
    )

    return {
        "manifest": manifest,
        "origin_id": origin_id,
        "origin_name": origin_name,
        "source_file": source_file,
        "cfg": cfg,
        "local_listings": local_listings,
        "counts": counts,
        "by_source": by_source,
        "all_duplicates": all_duplicates,
        "cards": analyzed_cards,
    }


def _open_zip(path):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"файл не найден: {p}")
    try:
        return zipfile.ZipFile(p, "r"), p
    except zipfile.BadZipFile as e:
        raise ValueError("выбранный файл не является ZIP-архивом .rwcards.zip") from e


def preview_cards_zip(path, origin_override=None, con=None):
    """Предпросмотр содержимого .rwcards.zip без изменения локальной базы."""
    con, own = _own_con(con)
    try:
        z, p = _open_zip(path)
        with z:
            an = _analyze_bundle(z, con, origin_override=origin_override, source_file=p.name)
        m = an["manifest"]
        c = an["counts"]
        preview_cards = []
        for ac in an["cards"][:100]:
            summ = ac["card"].get("card_summary") or {}
            preview_cards.append(
                {
                    "export_card_id": ac["export_card_id"],
                    "status": ac["status"],
                    "stale_reason": ac["stale_reason"],
                    "liked": bool(ac["card"].get("liked")),
                    "has_note": bool(ac["inc_note"]),
                    "sources": sorted({ic["key"][0] for ic in ac["items"]}),
                    "rooms": summ.get("rooms"),
                    "area": summ.get("area"),
                    "price": summ.get("price"),
                    "address": summ.get("address") or "",
                }
            )
        return {
            "bundle_id": m.get("bundle_id"),
            "exported_at": m.get("exported_at"),
            "kind": m.get("kind") or "cards",
            "app_version": m.get("app_version") or "",
            "origin_id": an["origin_id"],
            "origin_name": an["origin_name"],
            "source_file": p.name,
            "db_freshness": m.get("db_freshness") or {},
            **c,
            "by_source": an["by_source"],
            "all_duplicates": an["all_duplicates"],
            "cards_preview": preview_cards,
        }
    finally:
        if own:
            con.close()


def _extract_image(z, z_names, arc, dest_dir, source, source_id):
    """Безопасно извлекает изображение из архива в указанную папку."""
    if not arc or arc not in z_names:
        return False
    ext = Path(arc).suffix.lower()
    if ext not in {"." + e for e in _VALID_IMG_EXTS}:
        return False
    dst_ext = ".jpg" if ext == ".jpeg" else ext
    folder = dest_dir / source
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{_safe_sid(source_id)}{dst_ext}"
    if target.exists():
        return False
    tmp = folder / f"{_safe_sid(source_id)}.part"
    tmp.write_bytes(z.read(arc))
    tmp.replace(target)
    return True


_UPDATABLE_COLS = (
    "url", "title", "price", "price_m2", "rooms", "area", "living_area", "kitchen_area",
    "floor", "floors", "address", "lat", "lon", "renovation", "renovation_raw",
    "building_type", "build_year", "is_new_building", "seller_type", "photo",
    "published_at", "fetch_mode", "details_fetched", "cluster_key",
)


def _upsert_imported_listing(con, inc_row, loc_row, cfg, item_is_stale):
    """Интегрирует одно объявление из пакета в таблицу listings без искажения imported_only."""
    src = inc_row["source"]
    sid = str(inc_row["source_id"])
    raw_obj = {"apartments": True} if inc_row.get("apartments") else {}
    flags = dict(inc_row.get("flags") or {})
    item_eval = dict(inc_row)
    item_eval["flags"] = {}
    zone = geo.classify(item_eval, cfg["zone"])
    reasons = rules.evaluate(item_eval, cfg["filters"])
    # Сохраняем признаки обременения/доли/торгов, если они уже были определены отправителем
    merged_flags = {**flags, **(item_eval.get("flags") or {})}
    excluded = "; ".join(reasons) or None
    flags_json = json.dumps(merged_flags, ensure_ascii=False)
    raw_json = json.dumps(raw_obj, ensure_ascii=False)
    now_ts = db.now()

    if loc_row is None:
        cols = [
            "source", "source_id", *db.FIELDS,
            "first_seen", "last_seen", "active", "missed",
            "removed_at", "removed_reason", "imported_only",
        ]
        vals = {k: inc_row.get(k) for k in db.FIELDS}
        vals["zone"] = zone
        vals["excluded"] = excluded
        vals["flags"] = flags_json
        vals["raw"] = raw_json
        vals.setdefault("details_fetched", 1 if inc_row.get("description") else 0)
        act = 0 if item_is_stale else (1 if inc_row.get("active", 1) else 0)
        fs = inc_row.get("first_seen") or inc_row.get("last_seen") or now_ts
        ls = inc_row.get("last_seen") or fs
        rem_at = (inc_row.get("removed_at") or ls) if not act else None
        rem_reason = (inc_row.get("removed_reason") or "снято на сайте") if not act else None
        con.execute(
            f"INSERT INTO listings({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
            [
                src, sid, *[vals[k] for k in db.FIELDS],
                fs, ls, act, 0, rem_at, rem_reason, 1,
            ],
        )
        return False  # это imported_only=1, рыночные снимки не затрагивает

    # Объявление уже есть локально — сохраняем прежний imported_only
    touched_market = False
    is_local_market = not (loc_row.get("imported_only") or 0)
    inc_fs = str(inc_row.get("first_seen") or "")
    loc_fs = str(loc_row.get("first_seen") or "")
    if inc_fs and (not loc_fs or inc_fs < loc_fs):
        con.execute("UPDATE listings SET first_seen=? WHERE source=? AND source_id=?", (inc_fs, src, sid))
        if is_local_market:
            touched_market = True

    inc_ls = str(inc_row.get("last_seen") or "")
    loc_ls = str(loc_row.get("last_seen") or "")
    if not item_is_stale and inc_ls and (not loc_ls or inc_ls > loc_ls):
        sets = []
        args = []
        for k in _UPDATABLE_COLS:
            nv = inc_row.get(k)
            if nv is not None:
                sets.append(f"{k}=?")
                args.append(nv)
        inc_desc = (inc_row.get("description") or "").strip()
        if inc_desc and (inc_desc != (loc_row.get("description") or "").strip() or not (loc_row.get("description") or "").strip()):
            sets.append("description=?")
            args.append(inc_row.get("description"))
        if raw_obj and not _parse_json(loc_row.get("raw"), {}).get("apartments"):
            merged_raw = {**_parse_json(loc_row.get("raw"), {}), **raw_obj}
            sets.append("raw=?")
            args.append(json.dumps(merged_raw, ensure_ascii=False))
        sets.extend(["zone=?", "excluded=?", "flags=?", "last_seen=?"])
        args.extend([zone, excluded, flags_json, inc_ls])
        if inc_row.get("active", 1) and not loc_row["active"]:
            sets.extend(["active=1", "missed=0", "removed_at=NULL", "removed_reason=NULL"])
            if is_local_market:
                touched_market = True
        if is_local_market and (inc_row.get("price") != loc_row.get("price") or zone != loc_row.get("zone") or excluded != loc_row.get("excluded")):
            touched_market = True
        args.extend([src, sid])
        con.execute(f"UPDATE listings SET {','.join(sets)} WHERE source=? AND source_id=?", args)
    elif not (loc_row.get("description") or "").strip() and (inc_row.get("description") or "").strip():
        con.execute("UPDATE listings SET description=? WHERE source=? AND source_id=?", (inc_row.get("description"), src, sid))

    return touched_market


def _merge_card_manual_groups(con, incoming_rows):
    """Безопасно вливает manual_merges для актуальных карточек через связные компоненты."""
    if not incoming_rows:
        return
    main_rows = [dict(r) for r in con.execute("SELECT source, source_id, group_id, is_primary, ts FROM manual_merges")]
    by_key, by_tag, info = {}, {}, {}
    for side, src_rows in (("main", main_rows), ("imp", incoming_rows)):
        for r in src_rows:
            k = (r["source"], str(r["source_id"]))
            tag = (side, r["group_id"])
            by_key.setdefault(k, set()).add(tag)
            by_tag.setdefault(tag, set()).add(k)
            info.setdefault(k, []).append((side, r))
    seen_k, used_gids, out = set(), set(), []
    for k0 in sorted(by_key):
        if k0 in seen_k:
            continue
        comp_keys, comp_tags = set(), set()
        stack = [k0]
        seen_k.add(k0)
        while stack:
            k = stack.pop()
            comp_keys.add(k)
            for tag in by_key[k]:
                if tag in comp_tags:
                    continue
                comp_tags.add(tag)
                for nk in by_tag[tag]:
                    if nk not in seen_k:
                        seen_k.add(nk)
                        stack.append(nk)
        if len(comp_keys) < 2:
            continue
        cands = [
            (1 if r.get("is_primary") else 0, r.get("ts") or "", 1 if side == "main" else 0, k, r["group_id"])
            for k in comp_keys
            for side, r in info[k]
        ]
        _, win_ts, _, prim_key, win_gid = max(cands)
        if win_gid in used_gids:
            win_gid = f"{prim_key[0]}:{prim_key[1]}"
        used_gids.add(win_gid)
        ts = max((r.get("ts") or "" for k in comp_keys for _, r in info[k]), default=win_ts)
        for src, sid in sorted(comp_keys):
            out.append((src, sid, win_gid, 1 if (src, sid) == prim_key else 0, ts))
    con.execute("DELETE FROM manual_merges")
    if out:
        con.executemany(
            "INSERT OR IGNORE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)",
            out,
        )
    dedupe.clean_manual_merges(con)


def import_cards_zip(
    path,
    likes_mode="all",
    channel="file",
    origin_override=None,
    apply_manual_merges=True,
    con=None,
):
    """Импортирует пакет .rwcards.zip в слой «Импорт» с фильтрацией stale, повторов и заметок."""
    if likes_mode not in LIKES_MODES:
        raise ValueError(f"неизвестный режим избранного: {likes_mode}")
    con, own = _own_con(con)
    try:
        z, p = _open_zip(path)
        with z:
            an = _analyze_bundle(z, con, origin_override=origin_override, source_file=p.name)
            m = an["manifest"]
            c = an["counts"]
            if an["all_duplicates"]:
                return {
                    "skipped_empty": True,
                    "batch_id": None,
                    "origin_id": an["origin_id"],
                    "origin_name": an["origin_name"],
                    "source_file": p.name,
                    "likes_mode": likes_mode,
                    **c,
                    "by_source": an["by_source"],
                    "message": f"Все карточки ({c['total_cards']}) уже были импортированы ранее, новых данных и изменений в заметках нет",
                }

            receiver_ident = settings.ensure_device_identity(con)
            receiver_id = receiver_ident["device_id"]
            now_ts = db.now()
            stats_payload = {
                "by_source": an["by_source"],
                "multi_site_cards": c["multi_site_cards"],
                "manual_merged_cards": c["manual_merged_cards"],
            }
            cur = con.execute(
                """INSERT INTO import_batches(
                       ts, channel, origin_id, origin_name, source_file, bundle_id, likes_mode, stats_json,
                       total_listings, total_cards, new_cards, matched_cards, duplicate_cards, stale_cards, updated_notes
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now_ts,
                    channel,
                    an["origin_id"],
                    an["origin_name"],
                    p.name,
                    m.get("bundle_id") or "",
                    likes_mode,
                    json.dumps(stats_payload, ensure_ascii=False),
                    c["total_listings"],
                    c["total_cards"],
                    c["new_cards"],
                    c["matched_cards"],
                    c["duplicate_cards"],
                    c["stale_cards"],
                    c["updated_notes"],
                ),
            )
            batch_id = cur.lastrowid

            z_names = set(z.namelist())
            cfg = an["cfg"]
            touched_market = False
            live_mm_rows = []
            live_keys_in_db = set()
            imp_dir = import_images_dir()
            note_updated_at = m.get("exported_at") or now_ts

            for ac in an["cards"]:
                card = ac["card"]
                status = ac["status"]
                is_live_card = status in ("new", "matched", "note_updated")

                if is_live_card:
                    for ic in ac["items"]:
                        src, sid = ic["key"]
                        r = ic["row"]
                        if _upsert_imported_listing(con, r, ic["local"], cfg, ic["is_stale"]):
                            touched_market = True
                        live_keys_in_db.add((src, sid))
                        if images.local_path(src, sid) is None:
                            _extract_image(z, z_names, r.get("image_arc"), images.IMAGES, src, sid)

                    for ph_src, ph_sid, ph_ts, ph_price in card.get("price_history_rows") or []:
                        if ph_src in SOURCES and ph_sid is not None and ph_ts and ph_price is not None:
                            con.execute(
                                """INSERT INTO price_history(source, source_id, ts, price)
                                   SELECT ?,?,?,? WHERE NOT EXISTS (
                                       SELECT 1 FROM price_history WHERE source=? AND source_id=? AND ts=? AND price=?)""",
                                (ph_src, str(ph_sid), ph_ts, int(ph_price), ph_src, str(ph_sid), ph_ts, int(ph_price)),
                            )

                    if apply_manual_merges and card.get("manual_merges_rows"):
                        live_key_set = {ic["key"] for ic in ac["items"] if not ic["is_stale"] or ic["local"] is not None}
                        mm_subset = [
                            mm
                            for mm in card["manual_merges_rows"]
                            if (mm.get("source"), str(mm.get("source_id"))) in live_key_set
                        ]
                        if len(mm_subset) >= 2:
                            live_mm_rows.extend(mm_subset)
                elif status == "stale":
                    # Неактуальные карточки в listings не добавляем; фото кладём в import_images для превью
                    for ic in ac["items"]:
                        src, sid = ic["key"]
                        if ic["local"] is not None:
                            live_keys_in_db.add((src, sid))
                        if import_image_path(src, sid) is None:
                            _extract_image(z, z_names, ic["row"].get("image_arc"), imp_dir, src, sid)

            if apply_manual_merges and live_mm_rows:
                _merge_card_manual_groups(con, live_mm_rows)

            objects_count = dedupe.rebuild(con)

            # Актуализируем object_id → все ключи объекта в БД, чтобы лайки и заметки не терялись
            db_keys = {(r["source"], str(r["source_id"])) for r in con.execute("SELECT source, source_id FROM listings")}
            obj_keys_by_key = {}
            by_oid = defaultdict(list)
            for r in con.execute("SELECT source, source_id, object_id FROM listings"):
                k = (r["source"], str(r["source_id"]))
                oid = r["object_id"] or f"{k[0]}:{k[1]}"
                by_oid[oid].append(k)
            for ks in by_oid.values():
                for k in ks:
                    obj_keys_by_key[k] = ks

            # Применяем likes_mode и imported_notes, записываем import_items
            for ac in an["cards"]:
                card = ac["card"]
                status = ac["status"]
                is_live_card = status in ("new", "matched", "note_updated")
                card_keys = [ic["key"] for ic in ac["items"]]
                in_db_keys = [k for k in card_keys if k in db_keys]

                if is_live_card and in_db_keys:
                    expand_keys = sorted({ek for k in in_db_keys for ek in obj_keys_by_key.get(k, [k])})
                    should_like = (likes_mode == "all") or (likes_mode == "preserve" and bool(card.get("liked")))
                    if should_like:
                        for lk_src, lk_sid in expand_keys:
                            con.execute("INSERT OR IGNORE INTO likes(source, source_id, ts) VALUES(?,?,?)", (lk_src, lk_sid, now_ts))
                            con.execute("DELETE FROM dislikes WHERE source=? AND source_id=?", (lk_src, lk_sid))

                # Многопользовательские заметки (только для ключей, присутствующих в listings)
                if in_db_keys and (ac["inc_note"] or ac["note_changed"]):
                    note_ts = card.get("note_ts") or note_updated_at
                    for n_src, n_sid in in_db_keys:
                        if ac["inc_note"]:
                            con.execute(
                                """INSERT INTO imported_notes(source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id)
                                   VALUES(?,?,?,?,?,?,?,?)
                                   ON CONFLICT(source, source_id, origin_id) DO UPDATE SET
                                       origin_name=excluded.origin_name, text=excluded.text,
                                       ts=excluded.ts, updated_at=excluded.updated_at, batch_id=excluded.batch_id""",
                                (n_src, n_sid, an["origin_id"], an["origin_name"], ac["inc_note"], note_ts, note_updated_at, batch_id),
                            )
                        else:
                            con.execute(
                                "DELETE FROM imported_notes WHERE source=? AND source_id=? AND origin_id=?",
                                (n_src, n_sid, an["origin_id"]),
                            )

                if in_db_keys and card.get("imported_notes_chain"):
                    for ch in card["imported_notes_chain"]:
                        o3 = str(ch.get("origin_id") or "").strip()
                        txt3 = _norm_note(ch.get("text"))
                        if not o3 or o3 in (receiver_id, an["origin_id"]) or not txt3:
                            continue
                        ts3 = str(ch.get("updated_at") or ch.get("ts") or "")
                        for n_src, n_sid in in_db_keys:
                            con.execute(
                                """INSERT INTO imported_notes(source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id)
                                   VALUES(?,?,?,?,?,?,?,?)
                                   ON CONFLICT(source, source_id, origin_id) DO UPDATE SET
                                       origin_name=CASE WHEN COALESCE(excluded.updated_at, excluded.ts, '') > COALESCE(imported_notes.updated_at, imported_notes.ts, '')
                                                        THEN excluded.origin_name ELSE imported_notes.origin_name END,
                                       text=CASE WHEN COALESCE(excluded.updated_at, excluded.ts, '') > COALESCE(imported_notes.updated_at, imported_notes.ts, '')
                                                 THEN excluded.text ELSE imported_notes.text END,
                                       ts=CASE WHEN COALESCE(excluded.updated_at, excluded.ts, '') > COALESCE(imported_notes.updated_at, imported_notes.ts, '')
                                               THEN excluded.ts ELSE imported_notes.ts END,
                                       updated_at=CASE WHEN COALESCE(excluded.updated_at, excluded.ts, '') > COALESCE(imported_notes.updated_at, imported_notes.ts, '')
                                                       THEN excluded.updated_at ELSE imported_notes.updated_at END""",
                                (n_src, n_sid, o3, ch.get("origin_name") or "", txt3, ch.get("ts") or ts3, ts3, batch_id),
                            )

                # Срез каждой строки в import_items для просмотра истории пакета и неактуальных карточек
                summary = card.get("card_summary") or {}
                ph_by_key = defaultdict(list)
                for ph_src, ph_sid, ph_ts, ph_price in card.get("price_history_rows") or []:
                    ph_by_key[(ph_src, str(ph_sid))].append([ph_ts, ph_price])

                for ic in ac["items"]:
                    src, sid = ic["key"]
                    r = ic["row"]
                    snap = {
                        "listing": r,
                        "card_summary": summary,
                        "note": ac["inc_note"],
                        "note_ts": card.get("note_ts") or "",
                        "origin_id": an["origin_id"],
                        "origin_name": an["origin_name"],
                        "history": ph_by_key.get((src, sid), []),
                        "imported_notes_chain": card.get("imported_notes_chain") or [],
                    }
                    item_status = status
                    item_reason = ic["stale_reason"] or ac["stale_reason"]
                    con.execute(
                        """INSERT INTO import_items(
                               batch_id, export_card_id, source, source_id, status, stale_reason,
                               sender_liked, sender_active, sender_last_seen, sender_price, snapshot_json
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            batch_id,
                            ac["export_card_id"],
                            src,
                            sid,
                            item_status,
                            item_reason,
                            1 if (r.get("liked") or card.get("liked")) else 0,
                            1 if r.get("active", 1) else 0,
                            str(r.get("last_seen") or ""),
                            r.get("price"),
                            json.dumps(snap, ensure_ascii=False),
                        ),
                    )

            if touched_market:
                history.mark_snapshots_dirty(con)
            con.commit()

            return {
                "skipped_empty": False,
                "batch_id": batch_id,
                "ts": now_ts,
                "channel": channel,
                "origin_id": an["origin_id"],
                "origin_name": an["origin_name"],
                "source_file": p.name,
                "bundle_id": m.get("bundle_id") or "",
                "likes_mode": likes_mode,
                "objects": objects_count,
                **c,
                "by_source": an["by_source"],
            }
    finally:
        if own:
            con.close()


def list_batches(con, limit=200):
    """Список всех партий импорта в слое «Импорт» (новые сверху)."""
    out = []
    for r in con.execute("SELECT * FROM import_batches ORDER BY id DESC LIMIT ?", (int(limit),)):
        d = dict(r)
        d["stats"] = _parse_json(d.pop("stats_json", None), {})
        out.append(d)
    return out


def get_batch_detail(con, batch_id):
    """Детальный состав партии импорта с живым состоянием актуальных карточек и срезом неактуальных."""
    row = con.execute("SELECT * FROM import_batches WHERE id=?", (int(batch_id),)).fetchone()
    if row is None:
        raise LookupError(f"партия импорта №{batch_id} не найдена")
    batch = dict(row)
    batch["stats"] = _parse_json(batch.pop("stats_json", None), {})

    raw_items = [
        dict(r)
        for r in con.execute("SELECT * FROM import_items WHERE batch_id=? ORDER BY id ASC", (int(batch_id),))
    ]
    grouped = defaultdict(list)
    order = []
    for it in raw_items:
        cid = it["export_card_id"] or f"{it['source']}:{it['source_id']}"
        if cid not in grouped:
            order.append(cid)
        grouped[cid].append(it)

    all_keys = {(it["source"], str(it["source_id"])) for it in raw_items}
    live_rows = {}
    if all_keys:
        for r in con.execute("SELECT * FROM listings"):
            k = (r["source"], str(r["source_id"]))
            if k in all_keys:
                d = dict(r)
                d["source_id"] = str(d["source_id"])
                live_rows[k] = d

    target_oids = {
        live_rows[k]["object_id"] or f"{k[0]}:{k[1]}"
        for k in live_rows
    }
    oid_rows = defaultdict(list)
    if target_oids:
        for r in con.execute("SELECT * FROM listings ORDER BY first_seen DESC"):
            d = dict(r)
            d["source_id"] = str(d["source_id"])
            oid = d["object_id"] or f"{d['source']}:{d['source_id']}"
            if oid in target_oids:
                oid_rows[oid].append(d)

    all_live_keys = {(r["source"], r["source_id"]) for rs in oid_rows.values() for r in rs}
    hist = defaultdict(list)
    if all_live_keys:
        for r in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
            k = (r["source"], str(r["source_id"]))
            if k in all_live_keys:
                hist[k].append([r["ts"], r["price"]])

    liked = {(r["source"], str(r["source_id"])) for r in con.execute("SELECT source, source_id FROM likes")}
    disliked = {(r["source"], str(r["source_id"])) for r in con.execute("SELECT source, source_id FROM dislikes")}
    notes = {
        (r["source"], str(r["source_id"])): (r["text"] or "", r["ts"] or "")
        for r in con.execute("SELECT source, source_id, text, ts FROM notes")
    }
    imp_notes = defaultdict(dict)
    for r in con.execute(
        "SELECT source, source_id, origin_id, origin_name, text, ts, updated_at FROM imported_notes WHERE COALESCE(text, '') != ''"
    ):
        k = (r["source"], str(r["source_id"]))
        if k in all_live_keys:
            imp_notes[k][r["origin_id"]] = {
                "origin_id": r["origin_id"],
                "origin_name": r["origin_name"] or "",
                "text": r["text"] or "",
                "ts": r["updated_at"] or r["ts"] or "",
            }

    _, primaries = dedupe.active_manual_merges(con)

    cards = []
    counts = {"all": 0, "relevant": 0, "stale": 0, "with_notes": 0, "duplicate": 0}
    for cid in order:
        its = grouped[cid]
        statuses = [it["status"] for it in its]
        status = statuses[0] if statuses else "new"
        is_stale = status == "stale"
        stale_reasons = []
        for it in its:
            if it.get("stale_reason") and it["stale_reason"] not in stale_reasons:
                stale_reasons.append(it["stale_reason"])
        stale_reason = "; ".join(stale_reasons) if stale_reasons else None

        snaps = [_parse_json(it.get("snapshot_json"), {}) for it in its]
        snap0 = snaps[0] if snaps else {}
        summary = snap0.get("card_summary") or {}
        sender_note = _norm_note(snap0.get("note"))
        sender_descs = {}
        for it, sn in zip(its, snaps):
            lr = sn.get("listing") or {}
            s_desc = (lr.get("description") or "").strip()
            if s_desc:
                sender_descs[f"{it['source']}:{it['source_id']}"] = s_desc

        card_live_oids = []
        for it in its:
            k = (it["source"], str(it["source_id"]))
            if k in live_rows:
                oid = live_rows[k]["object_id"] or f"{k[0]}:{k[1]}"
                if oid not in card_live_oids:
                    card_live_oids.append(oid)

        in_db = bool(card_live_oids) and not (is_stale and not any(k in live_rows for k in ((it["source"], str(it["source_id"])) for it in its)))
        if in_db:
            primary_oid = card_live_oids[0]
            members = []
            seen_m = set()
            for oid in card_live_oids:
                for lr in oid_rows.get(oid, ()):
                    mk = (lr["source"], lr["source_id"])
                    if mk not in seen_m:
                        seen_m.add(mk)
                        members.append(dict(lr))
            members.sort(key=lambda i: ((i["source"], i["source_id"]) not in primaries, i.get("first_seen") or ""))

            card_notes_map = {}
            personal_note = ""
            item_out = []
            for lr in members:
                mk = (lr["source"], lr["source_id"])
                nt_t, _ = notes.get(mk, ("", ""))
                if nt_t and not personal_note:
                    personal_note = nt_t
                for oid_n, nobj in imp_notes.get(mk, {}).items():
                    prev_n = card_notes_map.get(oid_n)
                    if prev_n is None or nobj["ts"] > prev_n["ts"]:
                        card_notes_map[oid_n] = nobj
                img_p = images.local_path(*mk)
                imp_p = import_image_path(*mk)
                photo_url = f"/img/{mk[0]}/{mk[1]}" if img_p else (f"/img/import/{mk[0]}/{mk[1]}" if imp_p else None)
                item_out.append(
                    {
                        "source": mk[0],
                        "source_id": mk[1],
                        "key": f"{mk[0]}:{mk[1]}",
                        "url": lr.get("url"),
                        "title": lr.get("title"),
                        "price": lr.get("price"),
                        "active": bool(lr.get("active", 1)),
                        "last_seen": lr.get("last_seen"),
                        "description": lr.get("description") or "",
                        "sender_description": sender_descs.get(f"{mk[0]}:{mk[1]}", ""),
                        "history": hist.get(mk, []),
                        "photo_url": photo_url,
                        "removed_reason": lr.get("removed_reason"),
                    }
                )
            p_row = members[0]
            live_m = [m for m in members if m.get("active", 1)]
            prices = [m["price"] for m in (live_m or members) if m.get("price") is not None]
            min_p = min(prices) if prices else None
            area = p_row.get("area")
            card_obj = {
                "export_card_id": cid,
                "object_id": primary_oid,
                "in_db": True,
                "status": status,
                "is_stale": is_stale,
                "stale_reason": stale_reason,
                "sender_liked": any(it["sender_liked"] for it in its),
                "sender_note": sender_note,
                "liked": any((m["source"], m["source_id"]) in liked for m in members),
                "disliked": any((m["source"], m["source_id"]) in disliked for m in members)
                and not any((m["source"], m["source_id"]) in liked for m in members),
                "note": personal_note,
                "imported_notes": list(card_notes_map.values()),
                "title": p_row.get("title") or summary.get("title") or "",
                "address": p_row.get("address") or summary.get("address") or "",
                "zone": p_row.get("zone") or summary.get("zone") or "B",
                "rooms": p_row.get("rooms") if p_row.get("rooms") is not None else summary.get("rooms"),
                "area": area if area is not None else summary.get("area"),
                "floor": p_row.get("floor") if p_row.get("floor") is not None else summary.get("floor"),
                "floors": p_row.get("floors") if p_row.get("floors") is not None else summary.get("floors"),
                "price": min_p if min_p is not None else summary.get("price"),
                "price_max": max(prices) if prices else summary.get("price_max"),
                "price_m2": round(min_p / area) if min_p and area else p_row.get("price_m2"),
                "renovation": p_row.get("renovation") or summary.get("renovation"),
                "apartments": any(_parse_json(m.get("raw"), {}).get("apartments") for m in members),
                "is_new_building": any(m.get("is_new_building") for m in members),
                "sources": sorted({m["source"] for m in members}),
                "active": any(m.get("active", 1) for m in members),
                "excluded": p_row.get("excluded") if all(m.get("excluded") for m in members) else None,
                "first_seen": min((m.get("first_seen") or "" for m in members), default=""),
                "last_seen": max((m.get("last_seen") or "" for m in members), default=""),
                "items": item_out,
            }
        else:
            # Полностью автономная сборка карточки из snapshot_json (Случай 2 и Случай 3 stale)
            item_out = []
            for it, sn in zip(its, snaps):
                lr = sn.get("listing") or {}
                src, sid = it["source"], str(it["source_id"])
                has_imp_img = import_image_path(src, sid) is not None
                item_out.append(
                    {
                        "source": src,
                        "source_id": sid,
                        "key": f"{src}:{sid}",
                        "url": lr.get("url"),
                        "title": lr.get("title"),
                        "price": lr.get("price") if lr.get("price") is not None else it.get("sender_price"),
                        "active": bool(it.get("sender_active", 1)),
                        "last_seen": lr.get("last_seen") or it.get("sender_last_seen"),
                        "description": lr.get("description") or "",
                        "sender_description": lr.get("description") or "",
                        "history": sn.get("history") or [],
                        "photo_url": f"/img/import/{src}/{sid}" if has_imp_img else None,
                        "stale_reason": it.get("stale_reason"),
                    }
                )
            imp_n_list = []
            if sender_note:
                imp_n_list.append(
                    {
                        "origin_id": snap0.get("origin_id") or batch["origin_id"],
                        "origin_name": snap0.get("origin_name") or batch["origin_name"],
                        "text": sender_note,
                        "ts": snap0.get("note_ts") or batch["ts"],
                    }
                )
            card_obj = {
                "export_card_id": cid,
                "object_id": cid,
                "in_db": False,
                "status": status,
                "is_stale": is_stale,
                "stale_reason": stale_reason,
                "sender_liked": any(it["sender_liked"] for it in its),
                "sender_note": sender_note,
                "liked": False,
                "disliked": False,
                "note": "",
                "imported_notes": imp_n_list,
                "title": summary.get("title") or (item_out[0].get("title") if item_out else ""),
                "address": summary.get("address") or "",
                "zone": summary.get("zone") or "B",
                "rooms": summary.get("rooms"),
                "area": summary.get("area"),
                "floor": summary.get("floor"),
                "floors": summary.get("floors"),
                "price": summary.get("price"),
                "price_max": summary.get("price_max"),
                "price_m2": summary.get("price_m2"),
                "renovation": summary.get("renovation"),
                "apartments": bool(summary.get("apartments")),
                "is_new_building": bool(summary.get("is_new_building")),
                "sources": summary.get("sources") or sorted({i["source"] for i in item_out}),
                "active": bool(summary.get("active", False)) and not is_stale,
                "excluded": summary.get("excluded"),
                "first_seen": summary.get("first_seen") or "",
                "last_seen": summary.get("last_seen") or "",
                "items": item_out,
            }

        counts["all"] += 1
        if card_obj["is_stale"]:
            counts["stale"] += 1
        else:
            counts["relevant"] += 1
        if card_obj["status"] == "duplicate":
            counts["duplicate"] += 1
        if card_obj["note"] or card_obj["imported_notes"] or card_obj["sender_note"]:
            counts["with_notes"] += 1
        cards.append(card_obj)

    return {"batch": batch, "cards": cards, "counts": counts}


def delete_batch(con, batch_id):
    """Удаляет запись партии из журнала импортов, не трогая уже интегрированные объявления и заметки."""
    row = con.execute("SELECT id FROM import_batches WHERE id=?", (int(batch_id),)).fetchone()
    if row is None:
        raise LookupError(f"партия импорта №{batch_id} не найдена")
    con.execute("DELETE FROM import_items WHERE batch_id=?", (int(batch_id),))
    con.execute("DELETE FROM import_batches WHERE id=?", (int(batch_id),))
    con.commit()
    return {"ok": True, "deleted": int(batch_id)}
