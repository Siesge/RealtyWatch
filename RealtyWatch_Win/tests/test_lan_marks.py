"""Тесты обмена пользовательскими отметками и ручными склейками по локальной сети (LAN):
- Тихий запрос (Pull) лайков, дизлайков, заметок и ручных объединений у сопряжённого партнёра без уведомлений на его стороне;
- Выбор отдельных пунктов или всех сразу;
- Режимы «Добавить к моим» (merge) и «Перезаписать поверх моих» (replace с автобэкапом);
- Отправка (Push) с уведомлением и окном выбора у получателя;
- Проверка интерфейса импорта/экспорта отметок и приёма предложения в Chromium."""
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi import HTTPException

from app import db, dedupe, lan_sync, server, settings, tiles, updater
from tests.support import Case, LocalBrowser, con, has_browser
from tests.test_lan_sync import _PeerProc, _asgi_call, _free_port, _insert_row, _ui_call
from tests.test_tiles import _make_png


class LanMarksPullAndPushTests(Case):
    """Двухузловые LAN-тесты тихого запроса (Pull) и отправки с уведомлением (Push) для всех 4 категорий."""

    def setUp(self):
        super().setUp()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        self._orig_bind_host = lan_sync.service._bind_host
        lan_sync.service._bind_host = "127.0.0.1"
        self.peer_b = None

    def tearDown(self):
        if self.peer_b is not None:
            self.peer_b.stop()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        lan_sync.service._bind_host = self._orig_bind_host
        super().tearDown()

    def _start_and_pair(self):
        port_a = _free_port()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": port_a,
            "auto_discovery": False,
            "device_id": "",
            "device_name": "Компьютер А",
        }
        settings.save(cfg)
        c = con()
        my_a_id = lan_sync.service._identity(c)["device_id"]
        c.close()
        self.assertTrue(lan_sync.start_if_enabled())

        self.peer_b = _PeerProc(self.dir / "peer_b", name="Ноутбук Б", auto_discovery=False).start()
        st_pin, pin_resp = _ui_call(self.peer_b.ui_port, "POST", "/api/lan/pin", {})
        self.assertEqual(st_pin, 200)
        pair_res = server.lan_pair({"ip": "127.0.0.1", "port": self.peer_b.lan_port, "pin": pin_resp["pin"]})
        self.assertTrue(pair_res["ok"])
        return my_a_id, pair_res["device_id"]

    def test_silent_pull_subset_all_merge_and_replace_without_disturbing_peer(self):
        _, peer_b_id = self._start_and_pair()

        # Подготовка базы на Компьютере А (свои лайки, дизлайки, заметки и своё ручное объединение 101+102)
        c = con()
        _insert_row(c, "cian", "101", price=9_500_000, area=44.0, rooms=1, floor=3, address="Ялта, ул. Московская, 5")
        _insert_row(c, "avito", "102", price=9_600_000, area=49.0, rooms=1, floor=3, address="Ялта, ул. Московская, 5")
        _insert_row(c, "cian", "103", price=11_000_000, area=55.0, rooms=2, floor=4, address="Ялта, ул. Киевская, 12")
        _insert_row(c, "cian", "104", price=11_800_000, area=58.0, rooms=2, floor=5, address="Ялта, ул. Садовая, 8")
        _insert_row(c, "yandex", "105", price=12_200_000, area=61.0, rooms=2, floor=6, address="Ялта, ул. Чехова, 3")
        _insert_row(c, "avito", "106", price=12_300_000, area=66.0, rooms=2, floor=6, address="Ялта, ул. Чехова, 3")
        c.execute("INSERT INTO manual_merges VALUES('cian','101','grp-a',1,'2026-11-22T10:00:00')")
        c.execute("INSERT INTO manual_merges VALUES('avito','102','grp-a',0,'2026-11-22T10:00:00')")
        c.commit()
        dedupe.rebuild(c)
        c.execute("INSERT INTO likes VALUES('cian','101','2026-11-22T10:01:00')")
        c.execute("INSERT INTO likes VALUES('avito','102','2026-11-22T10:01:00')")
        c.execute("INSERT INTO dislikes VALUES('cian','103','2026-11-22T10:02:00')")
        c.execute("INSERT INTO notes VALUES('cian','101','Моя заметка А по 101','2026-11-22T10:03:00')")
        c.execute("INSERT INTO notes VALUES('avito','102','Моя заметка А по 101','2026-11-22T10:03:00')")
        c.commit()
        c.close()

        # Подготовка базы на Ноутбуке Б (свои лайки, в т.ч. на новую квартиру 999, дизлайк, заметки и ручная склейка 105+106)
        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "101", price=9_500_000, area=44.0, rooms=1, floor=3, address="Ялта, ул. Московская, 5")
            _insert_row(cb, "cian", "104", price=11_800_000, area=58.0, rooms=2, floor=5, address="Ялта, ул. Садовая, 8")
            _insert_row(cb, "yandex", "105", price=12_200_000, area=61.0, rooms=2, floor=6, address="Ялта, ул. Чехова, 3")
            _insert_row(cb, "avito", "106", price=12_300_000, area=66.0, rooms=2, floor=6, address="Ялта, ул. Чехова, 3")
            _insert_row(cb, "cian", "999", price=10_100_000, area=47.0, rooms=1, floor=7, address="Ялта, ул. Пушкинская, 9")
            cb.execute("INSERT INTO manual_merges VALUES('yandex','105','grp-b',1,'2026-11-22T11:00:00')")
            cb.execute("INSERT INTO manual_merges VALUES('avito','106','grp-b',0,'2026-11-22T11:00:00')")
            cb.commit()
            dedupe.rebuild(cb)
            # Б лайкнул 104 и 999 (которой ещё нет на А), а 101 дизлайкнул (на А 101 уже лайкнут!)
            cb.execute("INSERT INTO likes VALUES('cian','104','2026-11-22T11:01:00')")
            cb.execute("INSERT INTO likes VALUES('cian','999','2026-11-22T11:02:00')")
            cb.execute("INSERT INTO dislikes VALUES('cian','101','2026-11-22T11:03:00')")
            cb.execute("INSERT INTO dislikes VALUES('yandex','105','2026-11-22T11:04:00')")
            cb.execute("INSERT INTO dislikes VALUES('avito','106','2026-11-22T11:04:00')")
            # Заметка Б на 101 (где у А уже есть своя заметка!) и на 104 (где у А заметки нет)
            cb.execute("INSERT INTO notes VALUES('cian','101','Мнение Б: шумновато под окнами','2026-11-22T11:05:00')")
            cb.execute("INSERT INTO notes VALUES('cian','104','Отличная планировка и видовая терраса','2026-11-22T11:06:00')")
            cb.commit()
        finally:
            cb.close()

        # Проверяем тихий запрос счётчиков (/api/lan/marks/info)
        info = server.lan_marks_info(peer_id=peer_b_id)
        self.assertEqual(info["local"], {"likes": 1, "dislikes": 1, "notes": 1, "merges": 1})
        self.assertEqual(info["remote"], {"likes": 2, "dislikes": 2, "notes": 2, "merges": 1})

        # Снимаем текущий seq событий и inbox на Б до тихого запроса
        _, b_sum_before = _ui_call(self.peer_b.ui_port, "GET", "/api/status")
        b_seq_before = b_sum_before["lan"]["seq"]

        # 1) Тихий запрос ТОЛЬКО лайков и заметок в режиме "merge" («Добавить к моим»)
        r_sub = server.lan_pull_marks({"peer_id": peer_b_id, "items": ["likes", "notes"], "mode": "merge"})
        self.assertTrue(r_sub["ok"])
        self.assertEqual(r_sub["items"], ["likes", "notes"])
        self.assertIsNone(r_sub["backup"])

        # Убеждаемся, что Ноутбук Б НЕ потревожен (ни входящих запросов, ни событий уведомлений)
        _, b_sum_after = _ui_call(self.peer_b.ui_port, "GET", f"/api/status?lan_after={b_seq_before}")
        self.assertEqual(b_sum_after["lan"]["inbox_count"], 0)
        self.assertEqual(b_sum_after["lan"]["events"], [])

        c = con()
        try:
            # Лайки А (101, 102) сохранились + добавились 104 и догруженная 999 (imported_only=1)
            liked_sids = {r[0] for r in c.execute("SELECT source_id FROM likes")}
            self.assertEqual(liked_sids, {"101", "102", "104", "999"})
            row_999 = c.execute("SELECT imported_only FROM listings WHERE source='cian' AND source_id='999'").fetchone()
            self.assertIsNotNone(row_999)
            self.assertEqual(row_999[0], 1)
            # Дизлайки и ручные объединения пока НЕ запрашивались — остались прежними
            self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM dislikes")}, {"103"})
            self.assertEqual(len(dedupe.active_manual_merges(c)[0]), 1)
            # Заметка по 104 записалась в личные notes, а по 101 своя заметка А сохранилась и заметка Б легла в imported_notes!
            self.assertEqual(c.execute("SELECT text FROM notes WHERE source_id='104'").fetchone()[0], "Отличная планировка и видовая терраса")
            self.assertEqual(c.execute("SELECT text FROM notes WHERE source_id='101'").fetchone()[0], "Моя заметка А по 101")
            imp_n = c.execute("SELECT origin_name, text FROM imported_notes WHERE source_id='101'").fetchone()
            self.assertIsNotNone(imp_n)
            self.assertEqual(imp_n["origin_name"], "Ноутбук Б")
            self.assertIn("шумновато", imp_n["text"])
        finally:
            c.close()

        # 2) Тихий запрос ВСЕХ 4 пунктов (включая дизлайки и ручные объединения) в режиме "merge"
        r_all = server.lan_pull_marks({
            "peer_id": peer_b_id,
            "items": ["likes", "dislikes", "notes", "merges"],
            "mode": "merge",
        })
        self.assertTrue(r_all["ok"])
        c = con()
        try:
            # Оба ручных объединения активны: 101+102 (А) и 105+106 (Б)!
            by_grp, _ = dedupe.active_manual_merges(c)
            self.assertEqual(len(by_grp), 2)
            # Свой лайк А на 101 НЕ затёрся чужим дизлайком Б на 101, а дизлайк на объединённую 105+106 добавился!
            self.assertIn("101", {r[0] for r in c.execute("SELECT source_id FROM likes")})
            self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM dislikes")}, {"103", "105", "106"})
        finally:
            c.close()

        # 3) Тихий запрос ВСЕХ 4 пунктов в режиме "replace" («Перезаписать поверх моих»)
        r_rep = server.lan_pull_marks({
            "peer_id": peer_b_id,
            "items": ["likes", "dislikes", "notes", "merges"],
            "mode": "replace",
        })
        self.assertTrue(r_rep["ok"])
        self.assertTrue(Path(r_rep["backup"]).is_file())
        c = con()
        try:
            # Теперь отметки, заметки и ручные объединения полностью соответствуют Ноутбуку Б
            self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM likes")}, {"104", "999"})
            self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM dislikes")}, {"101", "105", "106"})
            self.assertEqual(c.execute("SELECT text FROM notes WHERE source_id='101'").fetchone()[0], "Мнение Б: шумновато под окнами")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM imported_notes").fetchone()[0], 0)
            by_grp_rep, _ = dedupe.active_manual_merges(c)
            self.assertEqual(len(by_grp_rep), 1)
            only_members = {sid for _, sid in next(iter(by_grp_rep.values()))}
            self.assertEqual(only_members, {"105", "106"})
        finally:
            c.close()

    def test_push_marks_triggers_notification_and_allows_merge_or_replace_on_recipient(self):
        my_a_id, peer_b_id = self._start_and_pair()

        # На Компьютере А готовим лайк, дизлайк, заметку и ручное объединение
        c = con()
        _insert_row(c, "cian", "501", price=10_000_000, area=46.0, rooms=1, floor=2, address="Ялта, ул. Руданского, 7")
        _insert_row(c, "avito", "502", price=10_100_000, area=51.0, rooms=1, floor=2, address="Ялта, ул. Руданского, 7")
        _insert_row(c, "cian", "503", price=11_500_000, area=54.0, rooms=2, floor=5, address="Ялта, ул. Ломоносова, 4")
        c.execute("INSERT INTO manual_merges VALUES('cian','501','m-501',1,'2026-11-22T12:00:00')")
        c.execute("INSERT INTO manual_merges VALUES('avito','502','m-501',0,'2026-11-22T12:00:00')")
        c.commit()
        dedupe.rebuild(c)
        c.execute("INSERT INTO likes VALUES('cian','501','2026-11-22T12:01:00')")
        c.execute("INSERT INTO likes VALUES('avito','502','2026-11-22T12:01:00')")
        c.execute("INSERT INTO dislikes VALUES('cian','503','2026-11-22T12:02:00')")
        c.execute("INSERT INTO notes VALUES('cian','501','Проверить документы','2026-11-22T12:03:00')")
        c.execute("INSERT INTO notes VALUES('avito','502','Проверить документы','2026-11-22T12:03:00')")
        c.commit()
        c.close()

        # На Ноутбуке Б есть свой лайк 777
        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "501", price=10_000_000, area=46.0, rooms=1, floor=2, address="Ялта, ул. Руданского, 7")
            _insert_row(cb, "avito", "502", price=10_100_000, area=51.0, rooms=1, floor=2, address="Ялта, ул. Руданского, 7")
            _insert_row(cb, "cian", "503", price=11_500_000, area=54.0, rooms=2, floor=5, address="Ялта, ул. Ломоносова, 4")
            _insert_row(cb, "cian", "777", price=9_200_000, area=42.0, rooms=1, floor=4, address="Ялта, ул. Кирова, 15")
            dedupe.rebuild(cb)
            cb.execute("INSERT INTO likes VALUES('cian','777','2026-11-22T09:00:00')")
            cb.commit()
        finally:
            cb.close()

        _, b_before = _ui_call(self.peer_b.ui_port, "GET", "/api/status")
        seq0 = b_before["lan"]["seq"]

        # Компьютер А отправляет (Push) все 4 категории на Ноутбук Б
        push_res = server.lan_send_marks({
            "peer_ids": [peer_b_id],
            "items": ["likes", "dislikes", "notes", "merges"],
        })
        self.assertTrue(push_res["ok"])
        self.assertEqual(push_res["counts"], {"likes": 1, "dislikes": 1, "notes": 1, "merges": 1})

        # На Ноутбуке Б пришло live-уведомление marks_offer И запрос появился во входящих с точным составом!
        _, b_after = _ui_call(self.peer_b.ui_port, "GET", f"/api/status?lan_after={seq0}")
        self.assertEqual(b_after["lan"]["inbox_count"], 1)
        offer_evs = [e for e in b_after["lan"]["events"] if e["kind"] == "marks_offer"]
        self.assertEqual(len(offer_evs), 1)
        self.assertEqual(offer_evs[0]["from_device_name"], "Компьютер А")
        self.assertEqual(offer_evs[0]["counts"], {"likes": 1, "dislikes": 1, "notes": 1, "merges": 1})

        _, b_lan_st = _ui_call(self.peer_b.ui_port, "GET", "/api/lan/status")
        req_b = b_lan_st["inbox"][0]
        self.assertEqual(req_b["kind"], "marks")
        self.assertEqual(req_b["items"], ["likes", "dislikes", "notes", "merges"])

        # Ноутбук Б принимает в режиме "merge" («Добавить к моим»)
        st_acc, acc_res = _ui_call(
            self.peer_b.ui_port,
            "POST",
            f"/api/lan/inbox/{req_b['req_id']}/accept",
            {"mode": "merge", "items": ["likes", "dislikes", "notes", "merges"]},
        )
        self.assertEqual(st_acc, 200)
        self.assertTrue(acc_res["ok"])

        cb = self.peer_b.db_connect()
        try:
            # Свой лайк 777 сохранился + добавился объединённый объект 501+502, дизлайк 503, заметка и ручная склейка!
            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM likes")}, {"777", "501", "502"})
            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM dislikes")}, {"503"})
            self.assertEqual(cb.execute("SELECT text FROM notes WHERE source_id='501'").fetchone()[0], "Проверить документы")
            self.assertEqual(len(dedupe.active_manual_merges(cb)[0]), 1)
        finally:
            cb.close()

        # Проверка защиты от неавторизованного запроса и битого архива
        r_unauth = _asgi_call(lan_sync.service.sync_app, "GET", "/sync/marks", client=("127.0.0.1", 50000))
        self.assertEqual(r_unauth.status_code, 401)
        with self.assertRaises(HTTPException) as ctx400:
            server.lan_pull_marks({"peer_id": peer_b_id, "items": [], "mode": "merge"})
        self.assertEqual(ctx400.exception.status_code, 400)

    def test_sync_history_records_spotlight_live_events_marks_and_dedupes_req_id(self):
        my_a_id, peer_b_id = self._start_and_pair()

        # На Компьютере А есть квартира 602, а квартиры 601 ещё нет в базе
        c = con()
        _insert_row(c, "cian", "602", price=11_400_000, area=53.0, rooms=2, floor=4, address="Ялта, ул. Парковая, 6")
        dedupe.rebuild(c)
        c.close()

        # На Ноутбуке Б есть 601 (которой нет на А), 602 и 603
        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "601", price=9_700_000, area=43.0, rooms=1, floor=3, address="Ялта, ул. Морская, 2")
            _insert_row(cb, "cian", "602", price=11_400_000, area=53.0, rooms=2, floor=4, address="Ялта, ул. Парковая, 6")
            _insert_row(cb, "avito", "603", price=13_100_000, area=68.0, rooms=2, floor=7, address="Ялта, ул. Дражинского, 11")
            dedupe.rebuild(cb)
            obj_601 = cb.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id='601'").fetchone()[0]
            cb.execute("INSERT INTO likes VALUES('cian','602','2026-11-22T14:00:00')")
            cb.execute("INSERT INTO dislikes VALUES('avito','603','2026-11-22T14:01:00')")
            cb.commit()
        finally:
            cb.close()

        # 1) Ноутбук Б пингует (Spotlight) карточку 601 на Компьютер А
        st_sp, sp_res = _ui_call(
            self.peer_b.ui_port,
            "POST",
            "/api/lan/spotlight",
            {"object_id": obj_601, "peer_ids": [my_a_id]},
        )
        self.assertEqual(st_sp, 200)
        self.assertTrue(sp_res["ok"])

        hist1 = server.lan_history(limit=50)
        self.assertEqual(hist1["unread"], 1)
        self.assertEqual(len(hist1["items"]), 1)
        sp_item = hist1["items"][0]
        self.assertEqual(sp_item["kind"], "spotlight")
        self.assertEqual(sp_item["peer_name"], "Ноутбук Б")
        self.assertEqual(sp_item["keys"], [["cian", "601"]])
        self.assertFalse(sp_item["seen"])
        # Даже до импорта карточки в превью истории уже есть адрес, цена и прямая ссылка на сайт!
        self.assertEqual(len(sp_item["preview"]), 1)
        self.assertIn("Морская, 2", sp_item["preview"][0]["address"])
        self.assertIn("601", sp_item["preview"][0]["url"])

        # 2) Включаем live_notes на обеих сторонах и ставим заметку на 602 -> попадает в sync_history на А
        server.lan_peer_live_config(peer_b_id, {"live_notes": True})
        st_lc, _ = _ui_call(
            self.peer_b.ui_port,
            "POST",
            f"/api/lan/peers/{my_a_id}/live-config",
            {"live_notes": True},
        )
        self.assertEqual(st_lc, 200)
        st_nt, _ = _ui_call(
            self.peer_b.ui_port,
            "POST",
            "/api/note",
            {"keys": ["cian:602"], "text": "Солнечная сторона и тихий двор"},
        )
        self.assertEqual(st_nt, 200)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            cur_h = server.lan_history(limit=50)
            if any(x["kind"] == "live_note" for x in cur_h["items"]):
                break
            time.sleep(0.1)
        hist2 = server.lan_history(limit=50)
        note_items = [x for x in hist2["items"] if x["kind"] == "live_note"]
        self.assertEqual(len(note_items), 1)
        self.assertEqual(note_items[0]["cats"].get("notes"), [["cian", "602"]])
        self.assertIn("Солнечная сторона", note_items[0]["preview"][0]["note"])

        # 3) Ноутбук Б пушит пакет отметок (лайки, дизлайки, заметки) -> запись marks_offer с req_id и pending_inbox=True
        st_pm, _ = _ui_call(
            self.peer_b.ui_port,
            "POST",
            "/api/lan/send/marks",
            {"peer_ids": [my_a_id], "items": ["likes", "dislikes", "notes"]},
        )
        self.assertEqual(st_pm, 200)
        hist3 = server.lan_history(limit=50)
        self.assertEqual(len(hist3["items"]), 3)
        offer_item = hist3["items"][0]
        self.assertEqual(offer_item["kind"], "marks_offer")
        self.assertTrue(offer_item["pending_inbox"])
        self.assertTrue(offer_item["req_id"])
        self.assertEqual(offer_item["cats"]["likes"], [["cian", "602"]])
        self.assertEqual(offer_item["cats"]["dislikes"], [["avito", "603"]])

        # Принимаем пакет по req_id -> та же самая строка обновляется в marks_applied без дублирования!
        acc = server.lan_inbox_accept(
            offer_item["req_id"],
            {"mode": "merge", "items": ["likes", "dislikes", "notes"]},
        )
        self.assertTrue(acc["ok"])
        hist4 = server.lan_history(limit=50)
        self.assertEqual(len(hist4["items"]), 3)
        applied_item = next(x for x in hist4["items"] if x["req_id"] == offer_item["req_id"])
        self.assertEqual(applied_item["id"], offer_item["id"])
        self.assertEqual(applied_item["kind"], "marks_applied")
        self.assertFalse(applied_item["pending_inbox"])

        # 4) Пометка прочитанным и очистка истории
        seen_res = server.lan_history_seen({})
        self.assertEqual(seen_res["unread"], 0)
        self.assertEqual(lan_sync.summary()["history_unread"], 0)
        self.assertTrue(all(x["seen"] for x in server.lan_history(limit=50)["items"]))

        clr_res = server.lan_history_clear()
        self.assertEqual(clr_res["unread"], 0)
        self.assertEqual(server.lan_history(limit=50)["items"], [])


class LanMarksBrowserUITests(Case):
    """Проверка окна импорта/экспорта отметок и приёма входящего предложения в Chromium."""

    def setUp(self):
        super().setUp()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        self._orig_bind_host = lan_sync.service._bind_host
        lan_sync.service._bind_host = "127.0.0.1"
        self.peer_b = None

    def tearDown(self):
        if self.peer_b is not None:
            self.peer_b.stop()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        lan_sync.service._bind_host = self._orig_bind_host
        super().tearDown()

    @unittest.skipUnless(has_browser(), "Chromium не установлен (setup.bat)")
    def test_import_export_marks_modal_and_incoming_push_dialog_in_browser(self):
        port_lan_a = _free_port()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": port_lan_a,
            "auto_discovery": False,
            "device_id": "",
            "device_name": "Основной ПК",
        }
        settings.save(cfg)

        c = con()
        my_a_id = lan_sync.service._identity(c)["device_id"]
        _insert_row(c, "cian", "810", price=9_900_000, area=45.0, rooms=1, floor=3, address="Ялта, ул. Набережная, 1")
        _insert_row(c, "cian", "820", price=11_200_000, area=52.0, rooms=2, floor=4, address="Ялта, ул. Гоголя, 10")
        c.execute("INSERT INTO likes VALUES('cian','810','2026-11-22T10:00:00')")
        c.commit()
        dedupe.rebuild(c)
        c.close()
        self.assertTrue(lan_sync.start_if_enabled())

        self.peer_b = _PeerProc(self.dir / "peer_b_ui", name="Компьютер Партнёра", auto_discovery=False).start()
        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "820", price=11_200_000, area=52.0, rooms=2, floor=4, address="Ялта, ул. Гоголя, 10")
            _insert_row(cb, "cian", "830", price=12_400_000, area=60.0, rooms=2, floor=6, address="Ялта, ул. Севастопольская, 5")
            _insert_row(cb, "avito", "831", price=12_500_000, area=64.0, rooms=2, floor=6, address="Ялта, ул. Севастопольская, 5")
            _insert_row(cb, "cian", "840", price=10_700_000, area=48.0, rooms=1, floor=5, address="Ялта, ул. Приморская, 14")
            cb.execute("INSERT INTO manual_merges VALUES('cian','830','ui-grp',1,'2026-11-22T11:00:00')")
            cb.execute("INSERT INTO manual_merges VALUES('avito','831','ui-grp',0,'2026-11-22T11:00:00')")
            cb.commit()
            dedupe.rebuild(cb)
            obj_840_id = cb.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id='840'").fetchone()[0]
            cb.execute("INSERT INTO likes VALUES('cian','820','2026-11-22T11:01:00')")
            cb.execute("INSERT INTO dislikes VALUES('cian','830','2026-11-22T11:02:00')")
            cb.execute("INSERT INTO dislikes VALUES('avito','831','2026-11-22T11:02:00')")
            cb.execute("INSERT INTO notes VALUES('cian','820','Заметка из браузерного теста','2026-11-22T11:03:00')")
            cb.commit()
        finally:
            cb.close()

        _, pin_resp = _ui_call(self.peer_b.ui_port, "POST", "/api/lan/pin", {})
        server.lan_pair({"ip": "127.0.0.1", "port": self.peer_b.lan_port, "pin": pin_resp["pin"]})

        dummy_png = _make_png()
        patch_tile = patch.object(tiles, "_fetch_url", return_value=(200, dummy_png))
        patch_upd = patch.object(updater, "_fetch_bytes", return_value=(404, b""))
        patch_tile.start()
        patch_upd.start()
        self.addCleanup(patch_tile.stop)
        self.addCleanup(patch_upd.stop)

        ui_port = _free_port()
        uv_srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=ui_port, log_level="error"))
        t = threading.Thread(target=uv_srv.run, daemon=True)
        t.start()
        self.addCleanup(lambda: setattr(uv_srv, "should_exit", True) or t.join(timeout=5.0))
        deadline = time.time() + 8.0
        while not uv_srv.started and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(uv_srv.started)

        b = LocalBrowser("ui-lan-marks", lambda h, u: False, lambda m: None, None, window="hidden")
        self.addCleanup(b.close)
        b.start()
        page = b.page
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"http://127.0.0.1:{ui_port}/", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => typeof SETTINGS === 'object' && SETTINGS !== null && view === 'dashboard'",
            timeout=10000,
        )

        # 1) Открываем окно «Сеть» -> жмём «⬇ Запросить отметки…» -> проверяем все 4 чекбокса и режим «Добавить к моим»
        page.click("#lanBtn")
        page.wait_for_selector(".lan-pull-marks", state="visible", timeout=5000)
        page.click(".lan-pull-marks")
        page.wait_for_selector("#lanMarksWin #btnLanMarksGo", state="visible", timeout=5000)

        for mk in ("likes", "dislikes", "notes", "merges"):
            self.assertTrue(page.is_checked(f"#lmChk_{mk}"))
        # Ждём подгрузки счётчиков партнёра в окно
        page.wait_for_function(
            "() => document.getElementById('lanMarksWin')?.textContent.includes('у партнёра: 1')",
            timeout=5000,
        )
        # Запрашиваем только лайки и заметки в режиме «Добавить к моим»
        page.uncheck("#lmChk_dislikes")
        page.uncheck("#lmChk_merges")
        page.evaluate("() => document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")
        page.click("#btnLanMarksGo")
        page.wait_for_function("() => document.getElementById('modal').classList.contains('hidden')", timeout=5000)

        c = con()
        try:
            self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM likes")}, {"810", "820"})
            self.assertEqual(c.execute("SELECT text FROM notes WHERE source_id='820'").fetchone()[0], "Заметка из браузерного теста")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM manual_merges").fetchone()[0], 0)
        finally:
            c.close()

        # 2) Теперь Партнёр Б пушит дизлайки и ручные объединения -> на А всплывает уведомление с кнопкой «Посмотреть и выбрать…»
        st_push, _ = _ui_call(
            self.peer_b.ui_port,
            "POST",
            "/api/lan/send/marks",
            {"peer_ids": [my_a_id], "items": ["dislikes", "merges"]},
        )
        self.assertEqual(st_push, 200)

        # Фоновый опрос страницы (каждые 2 с) сам подхватывает событие marks_offer и показывает уведомление
        page.wait_for_selector("#toasts .toast .toast-act", state="visible", timeout=5000)
        self.assertIn("Предложение обновить отметки", page.text_content("#toasts"))
        page.click("#toasts .toast .toast-act")
        page.wait_for_selector("#lanAcceptMarksWin #btnLanAcceptGo", state="visible", timeout=5000)
        self.assertIn("Компьютер Партнёра", page.text_content("#lanAcceptMarksWin"))

        page.evaluate("() => document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")
        page.click("#btnLanAcceptGo")
        page.wait_for_function("() => document.getElementById('modal').classList.contains('hidden')", timeout=5000)

        c = con()
        try:
            self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM dislikes")}, {"830", "831"})
            self.assertEqual(len(dedupe.active_manual_merges(c)[0]), 1)
        finally:
            c.close()

        # 3) Партнёр Б пингует новую карточку 840 (которой пока нет на Основном ПК) -> проверяем значок и панель истории справа!
        st_sp, _ = _ui_call(
            self.peer_b.ui_port,
            "POST",
            "/api/lan/spotlight",
            {"object_id": obj_840_id, "peer_ids": [my_a_id]},
        )
        self.assertEqual(st_sp, 200)

        # Ждём появления непрочитанного бейджа на значке истории в верхней шапке
        page.wait_for_function(
            "() => { const b = document.getElementById('lanHistBadge'); return b && !b.classList.contains('hidden') && Number(b.textContent) >= 1; }",
            timeout=5000,
        )
        page.click("#lanHistBtn")
        page.wait_for_selector("#lanDrawer.open .lh-card", state="visible", timeout=5000)
        # После открытия выезжающей панели бейдж непрочитанных сбрасывается
        page.wait_for_function(
            "() => document.getElementById('lanHistBadge').classList.contains('hidden')",
            timeout=5000,
        )

        drawer_txt = page.text_content("#lanDrawer")
        self.assertIn("Компьютер Партнёра", drawer_txt)
        self.assertIn("Приморская, 14", drawer_txt)
        self.assertIn("Севастопольская, 5", drawer_txt)
        self.assertIn("Заметка из браузерного теста", drawer_txt)
        # Проверяем наличие прямой внешней ссылки «Сайт ↗» прямо из истории
        self.assertGreaterEqual(page.locator("#lanDrawer a.lh-ext").count(), 1)

        # 3a) Кликаем чип «👎 дизлайки» в истории: панель закрывается, открывается «Подборка» с плашкой фокуса,
        # и скрытая дизлайком карточка (Севастопольская, 5) видна на экране несмотря на включённый #fNoDis!
        self.assertTrue(page.is_checked("#fNoDis"))
        page.click('#lanDrawer [data-lh-cat="dislikes"]')
        page.wait_for_selector("#eventFocusBar:not(.hidden)", state="visible", timeout=5000)
        self.assertIn("Показаны объекты из истории сети", page.text_content("#eventFocusBar"))
        self.assertIn("Севастопольская, 5", page.text_content("#cardsList"))

        # Сбрасываем фокус события — обычный фильтр #fNoDis снова скрывает дизлайкнутую карточку
        page.click("#btnClearEventFocus")
        page.wait_for_function(
            "() => document.getElementById('eventFocusBar').classList.contains('hidden')",
            timeout=5000,
        )
        self.assertNotIn("Севастопольская, 5", page.text_content("#cardsList"))

        # 3b) Снова открываем панель истории и кликаем на входящий пинг карточки 840 (которой не было в локальной базе) ->
        # карточка автоматически догружается у партнёра и подсвечивается в «Подборке»!
        page.click("#lanHistBtn")
        page.wait_for_selector("#lanDrawer.open .lh-card", state="visible", timeout=5000)
        page.click('#lanDrawer .lh-card:first-child [data-lh-go]')
        page.wait_for_function(
            "() => document.getElementById('cardsList')?.textContent.includes('Приморская, 14')",
            timeout=5000,
        )
        self.assertGreaterEqual(page.locator("#cardsList .card.spotlighted").count(), 1)

        self.assertEqual(js_errors, [], f"ошибки JS в браузере: {js_errors}")


if __name__ == "__main__":
    unittest.main()
