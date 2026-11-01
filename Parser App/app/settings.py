"""Настройки: значения по умолчанию + пользовательский data/settings.json."""
import copy
import json
import threading

from .paths import DB_DIR

SETTINGS_FILE = DB_DIR / "settings.json"

DEFAULTS = {
    "filters": {
        "rooms": [1, 2, 3],
        "area_min": 40,
        "price_min": 8_000_000,
        "price_max": 13_000_000,
        "exclude_encumbrance": True,
        "exclude_share": True,
        "exclude_auction": True,
        # off — не проверять; exclude_none — отсеять явно «без ремонта/отделки»;
        # strict — оставить только объявления с явно указанным ремонтом
        "renovation": "exclude_none",
    },
    # Зона 1: красный круг с GeoZone_v1.jpg (калибровка по Ливадийскому дворцу,
    # Белой даче и «Атлантиде», точность ~50–70 м). polygon — [[lat, lon], ...], если задан.
    "zone": {"name": "Зона (красный круг)", "center": [44.483823, 34.143353], "radius_m": 1035, "polygon": None},
    "sites": {
        "cian": {"enabled": True, "mode": "auto", "region": 184739, "delay": [2.5, 6.0], "max_pages": 40},
        "avito": {
            "enabled": True,
            "mode": "auto",
            "search_url": "https://www.avito.ru/yalta/kvartiry/prodam-ASgBAgICAUSSA8YQ",
            "details": True,
            # Авито ограничивает IP при частых запросах: первичная загрузка растягивается на несколько прогонов
            "max_details": 150,
            "delay": [6.0, 10.0], "max_verify": 20,
            "max_pages": 40,
        },
        "yandex": {"enabled": True, "mode": "auto", "region_path": "yalta", "delay": [2.5, 6.0], "max_pages": 40},
    },
    # window: hidden — окно за пределами экрана (появляется только при капче); visible; headless
    "browser": {"window": "hidden", "captcha_wait_min": 10},
    "server": {"port": 8765},
}

_lock = threading.Lock()


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    with _lock:
        cfg = copy.deepcopy(DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                _merge(cfg, json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return cfg


def save(cfg):
    with _lock:
        SETTINGS_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
