"""Точка входа: python -m app.main [--no-browser]"""
import sys
import threading
import webbrowser

import uvicorn

from . import paths  # noqa: F401  — создаёт папки и задаёт PLAYWRIGHT_BROWSERS_PATH
from . import settings


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    port = settings.load()["server"]["port"]
    url = f"http://127.0.0.1:{port}/"
    print(f"RealtyWatch: {url}  (закройте это окно, чтобы остановить)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    from .server import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
