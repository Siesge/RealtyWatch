"""Тесты Этапа 3: LAN-сервер /sync/*, IP/Bearer-безопасность, одноразовый PIN,
сопряжение, heartbeat и передача карточек, Избранного (merge/replace) и полной базы
с дельтой фото между двумя реальными локальными узлами."""
import asyncio
import http.client
import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import db, dedupe, exchange, images, lan_sync, paths, runlog, server, settings, transfer
from tests.support import Case, con


def _asgi_call(asgi_app, method: str, path: str, *, client=("127.0.0.1", 50000),
               headers: dict | None = None, json_body: dict | None = None, raw_body: bytes | None = None):
    if raw_body is not None:
        payload = raw_body
    elif json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
    else:
        payload = b""
    hdr_dict = {k.lower(): str(v) for k, v in (headers or {}).items()}
    if json_body is not None and raw_body is None:
        hdr_dict.setdefault("content-type", "application/json")
        hdr_dict.setdefault("content-length", str(len(payload)))
    hdr_list = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in hdr_dict.items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": hdr_list,
        "client": client,
        "server": ("127.0.0.1", 8766),
    }
    sent = {"done": False}
    status_box = {"code": 500, "chunks": []}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            status_box["code"] = msg["status"]
        elif msg["type"] == "http.response.body":
            status_box["chunks"].append(msg.get("body", b""))

    asyncio.run(asgi_app(scope, receive, send))
    txt = b"".join(status_box["chunks"]).decode("utf-8", errors="replace")
    try:
        data = json.loads(txt) if txt else {}
    except Exception:
        data = {"raw": txt}
    return SimpleNamespace(status_code=status_box["code"], data=data, text=txt)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _insert_row(c, source, sid, *, price=10_000_000, area=50.0, rooms=2, floor=3, floors=9,
                lat=44.49, lon=34.15, address="Ялта, ул. Чехова, 10", last_seen="2026-11-19T10:00:00",
                first_seen="2026-11-18T10:00:00", active=1, imported_only=0, excluded=None):
    c.execute(
        """INSERT INTO listings(
               source, source_id, url, title, price, price_m2, rooms, area, floor, floors,
               address, lat, lon, zone, renovation, description, flags, excluded,
               cluster_key, raw, first_seen, last_seen, active, imported_only
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source, str(sid), f"https://example.test/{source}/{sid}",
            f"{rooms}-к. квартира, {area:g} м²", price, round(price / area),
            rooms, area, floor, floors, address, lat, lon, "A", "euro",
            "Тестовое объявление", "{}", excluded, f"{source}:{sid}", "{}",
            first_seen, last_seen, active, imported_only,
        ),
    )


def _ui_call(port: int, method: str, path: str, body: dict | None = None, timeout: float = 15.0):
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdrs = {"Connection": "close"}
    if raw is not None:
        hdrs["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=raw, headers=hdrs)
        resp = conn.getresponse()
        txt = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(txt) if txt else {}
        except Exception:
            data = {"raw": txt}
        return resp.status, data
    finally:
        conn.close()


class _PeerProc:
    """Второй изолированный процесс RealtyWatch (UI + LAN-сервер) для честных тестов A <-> B."""

    def __init__(self, workdir: Path, name: str = "Узел Б", auto_discovery: bool = False):
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ui_port = _free_port()
        self.lan_port = _free_port()
        self.name = name
        self.auto_discovery = auto_discovery
        self.proc = None

    def start(self):
        env = os.environ.copy()
        env["RW_PEER_DIR"] = str(self.dir)
        env["RW_PEER_UI_PORT"] = str(self.ui_port)
        env["RW_PEER_LAN_PORT"] = str(self.lan_port)
        env["RW_PEER_NAME"] = self.name
        env["RW_PEER_AUTO_DISC"] = "1" if self.auto_discovery else "0"
        root_win = str(Path(__file__).resolve().parent.parent)
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _CHILD_RUNNER],
            cwd=root_win,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                out, err = self.proc.communicate()
                raise RuntimeError(f"Peer B завершился досрочно: {out.decode(errors='replace')} {err.decode(errors='replace')}")
            try:
                st, data = _ui_call(self.ui_port, "GET", "/api/lan/status", timeout=1.0)
                if st == 200 and data.get("listening"):
                    return self
            except Exception:
                pass
            time.sleep(0.1)
        raise TimeoutError("Peer B не запустил LAN-сервер за 12 с")

    def db_connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.dir / "realty.db", timeout=10.0)
        c.row_factory = sqlite3.Row
        return c

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        for stream in (getattr(self.proc, "stdout", None), getattr(self.proc, "stderr", None)) if self.proc else ():
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


_CHILD_RUNNER = r'''
import os
from pathlib import Path
import uvicorn
from app import db, exchange, images, lan_sync, runlog, server, settings, transfer

d = Path(os.environ["RW_PEER_DIR"])
d.mkdir(parents=True, exist_ok=True)
db.DB_FILE = d / "realty.db"
settings.SETTINGS_FILE = d / "settings.json"
images.IMAGES = transfer.IMAGES = d / "images"
transfer.BACKUPS = d / "backups"
transfer.DB_DIR = d
exchange.IMPORT_IMAGES = d / "import_images"
runlog.LOGS_DIR = d / "logs"

cfg = settings.load()
cfg["lan"] = {
    "enabled": True,
    "port": int(os.environ["RW_PEER_LAN_PORT"]),
    "auto_discovery": os.environ.get("RW_PEER_AUTO_DISC") == "1",
    "device_id": "",
    "device_name": os.environ["RW_PEER_NAME"],
}
settings.save(cfg)
c = db.connect()
c.close()

lan_sync.service._bind_host = "127.0.0.1"
lan_sync.start_if_enabled()
try:
    srv = uvicorn.Server(uvicorn.Config(
        server.app, host="127.0.0.1", port=int(os.environ["RW_PEER_UI_PORT"]),
        log_level="warning", access_log=False,
    ))
    srv.install_signal_handlers = lambda: None
    srv.run()
finally:
    lan_sync.stop()
'''


class LanSecurityAndUnitTests(Case):
    def setUp(self):
        super().setUp()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()

    def tearDown(self):
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        super().tearDown()

    def test_lan_ip_guard_auth_and_pin_lockout(self):
        self.assertTrue(lan_sync._is_lan_ip("192.168.1.25"))
        self.assertTrue(lan_sync._is_lan_ip("10.10.0.5"))
        self.assertTrue(lan_sync._is_lan_ip("172.16.4.2"))
        self.assertTrue(lan_sync._is_lan_ip("127.0.0.1"))
        self.assertTrue(lan_sync._is_lan_ip("::ffff:192.168.0.10"))
        self.assertFalse(lan_sync._is_lan_ip("8.8.8.8"))
        self.assertFalse(lan_sync._is_lan_ip("0.0.0.0"))
        self.assertFalse(lan_sync._is_lan_ip("not-an-ip"))

        # Не-LAN адрес режется на уровне middleware с HTTP 403
        r403 = _asgi_call(lan_sync.service.sync_app, "GET", "/sync/status", client=("8.8.8.8", 50000))
        self.assertEqual(r403.status_code, 403)
        r403_pair = _asgi_call(lan_sync.service.sync_app, "POST", "/sync/pair", client=("8.8.8.8", 50000), json_body={})
        self.assertEqual(r403_pair.status_code, 403)

        # LAN IP без заголовков авторизации -> 401
        r401 = _asgi_call(lan_sync.service.sync_app, "GET", "/sync/status", client=("192.168.1.40", 50000))
        self.assertEqual(r401.status_code, 401)
        r401_bad = _asgi_call(
            lan_sync.service.sync_app, "GET", "/sync/status",
            client=("192.168.1.40", 50000),
            headers={"X-RW-Device": "unknown", "Authorization": "Bearer bad"},
        )
        self.assertEqual(r401_bad.status_code, 401)

        # Брутфорс PIN: 5 неверных попыток -> 429 и полный сброс PIN
        pin_info = lan_sync.create_pin()
        real_pin = pin_info["pin"]
        wrong_pin = "000000" if real_pin != "000000" else "999999"
        body_tpl = {"device_id": "dev-x", "device_name": "Peer X", "port": 8766, "token": "tok-x"}
        for attempt in range(4):
            r = _asgi_call(
                lan_sync.service.sync_app, "POST", "/sync/pair",
                client=("192.168.1.40", 50000), json_body={**body_tpl, "pin": wrong_pin},
            )
            self.assertEqual(r.status_code, 403, f"attempt {attempt}: {r.text}")
        r5 = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/pair",
            client=("192.168.1.40", 50000), json_body={**body_tpl, "pin": wrong_pin},
        )
        self.assertEqual(r5.status_code, 429)
        self.assertFalse(lan_sync.service.pin_status()["active"])

        # Даже с исходным верным PIN после сброса -> 403
        r6 = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/pair",
            client=("192.168.1.40", 50000), json_body={**body_tpl, "pin": real_pin},
        )
        self.assertEqual(r6.status_code, 403)

        # Просроченный PIN отклоняется
        pin_exp = lan_sync.create_pin()
        with lan_sync.service._lock:
            lan_sync.service._pin_state["expires_at"] = time.time() - 1
        r_exp = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/pair",
            client=("192.168.1.40", 50000), json_body={**body_tpl, "pin": pin_exp["pin"]},
        )
        self.assertEqual(r_exp.status_code, 403)


class LanDualPeerIntegrationTests(Case):
    def setUp(self):
        super().setUp()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        self.port_a = _free_port()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": self.port_a,
            "auto_discovery": False,
            "device_id": "",
            "device_name": "Комп А",
        }
        settings.save(cfg)
        c = con()
        c.close()
        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())

        self.peer_b = _PeerProc(self.dir / "peer_b", name="Mac Б").start()

    def tearDown(self):
        self.peer_b.stop()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        super().tearDown()

    def _pair_ab(self):
        st, pin_resp = _ui_call(self.peer_b.ui_port, "POST", "/api/lan/pin", {})
        self.assertEqual(st, 200)
        pin = pin_resp["pin"]
        pair_res = lan_sync.pair_with("127.0.0.1", self.peer_b.lan_port, pin)
        self.assertTrue(pair_res["ok"])
        # Одноразовый PIN больше не сработает
        with self.assertRaises(ValueError):
            lan_sync.pair_with("127.0.0.1", self.peer_b.lan_port, pin)
        return pair_res["device_id"]

    def test_pairing_heartbeat_cards_and_favorites_merge_replace(self):
        peer_b_id = self._pair_ab()

        # Взаимные записи в sync_peers + heartbeat онлайн
        hb = lan_sync.service.poll_peers_once()
        self.assertTrue(hb[peer_b_id]["online"])
        st_a = server.lan_status(refresh=True)
        self.assertEqual(st_a["online_peers"], 1)
        self.assertEqual(st_a["peers"][0]["name"], "Mac Б")

        # Готовим карточки и фото на A
        c = con()
        _insert_row(c, "cian", "1001", price=10_500_000, area=52.0, address="Ялта, ул. Чехова, 10")
        _insert_row(c, "cian", "1002", price=14_000_000, area=70.0, rooms=3, address="Ялта, ул. Кирова, 20")
        c.execute("INSERT INTO likes VALUES('cian','1001','2026-11-19T10:05:00')")
        c.execute("INSERT INTO notes VALUES('cian','1001','Окна в тихий сад','2026-11-19T10:06:00')")
        c.commit()
        dedupe.rebuild(c)
        oid1 = c.execute("SELECT object_id FROM listings WHERE source_id='1001'").fetchone()[0]
        oid2 = c.execute("SELECT object_id FROM listings WHERE source_id='1002'").fetchone()[0]
        c.close()

        img_a = images.IMAGES / "cian" / "1001.jpg"
        img_a.parent.mkdir(parents=True, exist_ok=True)
        img_a.write_bytes(b"IMG_1001_BYTES" * 100)

        # 1) Отправка выбранных карточек A -> B и приём с likes_mode='preserve'
        send_res = server.lan_send_cards({"peer_ids": [peer_b_id], "object_ids": [oid1, oid2]})
        self.assertTrue(send_res["ok"])
        self.assertEqual(send_res["cards"], 2)

        st_code, b_stat = _ui_call(self.peer_b.ui_port, "GET", "/api/lan/status")
        self.assertEqual(st_code, 200)
        self.assertEqual(b_stat["inbox_count"], 1)
        req_cards = b_stat["inbox"][0]
        self.assertEqual(req_cards["kind"], "cards")

        st_acc, acc = _ui_call(
            self.peer_b.ui_port, "POST", f"/api/lan/inbox/{req_cards['req_id']}/accept",
            {"likes_mode": "preserve", "apply_manual_merges": True},
        )
        self.assertEqual(st_acc, 200)
        self.assertEqual(acc["new_cards"], 2)

        cb = self.peer_b.db_connect()
        try:
            batch = cb.execute("SELECT * FROM import_batches ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(batch["channel"], "lan")
            self.assertEqual(batch["origin_name"], "Комп А")
            liked_b = {r[0] for r in cb.execute("SELECT source_id FROM likes")}
            self.assertEqual(liked_b, {"1001"})
            inote = cb.execute("SELECT text, origin_name FROM imported_notes WHERE source_id='1001'").fetchone()
            self.assertEqual(inote["text"], "Окна в тихий сад")
            self.assertEqual(inote["origin_name"], "Комп А")
        finally:
            cb.close()
        self.assertTrue((self.peer_b.dir / "images" / "cian" / "1001.jpg").is_file())

        # 2) Отправка Избранного A -> B: сначала merge, затем replace
        # У B есть собственная понравившаяся карточка 2001
        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "2001", price=9_200_000, area=44.0, address="Ялта, ул. Садовая, 3")
            cb.execute("INSERT INTO likes VALUES('cian','2001','2026-11-19T10:10:00')")
            cb.commit()
        finally:
            cb.close()

        # На A в Избранное добавлена и 1002
        c = con()
        c.execute("INSERT OR IGNORE INTO likes VALUES('cian','1002','2026-11-19T10:15:00')")
        c.commit()
        c.close()

        fav_send = server.lan_send_favorites({"peer_ids": [peer_b_id]})
        self.assertTrue(fav_send["ok"])
        _, b_stat = _ui_call(self.peer_b.ui_port, "GET", "/api/lan/status")
        req_fav1 = b_stat["inbox"][0]
        self.assertEqual(req_fav1["kind"], "favorites")
        st_f1, _ = _ui_call(
            self.peer_b.ui_port, "POST", f"/api/lan/inbox/{req_fav1['req_id']}/accept",
            {"favorites_mode": "merge"},
        )
        self.assertEqual(st_f1, 200)
        cb = self.peer_b.db_connect()
        try:
            liked_merge = {r[0] for r in cb.execute("SELECT source_id FROM likes")}
            self.assertEqual(liked_merge, {"1001", "1002", "2001"})
        finally:
            cb.close()

        # Теперь на A остаётся только 1001 (уже существующий на B!), отправка Избранного -> B принимает с replace
        c = con()
        c.execute("DELETE FROM likes WHERE source_id='1002'")
        c.commit()
        c.close()
        fav_send2 = server.lan_send_favorites({"peer_ids": [peer_b_id]})
        self.assertTrue(fav_send2["ok"])
        _, b_stat = _ui_call(self.peer_b.ui_port, "GET", "/api/lan/status")
        req_fav2 = b_stat["inbox"][0]
        st_f2, f2_res = _ui_call(
            self.peer_b.ui_port, "POST", f"/api/lan/inbox/{req_fav2['req_id']}/accept",
            {"favorites_mode": "replace"},
        )
        self.assertEqual(st_f2, 200)
        self.assertTrue(Path(f2_res["backup"]).is_file())
        cb = self.peer_b.db_connect()
        try:
            liked_replace = {r[0] for r in cb.execute("SELECT source_id FROM likes")}
            self.assertEqual(liked_replace, {"1001"})
        finally:
            cb.close()

    def test_full_db_sync_photo_delta_busy_409_and_personal_layer_preservation(self):
        peer_b_id = self._pair_ab()

        # На A: свежий сбор + 3 объявления + 2 фото + ручное объединение + свой фильтр price_max
        cfg_a = settings.load()
        cfg_a["filters"]["price_max"] = 22_000_000
        settings.save(cfg_a)

        c = con()
        c.execute(
            "INSERT INTO runs(source, mode, started, finished, status, found, new) VALUES(?,?,?,?,?,?,?)",
            ("cian", "full", "2026-11-19T12:00:00", "2026-11-19T12:05:00", "done", 3, 3),
        )
        _insert_row(c, "cian", "3001", price=11_000_000, area=55.0, last_seen="2026-11-19T12:05:00")
        _insert_row(c, "cian", "3002", price=12_500_000, area=60.0, last_seen="2026-11-19T12:05:00")
        _insert_row(c, "cian", "3003", price=13_000_000, area=62.0, last_seen="2026-11-19T12:05:00")
        c.execute("INSERT INTO manual_merges VALUES('cian','3001','a-grp-1',1,'2026-11-19T12:05:00')")
        c.execute("INSERT INTO manual_merges VALUES('cian','3002','a-grp-1',0,'2026-11-19T12:05:00')")
        c.commit()
        dedupe.rebuild(c)
        c.close()

        (images.IMAGES / "cian").mkdir(parents=True, exist_ok=True)
        (images.IMAGES / "cian" / "3001.jpg").write_bytes(b"SHARED_3001_" * 150)
        (images.IMAGES / "cian" / "3002.webp").write_bytes(b"DELTA_3002_" * 150)

        # На B уже есть: общее фото 3001.jpg + свой богатый личный слой + импортированная карточка 9001
        b_img_dir = self.peer_b.dir / "images" / "cian"
        b_img_dir.mkdir(parents=True, exist_ok=True)
        (b_img_dir / "3001.jpg").write_bytes(b"EXISTING_LOCAL_3001_" * 100)

        cb = self.peer_b.db_connect()
        try:
            b_dev_id = cb.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]
            _insert_row(cb, "cian", "3001", price=10_800_000, area=55.0, last_seen="2026-11-18T09:00:00")
            _insert_row(cb, "cian", "3003", price=13_000_000, area=62.0, last_seen="2026-11-18T09:00:00")
            _insert_row(cb, "cian", "9001", price=8_900_000, area=40.0, imported_only=1, last_seen="2026-11-18T11:00:00")
            cb.execute("INSERT INTO likes VALUES('cian','3001','2026-11-18T12:00:00')")
            cb.execute("INSERT INTO likes VALUES('cian','9001','2026-11-18T12:01:00')")
            cb.execute("INSERT INTO dislikes VALUES('cian','3003','2026-11-18T12:02:00')")
            cb.execute("INSERT INTO notes VALUES('cian','3001','Личная заметка Б по 3001','2026-11-18T12:03:00')")
            cb.execute("INSERT INTO notes VALUES('cian','9001','Личная заметка Б по 9001','2026-11-18T12:04:00')")
            cb.execute("INSERT INTO imported_notes VALUES('cian','3001','peer-x','Коллега Анна','Хороший ремонт','2026-11-18T12:05:00','2026-11-18T12:05:00',1)")
            cb.execute("INSERT INTO import_batches(id,ts,channel,origin_id,origin_name,likes_mode,total_listings,total_cards) VALUES(1,'2026-11-18T11:00:00','file','peer-x','Анна','preserve',1,1)")
            cb.execute("INSERT INTO import_items(batch_id,export_card_id,source,source_id,status) VALUES(1,'c1','cian','9001','new')")
            cb.execute("INSERT INTO manual_merges VALUES('cian','3001','b-grp-1',1,'2026-11-18T12:06:00')")
            cb.execute("INSERT INTO manual_merges VALUES('cian','3003','b-grp-1',0,'2026-11-18T12:06:00')")
            cb.execute("INSERT INTO sync_peers(device_id,name,ip,port) VALUES('extra-peer','Запасной ПК','192.168.1.99',8766)")
            cb.commit()
        finally:
            cb.close()

        # Отправляем полную базу A -> B: 3001.jpg должна пропуститься, 3002.webp — докачаться
        db_res = server.lan_send_db({"peer_id": peer_b_id})
        self.assertTrue(db_res["ok"])
        self.assertEqual(db_res["total_images"], 2)
        self.assertEqual(db_res["sent_images"], 1)
        self.assertEqual(db_res["skipped_images"], 1)

        _, b_stat = _ui_call(self.peer_b.ui_port, "GET", "/api/lan/status")
        req_db = b_stat["inbox"][0]
        self.assertEqual(req_db["kind"], "db")
        self.assertEqual(req_db["images_received"], 1)

        # Защита от приёма полной базы во время активного сбора -> 409
        with patch.object(server.lan_sync.runner.manager, "any_running", return_value=True):
            # Ставим локально копию в очередь inbox и убеждаемся, что сервер отвечает HTTP 409
            lan_sync.service._ensure_inbox_loaded()
            req_fake = {"req_id": "busy-chk", "kind": "db", "db_path": str(self.dir / "nope.db")}
            lan_sync.service._inbox["busy-chk"] = req_fake
            with self.assertRaises(HTTPException) as ctx409:
                server.lan_inbox_accept("busy-chk", {})
            self.assertEqual(ctx409.exception.status_code, 409)
            lan_sync.service._inbox.pop("busy-chk", None)

        # B принимает базу с copy_filters_zone=False
        st_acc, acc_db = _ui_call(
            self.peer_b.ui_port, "POST", f"/api/lan/inbox/{req_db['req_id']}/accept",
            {"copy_filters_zone": False},
        )
        self.assertEqual(st_acc, 200)
        self.assertTrue(Path(acc_db["backup"]).is_file())
        self.assertEqual(acc_db["images_copied"], 1)
        self.assertEqual(acc_db["preserved_imported"], 1)
        self.assertFalse(acc_db["filters_copied"])

        # Проверяем дельта-фото и сохранение существующего файла на B
        self.assertEqual((b_img_dir / "3002.webp").read_bytes(), b"DELTA_3002_" * 150)
        self.assertTrue((b_img_dir / "3001.jpg").read_bytes().startswith(b"EXISTING_LOCAL_3001_"))

        # Проверяем сохранность всего личного слоя получателя B
        cb = self.peer_b.db_connect()
        try:
            self.assertEqual(cb.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0], b_dev_id)
            self.assertEqual(cb.execute("SELECT price FROM listings WHERE source_id='3001'").fetchone()[0], 11_000_000)
            self.assertIsNotNone(cb.execute("SELECT 1 FROM listings WHERE source_id='3002'").fetchone())
            imp_row = cb.execute("SELECT imported_only FROM listings WHERE source_id='9001'").fetchone()
            self.assertIsNotNone(imp_row)
            self.assertEqual(imp_row["imported_only"], 1)

            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM likes")}, {"3001", "9001"})
            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM dislikes")}, {"3003"})
            notes_b = {r["source_id"]: r["text"] for r in cb.execute("SELECT source_id, text FROM notes")}
            self.assertEqual(notes_b.get("3001"), "Личная заметка Б по 3001")
            self.assertEqual(notes_b.get("9001"), "Личная заметка Б по 9001")
            self.assertEqual(cb.execute("SELECT text FROM imported_notes WHERE source_id='3001'").fetchone()[0], "Хороший ремонт")
            self.assertEqual(cb.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0], 1)
            self.assertEqual(cb.execute("SELECT COUNT(*) FROM import_items").fetchone()[0], 1)

            merged = {r[0] for r in cb.execute("SELECT source_id FROM manual_merges")}
            self.assertEqual(merged, {"3001", "3002", "3003"})
            peers_b = {r[0] for r in cb.execute("SELECT device_id FROM sync_peers")}
            self.assertIn("extra-peer", peers_b)
            self.assertEqual(len(peers_b), 2)
        finally:
            cb.close()

        # Повторная отправка БД с copy_filters_zone=True перенимает только filters/zone, не трогая lan-настройки B
        cfg_b_before = json.loads((self.peer_b.dir / "settings.json").read_text(encoding="utf-8"))
        server.lan_send_db({"peer_id": peer_b_id})
        _, b_stat2 = _ui_call(self.peer_b.ui_port, "GET", "/api/lan/status")
        req_db2 = b_stat2["inbox"][0]
        st_acc2, acc_db2 = _ui_call(
            self.peer_b.ui_port, "POST", f"/api/lan/inbox/{req_db2['req_id']}/accept",
            {"copy_filters_zone": True},
        )
        self.assertEqual(st_acc2, 200)
        self.assertTrue(acc_db2["filters_copied"])
        cfg_b_after = json.loads((self.peer_b.dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg_b_after["filters"]["price_max"], 22_000_000)
        self.assertEqual(cfg_b_after["lan"]["device_name"], cfg_b_before["lan"]["device_name"])
        self.assertEqual(cfg_b_after["lan"]["port"], cfg_b_before["lan"]["port"])
        self.assertEqual(cfg_b_after["lan"]["device_id"], cfg_b_before["lan"]["device_id"])


class LanStage4LiveAndSpotlightTests(Case):
    def setUp(self):
        super().setUp()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        self.port_a = _free_port()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": self.port_a,
            "auto_discovery": False,
            "device_id": "",
            "device_name": "Комп А",
        }
        settings.save(cfg)
        c = con()
        c.close()
        lan_sync.service._bind_host = "127.0.0.1"
        self.assertTrue(lan_sync.start_if_enabled())
        self.peer_b = _PeerProc(self.dir / "peer_b", name="Mac Б").start()

    def tearDown(self):
        self.peer_b.stop()
        lan_sync.stop()
        lan_sync.reset_runtime_cache()
        super().tearDown()

    def _pair_ab(self):
        st, pin_resp = _ui_call(self.peer_b.ui_port, "POST", "/api/lan/pin", {})
        self.assertEqual(st, 200)
        res = lan_sync.pair_with("127.0.0.1", self.peer_b.lan_port, pin_resp["pin"])
        peer_b_id = res["device_id"]
        my_a_id = lan_sync.status()["device_id"]
        return my_a_id, peer_b_id

    def test_spotlight_matching_import_and_opt_out(self):
        my_a_id, peer_b_id = self._pair_ab()

        # У A: cian:501 (отдельно) и cian:503 (отсутствует у B)
        c = con()
        _insert_row(c, "cian", "501", price=11_200_000, area=54.0, address="Ялта, ул. Гоголя, 12")
        _insert_row(c, "cian", "503", price=16_500_000, area=78.0, rooms=3, address="Ялта, ул. Вергасова, 3")
        c.commit()
        dedupe.rebuild(c)
        oid_501 = c.execute("SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source_id='501'").fetchone()[0]
        oid_503 = c.execute("SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source_id='503'").fetchone()[0]
        c.close()

        # У B: cian:501 склеена с avito:502, поэтому object_id у B другой; cian:503 у B нет вообще
        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "avito", "502", price=11_100_000, area=54.0, address="Ялта, ул. Гоголя, 12")
            _insert_row(cb, "cian", "501", price=11_200_000, area=54.0, address="Ялта, ул. Гоголя, 12")
            cb.execute("UPDATE listings SET object_id='custom-b-obj' WHERE source_id IN ('501','502')")
            cb.commit()
        finally:
            cb.close()

        # 1) Подсветка 501 на B
        sp1 = server.lan_spotlight({"object_id": oid_501, "peer_ids": [peer_b_id]})
        self.assertTrue(sp1["ok"])
        st_b, b_stat = _ui_call(self.peer_b.ui_port, "GET", "/api/status?after=0&lan_after=0")
        self.assertEqual(st_b, 200)
        evs = b_stat["lan"]["events"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "spotlight")
        self.assertIn(["cian", "501"], evs[0]["member_keys"])

        # Проверяем курсор lan_after: повторный опрос не отдаёт то же событие
        st_b2, b_stat2 = _ui_call(self.peer_b.ui_port, "GET", f"/api/status?after=0&lan_after={b_stat['lan']['seq']}")
        self.assertEqual(st_b2, 200)
        self.assertEqual(b_stat2["lan"]["events"], [])

        # 2) Подсветка 503 -> догрузка на B через /api/lan/spotlight/import (GET /sync/card)
        sp2 = server.lan_spotlight({"object_id": oid_503, "peer_ids": [peer_b_id]})
        self.assertTrue(sp2["ok"])
        st_imp, imp_res = _ui_call(
            self.peer_b.ui_port, "POST", "/api/lan/spotlight/import",
            {"from_device_id": my_a_id, "source": "cian", "source_id": "503"},
        )
        self.assertEqual(st_imp, 200)
        self.assertTrue(imp_res["ok"])
        cb = self.peer_b.db_connect()
        try:
            self.assertIsNotNone(cb.execute("SELECT 1 FROM listings WHERE source='cian' AND source_id='503'").fetchone())
        finally:
            cb.close()

        # 3) Отключение accept_spotlight на B
        st_cfg, cfg_r = _ui_call(
            self.peer_b.ui_port, "POST", f"/api/lan/peers/{my_a_id}/live-config",
            {"accept_spotlight": False},
        )
        self.assertEqual(st_cfg, 200)
        self.assertFalse(cfg_r["live_cfg"]["accept_spotlight"])
        with self.assertRaises(HTTPException) as ctx_off:
            server.lan_spotlight({"object_id": oid_501, "peer_ids": [peer_b_id]})
        self.assertEqual(ctx_off.exception.status_code, 400)

    def test_live_favorites_notes_antiloop_and_merges(self):
        my_a_id, peer_b_id = self._pair_ab()

        c = con()
        _insert_row(c, "cian", "601", price=10_000_000, area=48.0, address="Ялта, ул. Пушкинская, 5")
        _insert_row(c, "cian", "602", price=13_500_000, area=65.0, rooms=3, address="Ялта, ул. Ленина, 9")
        c.commit()
        dedupe.rebuild(c)
        oid_601 = c.execute("SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source_id='601'").fetchone()[0]
        oid_602 = c.execute("SELECT COALESCE(object_id, source||':'||source_id) FROM listings WHERE source_id='602'").fetchone()[0]
        c.close()

        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "601", price=10_000_000, area=48.0, address="Ялта, ул. Пушкинская, 5")
            _insert_row(cb, "cian", "602", price=13_500_000, area=65.0, rooms=3, address="Ялта, ул. Ленина, 9")
            cb.execute("INSERT INTO notes VALUES('cian','601','Личная заметка Б','2026-11-19T10:00:00')")
            cb.commit()
        finally:
            cb.close()

        # Настройка: A -> add_only, live_notes, live_merges; B -> fav_recv=ask, live_notes, live_merges
        server.lan_peer_live_config(peer_b_id, {"fav_send": "add_only", "live_notes": True, "live_merges": True, "outbox_enabled": True})
        _ui_call(self.peer_b.ui_port, "POST", f"/api/lan/peers/{my_a_id}/live-config", {
            "fav_send": "add_remove", "fav_recv": "ask", "live_notes": True, "live_merges": True,
        })

        # 1) A ставит ♥ на 601 -> на B режим ask (уведомление + «Принять»)
        server.like({"keys": ["cian:601"], "on": True})
        lan_sync.flush_outbox()
        _, b_s = _ui_call(self.peer_b.ui_port, "GET", "/api/status?after=0&lan_after=0")
        ask_evs = [e for e in b_s["lan"]["events"] if e["kind"] == "live_fav"]
        self.assertEqual(len(ask_evs), 1)
        cb = self.peer_b.db_connect()
        try:
            self.assertEqual(cb.execute("SELECT COUNT(*) FROM likes").fetchone()[0], 0)
        finally:
            cb.close()
        _ui_call(self.peer_b.ui_port, "POST", "/api/lan/live/favorite/apply", {
            "from_device_id": my_a_id, "keys": ask_evs[0]["keys"], "on": True,
        })
        cb = self.peer_b.db_connect()
        try:
            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM likes")}, {"601"})
        finally:
            cb.close()

        # 2) Переключаем A на add_remove и B на auto_sync; B отправляем откат на 601 и ♥ на 602
        server.lan_peer_live_config(peer_b_id, {"fav_send": "add_remove"})
        _ui_call(self.peer_b.ui_port, "POST", f"/api/lan/peers/{my_a_id}/live-config", {"fav_recv": "auto_sync"})
        server.like({"keys": ["cian:601"], "on": False})
        server.like({"keys": ["cian:602"], "on": True})
        lan_sync.flush_outbox()

        cb = self.peer_b.db_connect()
        try:
            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM likes")}, {"602"})
        finally:
            cb.close()
        # Анти-петля: на A после применения событий на B лайки не отыгрались обратно (на A только 602, без дублей)
        lan_sync.flush_outbox()
        _ui_call(self.peer_b.ui_port, "POST", "/api/lan/status")  # пустой пинг не влияет
        c = con()
        self.assertEqual({r[0] for r in c.execute("SELECT source_id FROM likes")}, {"602"})
        c.close()

        # Повторное событие с тем же event_id отбрасывается защитой от петель
        peer_b_row = dict(con().execute("SELECT * FROM sync_peers WHERE device_id=?", (peer_b_id,)).fetchone())
        hdrs_a = lan_sync.service._auth_headers({"device_id": my_a_id}, peer_b_row)
        hdrs_a["Content-Type"] = "application/json"
        dup_body = json.dumps({
            "event_id": "loop-guard-1", "origin_device_id": my_a_id, "origin_name": "Комп А",
            "keys": [["cian", "601"]], "on": True,
        }).encode("utf-8")
        st_d1, res_d1 = lan_sync._http_call("127.0.0.1", self.peer_b.lan_port, "POST", "/sync/live/favorite", body=dup_body, headers=hdrs_a)
        st_d2, res_d2 = lan_sync._http_call("127.0.0.1", self.peer_b.lan_port, "POST", "/sync/live/favorite", body=dup_body, headers=hdrs_a)
        self.assertEqual(st_d1, 200)
        self.assertEqual(st_d2, 200)
        self.assertTrue(res_d2.get("duplicate"))

        # 3) Live-заметки: личная заметка на B сохраняется, imported_notes обновляется и очищается
        server.note_set({"keys": ["cian:601"], "text": "Заметка от А"})
        lan_sync.flush_outbox()
        cb = self.peer_b.db_connect()
        try:
            self.assertEqual(cb.execute("SELECT text FROM notes WHERE source_id='601'").fetchone()[0], "Личная заметка Б")
            self.assertEqual(cb.execute("SELECT text FROM imported_notes WHERE source_id='601'").fetchone()[0], "Заметка от А")
        finally:
            cb.close()
        server.note_set({"keys": ["cian:601"], "text": ""})
        lan_sync.flush_outbox()
        cb = self.peer_b.db_connect()
        try:
            self.assertEqual(cb.execute("SELECT text FROM notes WHERE source_id='601'").fetchone()[0], "Личная заметка Б")
            self.assertIsNone(cb.execute("SELECT text FROM imported_notes WHERE source_id='601'").fetchone())
        finally:
            cb.close()

        # 4) Live-синхронизация ручных объединений
        m_res = server.objects_merge({"primary_id": oid_601, "secondary_id": oid_602})
        merged_oid = m_res["object_id"]
        lan_sync.flush_outbox()
        cb = self.peer_b.db_connect()
        try:
            mm_b = {r[0] for r in cb.execute("SELECT source_id FROM manual_merges")}
            self.assertEqual(mm_b, {"601", "602"})
        finally:
            cb.close()
        server.objects_unmerge({"object_id": merged_oid})
        lan_sync.flush_outbox()
        cb = self.peer_b.db_connect()
        try:
            self.assertEqual(cb.execute("SELECT COUNT(*) FROM manual_merges").fetchone()[0], 0)
        finally:
            cb.close()

    def test_outbox_offline_to_online_and_pull_fresh_db(self):
        my_a_id, peer_b_id = self._pair_ab()
        server.lan_peer_live_config(peer_b_id, {"fav_send": "add_only", "live_notes": True, "outbox_enabled": True})
        _ui_call(self.peer_b.ui_port, "POST", f"/api/lan/peers/{my_a_id}/live-config", {
            "fav_recv": "auto_add", "live_notes": True,
        })

        c = con()
        _insert_row(c, "cian", "701", price=10_400_000, area=50.0, last_seen="2026-11-18T10:00:00")
        c.execute("INSERT INTO runs(source,mode,started,finished,status,found,new) VALUES(?,?,?,?,?,?,?)",
                  ("cian", "full", "2026-11-18T09:55:00", "2026-11-18T10:00:00", "done", 1, 1))
        c.commit()
        c.close()

        cb = self.peer_b.db_connect()
        try:
            _insert_row(cb, "cian", "701", price=10_400_000, area=50.0, last_seen="2026-11-18T10:00:00")
            _insert_row(cb, "cian", "702", price=12_100_000, area=58.0, last_seen="2026-11-20T12:00:00")
            cb.execute("INSERT INTO runs(source,mode,started,finished,status,found,new) VALUES(?,?,?,?,?,?,?)",
                       ("cian", "full", "2026-11-20T11:55:00", "2026-11-20T12:00:00", "done", 2, 1))
            cb.commit()
        finally:
            cb.close()

        # Останавливаем B: события на A должны лечь в офлайн-очередь sync_outbox
        b_dir = self.peer_b.dir
        b_ui_port = self.peer_b.ui_port
        b_lan_port = self.peer_b.lan_port
        self.peer_b.stop()

        server.like({"keys": ["cian:701"], "on": True})
        server.note_set({"keys": ["cian:701"], "text": "Офлайн заметка"})
        fl1 = lan_sync.flush_outbox()
        self.assertGreaterEqual(fl1["pending"], 2)
        self.assertGreaterEqual(lan_sync.status()["outbox_count"], 2)

        # Повторный старт B в ту же директорию и на те же порты -> автоматическая доставка из очереди
        restarted_b = _PeerProc(b_dir, name="Mac Б")
        restarted_b.ui_port = b_ui_port
        restarted_b.lan_port = b_lan_port
        self.peer_b = restarted_b.start()

        lan_sync.service.poll_peers_once()
        # Ждём автоматической доставки разбуженным _outbox_loop или завершаем flush_outbox
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and lan_sync.summary()["outbox_count"] > 0:
            time.sleep(0.05)
        fl2 = lan_sync.flush_outbox()
        self.assertEqual(fl2["pending"], 0)
        self.assertEqual(lan_sync.summary()["outbox_count"], 0)

        cb = self.peer_b.db_connect()
        try:
            self.assertEqual({r[0] for r in cb.execute("SELECT source_id FROM likes")}, {"701"})
            self.assertEqual(cb.execute("SELECT text FROM imported_notes WHERE source_id='701'").fetchone()[0], "Офлайн заметка")
        finally:
            cb.close()

        # У B более свежая база (2026-11-20 > 2026-11-18) -> A видит newer_db_peer и сам забирает базу (pull_db)
        st_a = server.lan_status(refresh=True)
        self.assertIsNotNone(st_a["newer_db_peer"])
        self.assertEqual(st_a["newer_db_peer"]["device_id"], peer_b_id)
        self.assertTrue(st_a["peers"][0]["has_newer_db"])

        pull_res = server.lan_pull_db({"peer_id": peer_b_id})
        self.assertTrue(pull_res["ok"])
        req_id = pull_res["req_id"]
        acc_res = server.lan_inbox_accept(req_id, {"copy_filters_zone": False})
        self.assertTrue(acc_res["ok"])

        c = con()
        self.assertIsNotNone(c.execute("SELECT 1 FROM listings WHERE source_id='702'").fetchone())
        c.close()
        self.assertIsNone(lan_sync.summary()["newer_db_peer"])


class LanAutoDiscoveryAndResilienceTests(Case):
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

    def test_active_discover_probe_and_auto_enable(self):
        port_a = _free_port()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": False,
            "port": port_a,
            "auto_discovery": True,
            "device_id": "",
            "device_name": "Комп Поиск",
        }
        settings.save(cfg)
        c = con()
        c.close()

        self.peer_b = _PeerProc(self.dir / "peer_b", name="Макбук Поиск", auto_discovery=True).start()

        # Активный поиск с auto_enable=True должен сам включить LAN на A, отправить RW_PROBE_V1 и найти B
        res = server.lan_discover({
            "auto_enable": True,
            "wait_sec": 1.0,
            "target_ports": [self.peer_b.lan_port + 1],
        })
        self.assertTrue(res["enabled"])
        self.assertTrue(res["listening"])
        self.assertTrue(settings.load()["lan"]["enabled"])
        names = {d["device_name"]: d for d in res["discovered"]}
        self.assertIn("Макбук Поиск", names)
        disc_b = names["Макбук Поиск"]
        self.assertEqual(disc_b["port"], self.peer_b.lan_port)

        # Сопряжение по найденному устройству и PIN
        st_pin, pin_resp = _ui_call(self.peer_b.ui_port, "POST", "/api/lan/pin", {})
        self.assertEqual(st_pin, 200)
        pair_res = server.lan_pair({"ip": disc_b["ip"], "port": disc_b["port"], "pin": pin_resp["pin"]})
        self.assertTrue(pair_res["ok"])
        st_after = server.lan_status(refresh=True)
        self.assertEqual(st_after["online_peers"], 1)

    def test_auto_reconnect_dhcp_port_change_spoof_guard_and_peer_state(self):
        port_a = _free_port()
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": port_a,
            "auto_discovery": True,
            "device_id": "",
            "device_name": "Комп А",
        }
        settings.save(cfg)
        con().close()
        self.assertTrue(lan_sync.start_if_enabled())

        b_dir = self.dir / "peer_b_reconn"
        self.peer_b = _PeerProc(b_dir, name="Ноутбук Б", auto_discovery=True).start()
        old_b_port = self.peer_b.lan_port

        _, pin_resp = _ui_call(self.peer_b.ui_port, "POST", "/api/lan/pin", {})
        pair_res = server.lan_pair({"ip": "127.0.0.1", "port": old_b_port, "pin": pin_resp["pin"]})
        peer_b_id = pair_res["device_id"]
        lan_sync.service.poll_peers_once()
        seq0 = lan_sync.summary()["seq"]

        # Останавливаем B -> должен сработать переход peer_state(online=False)
        self.peer_b.stop()
        self.peer_b = None
        lan_sync.service.poll_peers_once()
        sum_off = lan_sync.summary(lan_after=seq0)
        self.assertEqual(sum_off["online_peers"], 0)
        ps_off = [e for e in sum_off["events"] if e["kind"] == "peer_state"]
        self.assertEqual(len(ps_off), 1)
        self.assertFalse(ps_off[0]["online"])

        # Поддельный сервер с другим портом: подмена UDP discovery НЕ должна перезаписывать sync_peers
        class _RogueHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"detail":"unauthorized"}')

            def log_message(self, *args):
                pass

        rogue_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RogueHandler)
        rogue_port = rogue_srv.server_address[1]
        rogue_thr = threading.Thread(target=rogue_srv.serve_forever, daemon=True)
        rogue_thr.start()
        try:
            forged_pkt = json.dumps({
                "magic": lan_sync._DISCOVERY_MAGIC,
                "device_id": peer_b_id,
                "device_name": "Ноутбук Б",
                "port": rogue_port,
            }).encode("utf-8")
            lan_sync.service._handle_discovery_packet(forged_pkt, "127.0.0.1", sync_verify=True)
            c = con()
            saved_port = c.execute("SELECT port FROM sync_peers WHERE device_id=?", (peer_b_id,)).fetchone()[0]
            c.close()
            self.assertEqual(saved_port, old_b_port)
        finally:
            rogue_srv.shutdown()
            rogue_srv.server_close()

        # Перезапуск настоящего B в той же папке с НОВЫМ портом -> авто-переподключение и peer_state(online=True)
        seq1 = lan_sync.summary()["seq"]
        restarted_b = _PeerProc(b_dir, name="Ноутбук Б", auto_discovery=True)
        if restarted_b.lan_port == old_b_port:
            restarted_b.lan_port = _free_port()
        self.peer_b = restarted_b.start()
        new_b_port = self.peer_b.lan_port

        res = server.lan_discover({"auto_enable": False, "wait_sec": 1.0, "target_ports": [new_b_port + 1]})
        self.assertEqual(res["online_peers"], 1)
        c = con()
        updated_port = c.execute("SELECT port FROM sync_peers WHERE device_id=?", (peer_b_id,)).fetchone()[0]
        c.close()
        self.assertEqual(updated_port, new_b_port)
        ps_on = [e for e in lan_sync.summary(lan_after=seq1)["events"] if e["kind"] == "peer_state"]
        self.assertTrue(any(e["device_id"] == peer_b_id and e["online"] is True for e in ps_on))

        # Выключение LAN корректно сбрасывает счётчик онлайн
        off_res = server.lan_config({"enabled": False})
        self.assertFalse(off_res["enabled"])
        self.assertEqual(lan_sync.summary()["online_peers"], 0)

    def test_mid_transfer_disconnect_corrupt_payloads_and_session_ttl(self):
        cfg = settings.load()
        cfg["lan"] = {
            "enabled": True,
            "port": _free_port(),
            "auto_discovery": False,
            "device_id": "dev-a",
            "device_name": "Комп А",
        }
        settings.save(cfg)

        c = con()
        _insert_row(c, "cian", "801", price=9_800_000, area=48.0, address="Ялта, ул. Садовая, 3")
        c.commit()
        dedupe.rebuild(c)
        oid = c.execute("SELECT object_id FROM listings WHERE source_id='801'").fetchone()[0]
        peer_tok = "tok-peer-for-a"
        c.execute(
            "INSERT INTO sync_peers(device_id, name, ip, port, token_hash, peer_token, paired_at, last_seen) VALUES(?,?,?,?,?,?,?,?)",
            ("peer-sec", "Peer Sec", "127.0.0.1", _free_port(), lan_sync._hash_token("sec-token"), peer_tok, "2026-11-19T10:00:00", "2026-11-19T10:00:00"),
        )
        c.commit()
        c.close()

        auth_hdr = {
            "X-RW-Device": "peer-sec",
            "Authorization": "Bearer sec-token",
        }
        with tempfile.TemporaryDirectory(prefix="rw_test_trunc_") as td:
            zp = Path(td) / "good.rwcards.zip"
            exchange.export_cards_zip(zp, object_ids=[oid], kind="cards")
            raw_zip = zp.read_bytes()

        # 1) Обрезанный ZIP карточек -> 400, без .part и записей в _inbox
        r_trunc = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/push/cards",
            client=("127.0.0.1", 50000), headers=auth_hdr, raw_body=raw_zip[: len(raw_zip) // 2],
        )
        self.assertEqual(r_trunc.status_code, 400)
        self.assertEqual(len(lan_sync.status()["inbox"]), 0)
        idir = lan_sync.service._inbox_dir()
        self.assertEqual(list(idir.glob("*.part")) if idir.exists() else [], [])

        # 2) Несовпадение Content-Length -> 400 («получены неполные данные»)
        r_cl = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/push/cards",
            client=("127.0.0.1", 50000),
            headers={**auth_hdr, "Content-Length": str(len(raw_zip) + 50)},
            raw_body=raw_zip,
        )
        self.assertEqual(r_cl.status_code, 400)
        self.assertIn("неполные данные", r_cl.text)
        self.assertEqual(len(lan_sync.status()["inbox"]), 0)

        # 3) Битый SQLite внутри /sync/push/db/init -> 400 и чистая папка без остатков db_*
        with tempfile.TemporaryDirectory(prefix="rw_test_baddb_") as td:
            bad_init = Path(td) / "bad_init.zip"
            with zipfile.ZipFile(bad_init, "w") as zf:
                zf.writestr("realty.db", b"BROKEN_NOT_A_SQLITE_FILE")
                zf.writestr("meta.json", json.dumps({"format": lan_sync._DB_SYNC_FORMAT, "images_manifest": []}))
            bad_bytes = bad_init.read_bytes()
        r_baddb = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/push/db/init",
            client=("127.0.0.1", 50000), headers=auth_hdr, raw_body=bad_bytes,
        )
        self.assertEqual(r_baddb.status_code, 400)
        self.assertEqual([p for p in idir.glob("db_*")] if idir.exists() else [], [])

        # 4) Брошенная 2-шаговая сессия DB (disconnect до /db/images) + TTL cleanup
        with tempfile.TemporaryDirectory(prefix="rw_test_gooddb_") as td:
            snap_db = Path(td) / "realty.db"
            transfer._snapshot(snap_db)
            good_init = Path(td) / "init.zip"
            with zipfile.ZipFile(good_init, "w") as zf:
                zf.write(snap_db, "realty.db")
                zf.writestr("meta.json", json.dumps({
                    "format": lan_sync._DB_SYNC_FORMAT,
                    "freshness": {"cian": "2026-11-19T12:00:00"},
                    "counts": {"listings": 1, "likes": 0, "runs": 0, "images": 1},
                    "images_manifest": [{"source": "cian", "source_id": "999", "ext": "jpg", "size": 123}],
                }))
            good_bytes = good_init.read_bytes()

        r_init = _asgi_call(
            lan_sync.service.sync_app, "POST", "/sync/push/db/init",
            client=("127.0.0.1", 50000), headers=auth_hdr, raw_body=good_bytes,
        )
        self.assertEqual(r_init.status_code, 200, r_init.text)
        req_id = r_init.data["req_id"]
        self.assertFalse(r_init.data["queued"])
        self.assertIn(req_id, lan_sync.service._db_sessions)
        sess_dir = Path(lan_sync.service._db_sessions[req_id]["sess_dir"])
        self.assertTrue(sess_dir.is_dir())

        # Битый архив фотографий для этой сессии -> 400, в _inbox не попадает
        r_badimg = _asgi_call(
            lan_sync.service.sync_app, "POST", f"/sync/push/db/images/{req_id}",
            client=("127.0.0.1", 50000), headers=auth_hdr, raw_body=b"NOT_A_ZIP_DATA",
        )
        self.assertEqual(r_badimg.status_code, 400)
        self.assertEqual(len(lan_sync.status()["inbox"]), 0)

        # Просрочиваем незавершённую сессию и чистим по TTL
        lan_sync.service._db_sessions[req_id]["created_mono"] -= 700.0
        cleaned = lan_sync.service.cleanup_stale_sessions(max_age_sec=600.0)
        self.assertGreaterEqual(cleaned, 1)
        self.assertNotIn(req_id, lan_sync.service._db_sessions)
        self.assertFalse(sess_dir.exists())

        # Локальная база осталась целой
        c = con()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE source_id='801'").fetchone()[0], 1)
        c.close()

        # 5) Обрыв связи на стороне отправителя (RemoteDisconnected) -> чистое сообщение без падения и утечек
        before_tmp = set(Path(tempfile.gettempdir()).glob("rw_send_*"))
        with patch("http.client.HTTPConnection.getresponse", side_effect=http.client.RemoteDisconnected("closed")):
            sc = lan_sync.send_cards(["peer-sec"], object_ids=[oid])
            self.assertFalse(sc["ok"])
            self.assertIn("Связь с устройством прервалась", sc["results"][0]["error"])
            with self.assertRaises(ValueError) as ctx:
                lan_sync.send_db("peer-sec")
            self.assertIn("Связь с устройством прервалась", str(ctx.exception))
        after_tmp = set(Path(tempfile.gettempdir()).glob("rw_send_*"))
        self.assertEqual(after_tmp - before_tmp, set())
