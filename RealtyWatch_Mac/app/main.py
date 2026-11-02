"""Точка входа.
  python -m app.main            — окно программы (WebKit), сервер работает внутри, порт пользователю не нужен
  python -m app.main --server   — только сервер (для отладки или если окно не нужно), порт из настроек

Форк для macOS: окно — pywebview поверх WebKit, значка в строке меню нет (на macOS и pywebview,
и pystray требуют главный цикл приложения — вместе они не уживаются), системные вызовы — в app.system.
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
from . import system

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
    system.message(text)


def _single_instance():
    """Не даёт запустить вторую копию программы (две копии писали бы в одну базу)."""
    return system.single_instance(LOCK_FILE)


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
    return system.light_theme()


def _notify(title, text):
    """Уведомление системы: на macOS его показывает osascript, своего значка программе не нужно."""
    return system.notify(title, text)


def _startup_maintenance():
    """Объекты для объявлений, собранных до появления объединения, и догрузка недостающих фото."""
    from . import db, dedupe, images
    from .runner import manager

    from . import history

    con = db.connect()
    if con.execute("SELECT COUNT(*) FROM listings WHERE object_id IS NULL").fetchone()[0]:
        n = dedupe.rebuild(con)
        manager.log("db", f"объединение дублей: {n} объектов")
    con.execute("UPDATE runs SET status='interrupted', message='прервано: программа была закрыта во время сбора' "
                "WHERE status='running'")
    con.commit()
    history.backfill(con)
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


def _notify_hook():
    """Когда закончили все сайты одного запуска: уведомление, только если на рынке что-то изменилось.
    Сайт, который собирает ещё с прошлого запуска, уведомляет отдельно, когда закончит."""
    from . import db, history
    from .runner import SOURCE_LABEL, manager

    def hook(run_ids, sources, auto):
        con = db.connect()
        c = history.runs_summary(con, run_ids)
        con.close()
        text = _changes_text(c)
        names = ", ".join(SOURCE_LABEL.get(s, s) for s in sources)
        if not text:
            manager.log("db", f"сбор завершён ({names}) — изменений на рынке нет")
            return
        manager.log("db", f"🔔 изменения на рынке ({names}): {text}")
        _notify(f"RealtyWatch: изменения на рынке — {names}", text)

    return hook


class DesktopApi:
    """Методы, доступные странице как window.pywebview.api.* — нативные диалоги системы."""

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

    def open_db_folder(self):
        system.open_folder(paths.DB_DIR)
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
        system.open_folder(paths.LOGS)
        return {"ok": True}

    def pick_import(self):
        path = self._dialog("OPEN", directory=str(Path.home() / "Desktop"))
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
        _message(f"Не удалось запустить внутренний сервер (порт {port}). Подробности в logs/console.log")
        return
    url = f"http://127.0.0.1:{port}/"
    RUNTIME_FILE.write_text(json.dumps({"pid": os.getpid(), "url": url}), encoding="utf-8")
    threading.Thread(target=_startup_maintenance, daemon=True, name="maintenance").start()
    from .runner import manager
    from .server import scheduler

    sched = scheduler()  # автообновление по таймеру работает и при свёрнутом окне
    try:
        if server_only:
            manager.on_launch_done.append(_notify_hook())
            print(f"RealtyWatch (сервер): {url}")
            while True:
                time.sleep(1)
        else:
            import webview

            api = DesktopApi()
            window = webview.create_window(TITLE, url, width=1440, height=900,
                                           min_size=(1100, 680), maximized=True, js_api=api,
                                           background_color="#F3F1EC" if _light_theme() else "#0D0F12")
            DesktopApi._window = window
            manager.on_launch_done.append(_notify_hook())
            from . import alerts

            # капчу, которую программа не прошла сама, показывает уведомление: окно браузера уже на экране
            alerts.on_human_needed(lambda title, text: _notify(f"RealtyWatch — {title}", text))
            webview.start(private_mode=False, storage_path=str(paths.DATA / "webview"))
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_runs()
        server.should_exit = True
        _clean_caches()
        try:
            RUNTIME_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
