"""Точка входа.
  python -m app.main            — окно программы (WebView2), сервер работает внутри, порт пользователю не нужен
  python -m app.main --server   — только сервер (для отладки), порт из настроек
"""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

from . import paths
from . import settings

LOCK_FILE = paths.DATA / "app.lock"
RUNTIME_FILE = paths.DATA / "runtime.json"


def _fix_streams():
    # pythonw.exe запускается без консоли: stdout/stderr = None — пишем их в лог
    if sys.stdout is None or sys.stderr is None:
        f = open(paths.LOGS / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _message(text):
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, "RealtyWatch", 0x40)
    except Exception:
        print(text)


def _single_instance():
    """Не даёт запустить вторую копию программы (две копии писали бы в одну базу)."""
    import msvcrt

    f = open(LOCK_FILE, "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return f


def _free_port(preferred):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _start_server(port):
    import uvicorn

    from .server import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    t = threading.Thread(target=server.run, daemon=True, name="server")
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server


TITLE = "RealtyWatch — квартиры в Ялте"


def _light_theme():
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


def _set_app_id():
    """Своя группа в панели задач (иначе Windows покажет иконку python.exe)."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RealtyWatch.Yalta")
    except Exception:
        pass


def _apply_icon():
    """pywebview берёт иконку из python.exe — заменяем на свою через WM_SETICON."""
    try:
        import ctypes

        u = ctypes.windll.user32
        u.LoadImageW.restype = ctypes.c_void_p
        u.FindWindowW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        ico = str(paths.ROOT / "assets" / "icon.ico")
        hwnd = None
        for _ in range(50):
            hwnd = u.FindWindowW(None, TITLE)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return
        big = u.LoadImageW(None, ico, 1, u.GetSystemMetrics(11) * 2, u.GetSystemMetrics(12) * 2, 0x10)
        small = u.LoadImageW(None, ico, 1, u.GetSystemMetrics(49), u.GetSystemMetrics(50), 0x10)
        u.SendMessageW(hwnd, 0x80, 1, big)    # WM_SETICON, ICON_BIG
        u.SendMessageW(hwnd, 0x80, 0, small)  # ICON_SMALL
    except Exception:
        pass


def _startup_maintenance():
    """Объекты для объявлений, собранных до появления объединения, и догрузка недостающих фото."""
    from . import db, dedupe, images
    from .runner import manager

    from . import history

    con = db.connect()
    try:
        rules = con.execute("SELECT value FROM meta WHERE key='dedupe_rules'").fetchone()
        if rules is None or rules[0] != str(dedupe.RULES_VERSION):
            # правила объединения поменялись — объекты пересобираются, и с ними точки дашборда
            before = con.execute("SELECT COUNT(DISTINCT object_id) FROM listings").fetchone()[0]
            n = dedupe.rebuild(con)
            history.rebuild_snapshots(con)
            con.execute("INSERT OR REPLACE INTO meta VALUES('dedupe_rules', ?)", (str(dedupe.RULES_VERSION),))
            con.commit()
            manager.log("db", f"объединение дублей по новым правилам: было {before} объектов, стало {n}")
        elif con.execute("SELECT COUNT(*) FROM listings WHERE object_id IS NULL").fetchone()[0]:
            n = dedupe.rebuild(con)
            manager.log("db", f"объединение дублей: {n} объектов")
        con.execute("UPDATE runs SET status='interrupted', message='прервано: программа была закрыта во время сбора' "
                    "WHERE status='running'")
        con.commit()
        history.backfill(con)
    finally:
        con.close()
    images.repair_async(db.connect, log=lambda m: manager.log("db", m))


def _changes_text(c):
    parts = []
    if c["new"]:
        parts.append(f"+{c['new']} новых")
    if c["removed"]:
        parts.append(f"{c['removed']} снято")
    if c["price_down"]:
        parts.append(f"↓{c['price_down']} подешевели")
    if c["price_up"]:
        parts.append(f"↑{c['price_up']} подорожали")
    if c["returned"]:
        parts.append(f"{c['returned']} вернулись в продажу")
    return " · ".join(parts)


def _notify_hook(tray=lambda: None):
    """Когда закончили все сайты одного запуска: уведомление, только если на рынке что-то изменилось.
    Сайт, который собирает ещё с прошлого запуска, уведомляет отдельно, когда закончит.
    tray() — значок в трее на эту минуту или None: без галочки значка нет и уведомлений тоже."""
    from . import db, history
    from .runner import SOURCE_LABEL, manager
    from .share import plural

    def hook(run_ids, sources, auto):
        con = db.connect()
        try:
            c = history.runs_summary(con, run_ids)
            marks = ",".join("?" * len(run_ids))
            since = con.execute(f"SELECT MIN(started) FROM runs WHERE id IN ({marks})", run_ids).fetchone()[0] if run_ids else None
            hidden = db.hidden_fresh(con, since) if since else 0
        finally:
            con.close()
        text = _changes_text(c)
        names = ", ".join(SOURCE_LABEL.get(s, s) for s in sources)
        # скрытые квартиры, в карточки которых за этот сбор добавились объявления: может быть, новая квартира
        hid = (f"в {hidden} {plural(hidden, 'скрытую квартиру', 'скрытые квартиры', 'скрытых квартир')} "
               "добавились объявления") if hidden else ""
        if hid:
            manager.log("db", f"👎 {hid} — кружок на кнопке «Скрытые»")
        if not text and not hid:
            manager.log("db", f"сбор завершён ({names}) — изменений на рынке нет")
            return
        if text:
            manager.log("db", f"🔔 изменения на рынке ({names}): {text}")
        icon = tray()
        if icon:
            body = " · ".join(x for x in (text, hid) if x)
            # только новости о скрытых — по клику сразу «Скрытые» в подборке
            icon.notify(f"RealtyWatch: изменения на рынке — {names}", body + "\nНажмите, чтобы посмотреть",
                        view="changes:last" if text else "cards:hidden")

    return hook


class DesktopApi:
    """Методы, доступные странице как window.pywebview.api.* — нативные диалоги Windows."""

    _window = None

    def _dialog(self, kind, file_types=("Архив базы RealtyWatch (*.zip)",), **kw):
        import webview

        fd = getattr(webview, "FileDialog", None)
        mode = getattr(fd, kind) if fd else getattr(webview, f"{kind}_DIALOG")
        res = DesktopApi._window.create_file_dialog(mode, file_types=file_types, **kw)
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    def export_db(self):
        from . import transfer

        path = self._dialog("SAVE", directory=str(Path.home() / "Desktop"), save_filename=transfer.default_name())
        if not path:
            return {"cancelled": True}
        if not path.lower().endswith(".zip"):
            path += ".zip"
        return {"path": transfer.export_zip(path)}

    def export_liked(self):
        from . import db, share

        con = db.connect()
        try:
            if not share.has_liked(con):
                return {"error": "отметьте квартиры сердечком — выгружать пока нечего"}
            path = self._dialog("SAVE", file_types=("Страница с избранным (*.html)",),
                                directory=str(Path.home() / "Desktop"), save_filename=share.default_name())
            if not path:
                return {"cancelled": True}
            return share.export(path, con)
        finally:
            con.close()

    def open_db_folder(self):
        os.startfile(str(paths.DB_DIR))
        return {"ok": True}

    def export_run_log(self, run_id):
        from .server import run_log_text

        text, snaps = run_log_text(run_id)
        if text is None:
            return {"error": "журнала нет"}
        path = self._dialog("SAVE", file_types=("Журнал (*.log;*.txt)",), directory=str(Path.home() / "Desktop"),
                            save_filename=f"realtywatch-run-{run_id}.log")
        if not path:
            return {"cancelled": True}
        Path(path).write_text(text + (("\nСохранённые страницы (logs/snapshots):\n" + "\n".join(snaps)) if snaps else ""),
                              encoding="utf-8")
        return {"path": path}

    def open_logs_folder(self):
        os.startfile(str(paths.LOGS))
        return {"ok": True}

    def pick_import(self):
        path = self._dialog("OPEN", directory=str(Path.home() / "Desktop"))
        return {"path": path} if path else {"cancelled": True}

    def export_cards(self, object_ids=None, liked_only=False, kind="cards"):
        from . import exchange
        from .runner import manager

        kind = str(kind or ("favorites" if liked_only else "cards"))
        path = self._dialog(
            "SAVE",
            file_types=("Карточки RealtyWatch (*.rwcards.zip)", "ZIP-архив (*.zip)"),
            directory=str(Path.home() / "Desktop"),
            save_filename=exchange.default_name(kind),
        )
        if not path:
            return {"cancelled": True}
        if not path.lower().endswith(".zip"):
            path += exchange.EXT
        try:
            res = exchange.export_cards_zip(
                path,
                object_ids=object_ids,
                liked_only=bool(liked_only),
                kind=kind,
            )
        except ValueError as e:
            return {"error": str(e)}
        manager.log("db", f"карточки выгружены (.rwcards.zip): {res['path']} ({res['cards']} карточек, фото {res['images']})")
        return res

    def pick_cards_import(self):
        path = self._dialog(
            "OPEN",
            file_types=("Карточки RealtyWatch (*.rwcards.zip;*.zip)",),
            directory=str(Path.home() / "Desktop"),
        )
        return {"path": path} if path else {"cancelled": True}


def _clean_caches():
    """При закрытии убираем кэши браузера и окна: папку программы можно сразу заархивировать и переслать."""
    try:
        from . import cleanup

        t0 = time.monotonic()
        freed = cleanup.free_caches()
        if freed:
            print(f"очищены кэши: освобождено {freed / 1048576:.0f} МБ за {time.monotonic() - t0:.1f} с")
    except Exception as e:  # очистка не должна мешать закрытию
        print(f"не удалось очистить кэши: {type(e).__name__}: {e}")


def _shutdown_runs():
    from .runner import manager

    manager.stop()
    deadline = time.time() + 20
    for t in list(manager.threads.values()):
        t.join(max(0.1, deadline - time.time()))


def main():
    _fix_streams()
    lock = _single_instance()
    if lock is None:
        _message("RealtyWatch уже запущен.")
        return
    cfg = settings.load()
    server_only = "--server" in sys.argv
    port = cfg["server"]["port"] if server_only else _free_port(cfg["server"]["port"])
    server = _start_server(port)
    if not server.started:
        _message(f"Не удалось запустить внутренний сервер (порт {port}). Подробности в logs\\console.log")
        return
    url = f"http://127.0.0.1:{port}/"
    RUNTIME_FILE.write_text(json.dumps({"pid": os.getpid(), "url": url}), encoding="utf-8")
    threading.Thread(target=_startup_maintenance, daemon=True, name="maintenance").start()
    from . import lan_sync, updater
    from .runner import manager
    from .server import scheduler

    lan_sync.start_if_enabled()
    sched = scheduler()  # автообновление по таймеру работает и при свёрнутом окне
    tray = None
    stop_srv_evt = threading.Event()
    try:
        if server_only:
            updater.service.set_hooks(shutdown_cb=stop_srv_evt.set)
            updater.service.start_background()
            manager.on_launch_done.append(_notify_hook())
            print(f"RealtyWatch (сервер для отладки): {url}")
            while not stop_srv_evt.is_set():
                stop_srv_evt.wait(1.0)
        else:
            import webview

            from .tray import Tray, wanted

            _set_app_id()
            api = DesktopApi()
            window = webview.create_window(TITLE, url, width=1440, height=900,
                                           min_size=(1100, 680), maximized=True, js_api=api,
                                           background_color="#F3F1EC" if _light_theme() else "#0D0F12")
            DesktopApi._window = window
            window.events.shown += _apply_icon

            hooked = False
            tray_lock = threading.Lock()

            def _tray(on):
                """Значок в трее живёт ровно столько, сколько стоит галочка «сворачивать в трей
                по закрытию»: сняли — убираем сразу, поставили — заводим, без перезапуска."""
                nonlocal tray
                with tray_lock:
                    if on and tray is None:
                        tray = Tray(window, str(paths.ROOT / "assets" / "icon.png"), TITLE,
                                    on_update=lambda: manager.start(sched.get()["sources"]))
                    elif tray and not on:
                        tray.stop()
                        tray = None

            def _quit_for_update():
                if tray:
                    tray.quitting = True
                try:
                    window.destroy()
                except Exception:
                    pass

            def on_shown():
                nonlocal hooked
                if hooked:
                    return
                hooked = True
                manager.on_launch_done.append(_notify_hook(lambda: tray))
                # капча, которую программа не прошла сама, — уведомление Windows (окно браузера уже на экране)
                from . import alerts
                alerts.on_human_needed(lambda title, text: tray and tray.notify(f"RealtyWatch — {title}", text))
                # «Свернуть» — обычная панель задач, «Закрыть» — в трей, пока значок в трее есть
                window.events.closing += (lambda: tray.on_close() if tray else True)
                settings.on_change(lambda cfg: _tray(bool(cfg.get("ui", {}).get("tray_on_close", True))))
                _tray(wanted())
                updater.service.set_hooks(
                    shutdown_cb=_quit_for_update,
                    notify_cb=lambda title, text: tray and tray.notify(title, text),
                )
                updater.service.start_background()

            window.events.shown += on_shown
            webview.start(private_mode=False, storage_path=str(paths.DATA / "webview"))
    except KeyboardInterrupt:
        pass
    finally:
        updater.service.stop_background()
        if tray:
            tray.stop()
        _shutdown_runs()
        lan_sync.stop()
        server.should_exit = True
        _clean_caches()
        try:
            RUNTIME_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
