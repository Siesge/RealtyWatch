"""Данные для окна «О программе»: автор и лицензии всего, из чего собрана программа.

Названия и лицензии записаны здесь, а версии берутся у установленных пакетов и у папки browsers —
так список не расходится с тем, что реально лежит рядом с программой.
"""
import importlib.metadata as meta
import platform

from . import paths

AUTHOR = "Анатолий Никитин"
NAME = "RealtyWatch"

# пакеты Python из requirements.txt: имя пакета, лицензия, зачем он нужен
PY_LIBS = [
    ("curl_cffi", "MIT", "быстрые HTTP-запросы с отпечатком браузера"),
    ("patchright", "Apache-2.0", "управление Chromium, форк Playwright"),
    ("fastapi", "MIT", "локальный веб-интерфейс"),
    ("uvicorn", "BSD-3-Clause", "сервер интерфейса"),
    ("lxml", "BSD-3-Clause", "разбор страниц сайтов"),
    ("pywebview", "BSD-3-Clause", "окно программы"),
    ("pythonnet", "MIT", "мост к WebView2 внутри окна"),
    ("pystray", "LGPL-3.0", "значок в трее"),
    ("pillow", "MIT-CMU", "иконки и фотографии"),
]
# то, что лежит в web/vendor и работает внутри страницы
WEB_LIBS = [
    ("Leaflet", "1.9.4", "BSD-2-Clause", "карта"),
    ("Chart.js", "4.4.1", "MIT", "графики на дашборде"),
    ("Manrope", "", "SIL OFL 1.1", "шрифт интерфейса"),
]
DATA = [
    ("OpenStreetMap", "", "ODbL 1.0", "карта, границы Ялты и округа — © участники OpenStreetMap"),
]


def _version(dist):
    try:
        return meta.version(dist)
    except Exception:  # пакета нет (отладка на системном Python) — просто без версии
        return ""


def _chromium():
    """Номер сборки Chromium — из имени папки, которую создаёт setup.bat."""
    try:
        dirs = sorted(paths.BROWSERS.glob("chromium-*"))
        return dirs[-1].name.split("-", 1)[1] if dirs else ""
    except OSError:
        return ""


def _app_version():
    """Версия из version.txt — его кладёт сборщик релиза; при работе из репозитория файла нет."""
    f = paths.ROOT / "version.txt"
    try:
        first = f.read_text(encoding="utf-8-sig").strip().splitlines()[0]
    except (OSError, IndexError):
        return ""
    # «RealtyWatch 1.0, сборка 2026-11-03, коммит a11b165»
    head = first.split(",")[0].strip()
    return head[len(NAME):].strip() if head.startswith(NAME) else head


def info():
    """Всё, что показывает окно «О программе»."""
    env = [("Python", platform.python_version(), "PSF-2.0", "среда выполнения"),
           ("Chromium", _chromium(), "BSD-3-Clause", "браузер для сбора данных")]
    return {
        "name": NAME,
        "version": _app_version(),
        "author": AUTHOR,
        "about": "Инструмент для анализа рынка квартир в Ялте: собирает объявления с ЦИАН, Авито и "
                 "Яндекс Недвижимости, сводит их в объекты и ведёт историю цен.",
        "groups": [
            {"title": "Библиотеки Python", "items": [{"name": n, "version": _version(n), "license": lic, "what": what}
                                                     for n, lic, what in PY_LIBS]},
            {"title": "В интерфейсе", "items": [{"name": n, "version": v, "license": lic, "what": what}
                                                for n, v, lic, what in WEB_LIBS]},
            {"title": "Среда", "items": [{"name": n, "version": v, "license": lic, "what": what} for n, v, lic, what in env]},
            {"title": "Данные", "items": [{"name": n, "version": v, "license": lic, "what": what} for n, v, lic, what in DATA]},
        ],
    }
