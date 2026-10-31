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
    try:
        if server_only:
            print(f"RealtyWatch (сервер для отладки): {url}")
            while True:
                time.sleep(1)
        else:
            import webview

            webview.create_window("RealtyWatch — квартиры в Ялте", url, width=1440, height=900,
                                  min_size=(1000, 650), maximized=True, background_color="#f4f5f7")
            webview.start(private_mode=False, storage_path=str(paths.DATA / "webview"))
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_runs()
        server.should_exit = True
        try:
            RUNTIME_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
