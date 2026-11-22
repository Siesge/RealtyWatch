"""Тесты механизма обновления через GitHub Releases (app/updater.py), уборки устаревших/мусорных файлов,
отката при сбое, бесконфликтной миграции старых баз с сохранением всех пользовательских данных
(заметок, ручных объединений, лайков, дизлайков) и интерфейса в «Настройках»."""
import hashlib
import io
import json
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi import HTTPException

from app import db, dedupe, history, lan_sync, paths, runner, server, settings, tiles, transfer, updater
from tests.support import Case, LocalBrowser, con, has_browser


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build_release_zip(files: dict[str, bytes | str], prefix: str = "RealtyWatch/") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            raw = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(prefix + rel, raw)
    return buf.getvalue()


class UpdaterCoreAndCleanupTests(Case):
    """Проверка выбора ассета, валидации ZIP/SHA-256, удаления мусора, сохранности БД и отката."""

    def test_version_parsing_and_portable_asset_selection(self):
        self.assertEqual(updater.parse_version("v1.5"), (1, 5))
        self.assertEqual(updater.parse_version("RealtyWatch 1.5.0, сборка 2026-11-17, коммит abc"), (1, 5))
        self.assertTrue(updater.is_newer_version("v1.6", "1.5"))
        self.assertTrue(updater.is_newer_version("1.10", "1.9"))
        self.assertFalse(updater.is_newer_version("1.5.0", "1.5"))
        self.assertFalse(updater.is_newer_version("1.4", "1.5"))

        fake_sha = "a" * 64
        rel = {
            "tag_name": "v1.6",
            "name": "RealtyWatch 1.6",
            "body": "Добавлено автообновление и кэш тайлов.",
            "html_url": "https://github.com/Siesge/RealtyWatch/releases/tag/v1.6",
            "published_at": "2026-11-22T12:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "RealtyWatch-1.6-macos.dmg",
                    "browser_download_url": "https://example.test/mac.dmg",
                    "size": 5000,
                },
                {
                    "name": "RealtyWatch-1.6-windows-с-базой.zip",
                    "browser_download_url": "https://example.test/with-db.zip",
                    "size": 900000,
                },
                {
                    "name": "RealtyWatch-1.6-windows-portable.zip",
                    "browser_download_url": "https://example.test/portable.zip",
                    "size": 650000,
                    "digest": f"sha256:{fake_sha}",
                },
            ],
        }
        picked = updater.select_portable_asset(rel)
        self.assertEqual(picked["version"], "1.6")
        self.assertEqual(picked["asset_name"], "RealtyWatch-1.6-windows-portable.zip")
        self.assertEqual(picked["sha256"], fake_sha)

        with self.assertRaises(ValueError):
            updater.select_portable_asset({**rel, "draft": True})

    def test_zip_validation_rejects_bad_sha256_traversal_and_embedded_database(self):
        good_bytes = _build_release_zip({
            "app/main.py": "print('ok')\n" * 60,
            "web/index.html": "<html>RealtyWatch</html>\n" * 40,
            "version.txt": "RealtyWatch 1.6, сборка 2026-11-22\r\n",
        })
        zp = self.dir / "good.zip"
        zp.write_bytes(good_bytes)

        # Неверная контрольная сумма SHA-256
        with self.assertRaises(ValueError) as ctx_sha:
            updater.validate_and_unpack_release(zp, self.dir / "out1", expected_sha256="b" * 64)
        self.assertIn("SHA-256", str(ctx_sha.exception))

        # Архив с database/realty.db должен жёстко блокироваться, чтобы чужая база никогда не затёрла пользовательскую
        with_db_bytes = _build_release_zip({
            "app/main.py": "print('ok')\n" * 60,
            "web/index.html": "<html>ok</html>\n" * 40,
            "database/realty.db": b"SQLite format 3\x00" + b"0" * 600,
        })
        zp_db = self.dir / "with_db.zip"
        zp_db.write_bytes(with_db_bytes)
        with self.assertRaises(ValueError) as ctx_db:
            updater.validate_and_unpack_release(zp_db, self.dir / "out2")
        self.assertIn("database", str(ctx_db.exception))

        # Path traversal
        trav_bytes = _build_release_zip({
            "../outside.py": "x = 1\n" * 100,
            "app/main.py": "print('ok')\n" * 60,
            "web/index.html": "<html>ok</html>\n" * 40,
        }, prefix="")
        zp_tr = self.dir / "trav.zip"
        zp_tr.write_bytes(trav_bytes)
        with self.assertRaises(ValueError):
            updater.validate_and_unpack_release(zp_tr, self.dir / "out3")

    def test_update_preserves_all_user_data_deletes_obsolete_files_and_cleans_garbage(self):
        root = updater.APP_ROOT
        data_dir = updater.DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)

        # Разворачиваем установленную версию 1.5 с устаревшими файлами и мусором
        (root / "version.txt").write_text("RealtyWatch 1.5, сборка 2026-11-17, коммит 1111111\r\n", encoding="utf-8")
        (root / "app").mkdir(parents=True, exist_ok=True)
        (root / "app" / "main.py").write_text("# v1.5 main\n", encoding="utf-8")
        (root / "app" / "obsolete_v15.py").write_text("# должен быть удалён в 1.6\n", encoding="utf-8")
        (root / "app" / "old_subpkg").mkdir(parents=True, exist_ok=True)
        (root / "app" / "old_subpkg" / "deprecated.py").write_text("# старый подпакет\n", encoding="utf-8")
        (root / "app" / "__pycache__").mkdir(parents=True, exist_ok=True)
        (root / "app" / "__pycache__" / "obsolete.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
        (root / "app" / "unfinished.part").write_bytes(b"temp")
        (root / "web").mkdir(parents=True, exist_ok=True)
        (root / "web" / "index.html").write_text("<html>v1.5</html>", encoding="utf-8")
        (root / "web" / "old_panel.js").write_text("// удалён в 1.6", encoding="utf-8")
        (root / "old_release_note.txt").write_text("устаревший корневой файл из 1.5", encoding="utf-8")
        (root / "stray_crash.tmp").write_text("мусор в корне", encoding="utf-8")
        (data_dir / "installed_files.json").write_text(
            json.dumps(["app/main.py", "app/obsolete_v15.py", "web/index.html", "web/old_panel.js", "old_release_note.txt", "version.txt"]),
            encoding="utf-8",
        )

        # Наполняем защищённые пользовательские папки и саму БД богатым пользовательским слоем
        for sub, rel, content in [
            ("database/images/cian", "101.jpg", b"JPEG_USER_PHOTO"),
            ("database/tiles/13/4873", "2948.png", b"\x89PNG\r\n\x1a\nUSER_TILE"),
            ("profiles/avito", "Cookies", b"AVITO_SESSION_COOKIES"),
            ("exports", "Избранное.html", b"<html>User Export</html>"),
            ("logs", "app.log", b"log line\n"),
        ]:
            d_p = root / sub
            d_p.mkdir(parents=True, exist_ok=True)
            (d_p / rel).write_bytes(content)

        c = con()
        db.upsert(c, {
            "source": "cian", "source_id": "101", "url": "https://cian.test/101", "title": "2-к, 62 м²",
            "price": 11_500_000, "rooms": 2, "area": 62.0, "kitchen_area": 14.0, "build_year": 2018,
            "floor": 4, "floors": 9, "address": "Ялта, ул. Морская, 10", "lat": 44.485, "lon": 34.145, "zone": "A",
        })
        db.upsert(c, {
            "source": "avito", "source_id": "202", "url": "https://avito.test/202", "title": "2-к, 63 м²",
            "price": 11_700_000, "rooms": 2, "area": 63.0, "floor": 5, "floors": 9,
            "address": "Ялта, ул. Морская, 10А", "lat": 44.4852, "lon": 34.1452, "zone": "A",
        })
        db.upsert(c, {
            "source": "yandex", "source_id": "303", "url": "https://yandex.test/303", "title": "1-к, 40 м²",
            "price": 8_900_000, "rooms": 1, "area": 40.0, "floor": 2, "floors": 5,
            "address": "Ялта, ул. Садовая, 3", "lat": 44.490, "lon": 34.150, "zone": "A",
        })
        c.commit()
        dedupe.rebuild(c)
        c.close()

        # Пользовательские лайки, дизлайки, личная заметка, чужая заметка и ручное объединение карточек
        server.objects_merge({"primary_id": "cian:101", "secondary_id": "avito:202"})
        server.like({"keys": ["cian:101", "avito:202"], "on": True})
        server.dislike({"keys": ["yandex:303"], "on": True})
        server.note_set({"keys": ["cian:101", "avito:202"], "text": "Отличная планировка, звонить после 18:00"})
        c = con()
        c.execute(
            "INSERT INTO imported_notes VALUES('cian','101','peer-1','Супруга','Понравился балкон','2026-11-20T12:00:00','2026-11-20T12:00:00',1)"
        )
        c.execute("INSERT INTO sync_peers(device_id,name,ip,port,token_hash) VALUES('peer-1','Ноутбук','192.168.1.25',8766,'hash1')")
        c.commit()
        c.close()

        # Готовим пакет релиза v1.6 (без obsolete_v15.py, old_subpkg, old_panel.js, old_release_note.txt)
        zip_v16 = _build_release_zip({
            "version.txt": "RealtyWatch 1.6, сборка 2026-11-22, коммит 2222222\r\n",
            "app/main.py": "# v1.6 main\n" + ("x = 1\n" * 60),
            "app/new_feature_v16.py": "FEATURE = True\n",
            "web/index.html": "<html>v1.6</html>\n" * 30,
            "web/app.js": "// v1.6 app\n" * 30,
            "requirements.txt": "fastapi>=0.115\n",
        })
        sha_v16 = hashlib.sha256(zip_v16).hexdigest()
        gh_payload = json.dumps({
            "tag_name": "v1.6",
            "name": "RealtyWatch 1.6",
            "body": "### Что нового\n- Автообновление через GitHub\n- Чистка устаревших файлов",
            "html_url": "https://github.com/Siesge/RealtyWatch/releases/tag/v1.6",
            "published_at": "2026-11-22T15:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "RealtyWatch-1.6-windows-portable.zip",
                "browser_download_url": "http://127.0.0.1:9/RealtyWatch-1.6-windows-portable.zip",
                "size": len(zip_v16),
                "digest": f"sha256:{sha_v16}",
            }],
        }).encode("utf-8")

        def fake_fetch(url, accept="*/*", timeout=25.0, max_bytes=updater.MAX_ZIP_BYTES):
            if url.endswith("/releases/latest"):
                return 200, gh_payload
            return 200, zip_v16

        svc = updater.UpdaterService()
        with patch.object(updater, "_fetch_bytes", side_effect=fake_fetch):
            st = svc.check_for_updates(manual=True, download=True)

        self.assertTrue(st["ready"])
        self.assertEqual(st["available_version"], "1.6")

        # Создаём 4 старых бэкапа обновления, чтобы проверить авто-ротацию до KEEP_UPDATE_BACKUPS (3)
        transfer.BACKUPS.mkdir(parents=True, exist_ok=True)
        for idx in range(4):
            p_old = transfer.BACKUPS / f"перед_обновлением_v1.{idx}_2026-10-0{idx + 1}_1200.zip"
            p_old.write_bytes(b"OLD_BACKUP")
            os_ts = time.time() - (10 - idx) * 86400
            import os as _os
            _os.utime(p_old, (os_ts, os_ts))

        res = svc.apply_update(spawn_installer=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["version"], "1.6")
        self.assertTrue(Path(res["backup"]).is_file())
        self.assertLessEqual(len(list(transfer.BACKUPS.glob("перед_обновлением_*.zip"))), updater.KEEP_UPDATE_BACKUPS)

        # Проверяем новые файлы и полное удаление старых файлов + мусора + пустых папок
        self.assertEqual(updater.read_local_version(root), "1.6")
        self.assertTrue((root / "app" / "new_feature_v16.py").is_file())
        self.assertFalse((root / "app" / "obsolete_v15.py").exists())
        self.assertFalse((root / "app" / "old_subpkg").exists())
        self.assertFalse((root / "app" / "__pycache__").exists())
        self.assertFalse((root / "app" / "unfinished.part").exists())
        self.assertFalse((root / "web" / "old_panel.js").exists())
        self.assertFalse((root / "old_release_note.txt").exists())
        self.assertFalse((root / "stray_crash.tmp").exists())
        self.assertFalse(updater._staging_dir(data_dir).exists())
        self.assertFalse(updater._rollback_dir(data_dir).exists())

        # Проверяем неприкосновенность защищённых папок и всех пользовательских данных БД
        self.assertEqual((root / "database/images/cian/101.jpg").read_bytes(), b"JPEG_USER_PHOTO")
        self.assertEqual((root / "database/tiles/13/4873/2948.png").read_bytes(), b"\x89PNG\r\n\x1a\nUSER_TILE")
        self.assertEqual((root / "profiles/avito/Cookies").read_bytes(), b"AVITO_SESSION_COOKIES")
        self.assertEqual((root / "exports/Избранное.html").read_bytes(), b"<html>User Export</html>")

        rows_after = {f"{r['source']}:{r['source_id']}": r for r in server.listings()}
        self.assertEqual(len(rows_after), 3)
        self.assertTrue(rows_after["cian:101"]["liked"])
        self.assertTrue(rows_after["cian:101"]["manual_merged"])
        self.assertTrue(rows_after["cian:101"]["manual_primary"])
        self.assertEqual(rows_after["cian:101"]["object_id"], rows_after["avito:202"]["object_id"])
        self.assertEqual(rows_after["cian:101"]["note"], "Отличная планировка, звонить после 18:00")
        self.assertEqual(rows_after["cian:101"]["imported_notes"][0]["text"], "Понравился балкон")
        self.assertTrue(rows_after["yandex:303"]["disliked"])
        c2 = con()
        self.assertEqual(c2.execute("SELECT COUNT(*) FROM sync_peers WHERE device_id='peer-1'").fetchone()[0], 1)
        c2.close()

        # Дополнительно проверяем автономный скрипт _STANDALONE_APPLY_SCRIPT через python -I (как в реальном апдейтере)
        unpacked2 = self.dir / "unpacked_v17"
        zp_v17 = self.dir / "v17.zip"
        zp_v17.write_bytes(_build_release_zip({
            "version.txt": "RealtyWatch 1.7, сборка 2026-11-23\r\n",
            "app/main.py": "# v1.7 main\n" * 40,
            "web/index.html": "<html>v1.7</html>\n" * 30,
        }))
        updater.validate_and_unpack_release(zp_v17, unpacked2)
        script_py = self.dir / "apply_staged.py"
        script_py.write_text(updater._STANDALONE_APPLY_SCRIPT, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-I", str(script_py), str(unpacked2), str(root), str(data_dir), "1.7"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(updater.read_local_version(root), "1.7")
        self.assertFalse((root / "app" / "new_feature_v16.py").exists(), "файл из 1.6, убранный в 1.7, удалён автономным скриптом")
        self.assertEqual((root / "profiles/avito/Cookies").read_bytes(), b"AVITO_SESSION_COOKIES")

    def test_rollback_restores_previous_version_on_copy_failure(self):
        root = updater.APP_ROOT
        data_dir = updater.DATA_DIR
        (root / "version.txt").write_text("RealtyWatch 1.5\r\n", encoding="utf-8")
        (root / "app").mkdir(parents=True, exist_ok=True)
        (root / "app" / "main.py").write_text("# v1.5 working\n", encoding="utf-8")
        (root / "app" / "keep_me.py").write_text("KEEP = 1\n", encoding="utf-8")
        (root / "web").mkdir(parents=True, exist_ok=True)
        (root / "web" / "index.html").write_text("<html>1.5</html>", encoding="utf-8")

        unpacked = self.dir / "broken_unpacked"
        (unpacked / "app").mkdir(parents=True, exist_ok=True)
        (unpacked / "app" / "main.py").write_text("# v1.6 new\n", encoding="utf-8")
        (unpacked / "web").mkdir(parents=True, exist_ok=True)
        (unpacked / "web" / "index.html").write_text("<html>1.6</html>", encoding="utf-8")
        (unpacked / "version.txt").write_text("RealtyWatch 1.6\r\n", encoding="utf-8")

        real_replace = updater.os.replace
        calls = {"n": 0}

        def failing_replace(src, dst):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError("имитация сбоя диска посреди обновления")
            return real_replace(src, dst)

        with patch.object(updater.os, "replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                updater.sync_release_tree(unpacked, root, data_dir, new_version="1.6")

        # Убеждаемся, что откат вернул все исходные файлы v1.5 целиком
        self.assertEqual(updater.read_local_version(root), "1.5")
        self.assertEqual((root / "app" / "main.py").read_text(encoding="utf-8"), "# v1.5 working\n")
        self.assertTrue((root / "app" / "keep_me.py").is_file())

    def test_legacy_v1_database_migrates_conflict_free_with_all_user_data(self):
        # Создаём минимальную базу от старой версии (без новых колонок и новых таблиц)
        legacy_db = self.dir / "legacy_v1.db"
        raw_con = sqlite3.connect(legacy_db)
        raw_con.executescript("""
            CREATE TABLE listings(
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                url TEXT, title TEXT, price INTEGER, rooms INTEGER, area REAL,
                floor INTEGER, floors INTEGER, address TEXT, lat REAL, lon REAL, zone TEXT,
                first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
                PRIMARY KEY(source, source_id)
            );
            CREATE TABLE price_history(source TEXT, source_id TEXT, ts TEXT, price INTEGER);
            CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, started TEXT, finished TEXT, status TEXT);
            CREATE TABLE likes(source TEXT, source_id TEXT, PRIMARY KEY(source, source_id));
            CREATE TABLE notes(source TEXT, source_id TEXT, text TEXT, PRIMARY KEY(source, source_id));
            INSERT INTO listings VALUES(
                'cian', '501', 'https://cian.test/501', '2-к., 58 м²', 10500000, 2, 58.0,
                3, 9, 'Ялта, ул. Кирова, 15', 44.486, 34.146, 'A',
                '2026-09-01T10:00:00', '2026-09-10T10:00:00', 1
            );
            INSERT INTO price_history VALUES('cian', '501', '2026-09-01T10:00:00', 10500000);
            INSERT INTO likes VALUES('cian', '501');
            INSERT INTO notes VALUES('cian', '501', 'Старая заметка из v1.0');
        """)
        raw_con.commit()
        raw_con.close()

        db.DB_FILE = legacy_db
        db.reset()
        c = db.connect()
        try:
            ver = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            self.assertEqual(ver, str(db.SCHEMA_VERSION))
            dedupe.rebuild(c)
            history.backfill(c)
            dash = history.dashboard(c)
            self.assertIn("recent", dash)
        finally:
            c.close()

        items = server.listings()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_id"], "501")
        self.assertTrue(items[0]["liked"])
        self.assertEqual(items[0]["note"], "Старая заметка из v1.0")
        self.assertIsNone(items[0]["kitchen_area"])
        self.assertIsNone(items[0]["build_year"])

    def test_intervals_day_week_month_and_busy_or_dev_guards(self):
        svc = updater.UpdaterService()
        # По умолчанию auto_check=True, interval='week', ещё не проверялось -> is_due() == True
        self.assertTrue(svc.is_due())

        now = datetime.now()
        for interval, ago_days, expected in [
            ("day", 0.5, False),
            ("day", 1.1, True),
            ("week", 5.0, False),
            ("week", 7.2, True),
            ("month", 20.0, False),
            ("month", 31.0, True),
        ]:
            server.set_settings({"updates": {"auto_check": True, "interval": interval}})
            svc._write_state({"last_checked": (now - timedelta(days=ago_days)).isoformat(timespec="seconds")})
            self.assertEqual(svc.is_due(), expected, f"interval={interval}, ago={ago_days}")

        server.set_settings({"updates": {"auto_check": False, "interval": "day"}})
        self.assertFalse(svc.is_due())

        # Блокировка установки во время активного сбора -> HTTP 409
        with patch.object(runner.manager, "any_running", return_value=True):
            with self.assertRaises(HTTPException) as ctx409:
                server.update_apply()
            self.assertEqual(ctx409.exception.status_code, 409)

        # Защита git-папки разработчика без version.txt -> HTTP 400
        (updater.APP_ROOT / ".git").mkdir(parents=True, exist_ok=True)
        with self.assertRaises(HTTPException) as ctx400:
            server.update_apply()
        self.assertEqual(ctx400.exception.status_code, 400)
        self.assertIn("git", ctx400.exception.detail.lower())


class UpdaterSettingsUITests(Case):
    """Проверка блока «Обновление программы» в Настройках и верхнего баннера обновления в Chromium."""

    @unittest.skipUnless(has_browser(), "Chromium не установлен (setup.bat)")
    def test_settings_controls_and_check_update_banner_in_browser(self):
        lan_sync.stop()
        root = updater.APP_ROOT
        (root / "version.txt").write_text("RealtyWatch 1.5, сборка 2026-11-17\r\n", encoding="utf-8")

        zip_v16 = _build_release_zip({
            "version.txt": "RealtyWatch 1.6, сборка 2026-11-22\r\n",
            "app/main.py": "# v1.6\n" * 50,
            "web/index.html": "<html>v1.6</html>\n" * 30,
        })
        sha_v16 = hashlib.sha256(zip_v16).hexdigest()
        gh_payload = json.dumps({
            "tag_name": "v1.6",
            "name": "RealtyWatch 1.6 — Обновление",
            "body": "Добавлена проверка обновлений раз в день, неделю и месяц.",
            "html_url": "https://github.com/Siesge/RealtyWatch/releases/tag/v1.6",
            "published_at": "2026-11-22T15:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "RealtyWatch-1.6-windows-portable.zip",
                "browser_download_url": "http://127.0.0.1:9/portable.zip",
                "size": len(zip_v16),
                "digest": f"sha256:{sha_v16}",
            }],
        }).encode("utf-8")

        def fake_fetch(url, accept="*/*", timeout=25.0, max_bytes=updater.MAX_ZIP_BYTES):
            return (200, gh_payload) if url.endswith("/releases/latest") else (200, zip_v16)

        patch_upd = patch.object(updater, "_fetch_bytes", side_effect=fake_fetch)
        patch_tile = patch.object(tiles, "_fetch_url", return_value=(404, b""))
        patch_upd.start()
        patch_tile.start()
        self.addCleanup(patch_upd.stop)
        self.addCleanup(patch_tile.stop)

        port = _free_port()
        uv_srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=uv_srv.run, daemon=True)
        t.start()
        self.addCleanup(lambda: setattr(uv_srv, "should_exit", True) or t.join(timeout=5.0))
        deadline = time.time() + 8.0
        while not uv_srv.started and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(uv_srv.started)

        b = LocalBrowser("ui-updater", lambda h, u: False, lambda m: None, None, window="hidden")
        self.addCleanup(b.close)
        b.start()
        page = b.page
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => typeof SETTINGS === 'object' && SETTINGS !== null && view === 'dashboard' && document.getElementById('view-dashboard').children.length > 0",
            timeout=10000,
        )
        page.click('.nav button[data-view="settings"]')
        page.wait_for_selector("#updateCard #btnCheckUpdate", state="visible", timeout=5000)

        # Проверяем все три варианта периодичности: раз в день, раз в неделю, раз в месяц
        opts = page.evaluate("() => [...document.querySelectorAll('#sUpdInt option')].map(o => [o.value, o.textContent.trim()])")
        self.assertEqual(opts, [["day", "раз в день"], ["week", "раз в неделю"], ["month", "раз в месяц"]])
        self.assertTrue(page.is_checked("#sUpdAuto"))

        # Меняем периодичность на «раз в месяц» и выключаем/включаем галочку — должно сразу сохраняться в settings
        page.select_option("#sUpdInt", "month")
        page.wait_for_timeout(150)
        self.assertEqual(settings.load()["updates"]["interval"], "month")
        page.evaluate("() => document.getElementById('sUpdAuto').click()")
        page.wait_for_timeout(150)
        self.assertFalse(settings.load()["updates"]["auto_check"])
        page.evaluate("() => document.getElementById('sUpdAuto').click()")
        page.wait_for_timeout(150)
        self.assertTrue(settings.load()["updates"]["auto_check"])

        # Убираем всплывшие выше уведомления справа сверху, чтобы клик пришёлся точно в кнопку #btnCheckUpdate
        page.evaluate("() => document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")
        page.click("#btnCheckUpdate")
        page.wait_for_selector("#btnApplyUpdate", state="visible", timeout=5000)
        self.assertIn("1.6", page.text_content("#updStatusBox"))
        self.assertFalse(page.evaluate("() => document.getElementById('updateBar').classList.contains('hidden')"))

        # Открываем окно «Описание релиза»
        page.evaluate("() => document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")
        page.click("#btnUpdNotes")
        page.wait_for_selector("#updModalClose", state="visible", timeout=3000)
        self.assertIn("раз в день, неделю и месяц", page.text_content("#modalBody"))
        page.click("#updModalClose")

        self.assertEqual(js_errors, [], f"ошибки JS в браузере: {js_errors}")


if __name__ == "__main__":
    unittest.main()
