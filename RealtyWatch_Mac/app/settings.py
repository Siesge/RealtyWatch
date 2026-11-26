"""Настройки: значения по умолчанию + пользовательский data/settings.json."""
import copy
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
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
    "server": {"port": 8765},
    "lan": {"enabled": False, "port": 8766, "auto_discovery": True, "device_id": "", "device_name": "", "machine_fp": ""},
}

_lock = threading.Lock()
_id_lock = threading.Lock()


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


_DARWIN_UUID_RE = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')


def parse_darwin_ioreg_uuid(output: str) -> str:
    """Извлекает IOPlatformUUID из вывода ioreg -rd1 -c IOPlatformExpertDevice."""
    m = _DARWIN_UUID_RE.search(output or "")
    return m.group(1).strip().lower() if m and m.group(1).strip() else ""


def _darwin_platform_uuid() -> str:
    for cmd in ("/usr/sbin/ioreg", "ioreg"):
        try:
            proc = subprocess.run(
                [cmd, "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2.0,
            )
            if proc.returncode == 0 and proc.stdout:
                uid = parse_darwin_ioreg_uuid(proc.stdout)
                if uid:
                    return uid
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def machine_fingerprint() -> str:
    """Короткий отпечаток текущей ОС/машины (macOS IOPlatformUUID / Windows MachineGuid / hostname),
    чтобы обнаруживать перенос папки или БД на другой компьютер."""
    raw = ""
    if sys.platform == "darwin":
        raw = _darwin_platform_uuid()
    elif os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                val, _ = winreg.QueryValueEx(k, "MachineGuid")
                raw = str(val or "").strip().lower()
        except OSError:
            raw = ""
    if not raw:
        raw = f"{(socket.gethostname() or '').strip().lower()}:{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ensure_device_identity(con=None):
    """Гарантирует наличие постоянного уникального UUID (device_id) для этого ПК.
    Если папка или settings.json были скопированы с другого компьютера (machine_fp не совпал),
    автоматически генерирует новый device_id, чтобы две машины не считали друг друга «самим собой»."""
    with _id_lock:
        cfg = load()
        lan = cfg.setdefault("lan", {})
        cur_fp = machine_fingerprint()
        saved_fp = str(lan.get("machine_fp") or "").strip()
        host_name = (socket.gethostname() or "").strip() or "RealtyWatch"
        cloned_from_other_pc = bool(saved_fp and cur_fp and saved_fp != cur_fp)

        dev_id = "" if cloned_from_other_pc else str(lan.get("device_id") or "").strip()
        if not dev_id and not cloned_from_other_pc and con is not None:
            row = con.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()
            if row and row[0]:
                dev_id = str(row[0]).strip()
        if not dev_id:
            dev_id = str(uuid.uuid4())

        dev_name = str(lan.get("device_name") or "").strip()
        if not dev_name or cloned_from_other_pc:
            dev_name = host_name

        if (
            lan.get("device_id") != dev_id
            or lan.get("device_name") != dev_name
            or lan.get("machine_fp") != cur_fp
        ):
            lan["device_id"] = dev_id
            lan["device_name"] = dev_name
            lan["machine_fp"] = cur_fp
            save(cfg)

        if con is not None:
            if cloned_from_other_pc:
                try:
                    con.execute("DELETE FROM sync_peers")
                    con.execute("DELETE FROM sync_outbox")
                except Exception:
                    pass
            row = con.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()
            if not row or str(row[0] or "") != dev_id or cloned_from_other_pc:
                con.execute("INSERT OR REPLACE INTO meta VALUES('device_id', ?)", (dev_id,))
                con.commit()
        return {"device_id": dev_id, "device_name": dev_name}


def rotate_device_id(con=None) -> dict:
    """Принудительно назначает этому компьютеру новый уникальный device_id (при обнаружении клона ID в локальной сети)."""
    with _id_lock:
        cfg = load()
        lan = cfg.setdefault("lan", {})
        new_id = str(uuid.uuid4())
        cur_fp = machine_fingerprint()
        dev_name = str(lan.get("device_name") or "").strip() or (socket.gethostname() or "").strip() or "RealtyWatch"
        lan["device_id"] = new_id
        lan["device_name"] = dev_name
        lan["machine_fp"] = cur_fp
        save(cfg)
        if con is not None:
            con.execute("INSERT OR REPLACE INTO meta VALUES('device_id', ?)", (new_id,))
            con.commit()
        return {"device_id": new_id, "device_name": dev_name}
