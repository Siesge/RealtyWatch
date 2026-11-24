"""Настройки: значения по умолчанию + пользовательский data/settings.json."""
import copy
import json
import socket
import threading
import uuid

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
            "delay": [6.0, 10.0], "delay_base": [6.0, 10.0], "max_verify": 20,
            "max_pages": 40,
        },
        "yandex": {"enabled": True, "mode": "auto", "region_path": "yalta", "delay": [2.5, 6.0], "max_pages": 40},
    },
    # window: hidden — окно за пределами экрана (появляется только при капче); visible; headless
    # auto_captcha — пазл-капчу Авито (GeeTest) программа проходит сама, остальные — человек
    "browser": {"window": "hidden", "captcha_wait_min": 10, "auto_captcha": True},
    # ui: tray_on_close — «Закрыть» убирает окно в трей, программа продолжает работать; выключено —
    # «Закрыть» закрывает программу (значок в трее и уведомления показываются, пока включена опция или расписание).
    "ui": {"tray_on_close": True},
    "server": {"port": 8765},
    "lan": {"enabled": False, "port": 8766, "auto_discovery": True, "device_id": "", "device_name": ""},
    "updates": {"auto_check": True, "interval": "day"},
}

_lock = threading.Lock()
_id_lock = threading.Lock()
_watchers = []


def on_change(fn):
    """Кого позвать, когда настройки сохранили: значок в трее появляется и исчезает сразу."""
    _watchers.append(fn)


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
    for fn in _watchers:  # вне замка: слушатель сам читает настройки
        try:
            fn(cfg)
        except Exception:
            pass


def ensure_device_identity(con=None):
    """Гарантирует наличие постоянного UUID (device_id) и понятного имени устройства для обмена и LAN."""
    with _id_lock:
        cfg = load()
        lan = cfg.setdefault("lan", {})
        dev_id = str(lan.get("device_id") or "").strip()
        if not dev_id and con is not None:
            row = con.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()
            if row and row[0]:
                dev_id = str(row[0]).strip()
        if not dev_id:
            dev_id = str(uuid.uuid4())
        dev_name = str(lan.get("device_name") or "").strip()
        if not dev_name:
            dev_name = (socket.gethostname() or "").strip() or "RealtyWatch"
        if lan.get("device_id") != dev_id or lan.get("device_name") != dev_name:
            lan["device_id"] = dev_id
            lan["device_name"] = dev_name
            save(cfg)
        if con is not None:
            row = con.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()
            if not row or str(row[0] or "") != dev_id:
                con.execute("INSERT OR REPLACE INTO meta VALUES('device_id', ?)", (dev_id,))
                con.commit()
        return {"device_id": dev_id, "device_name": dev_name}
