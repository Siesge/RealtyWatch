"""Все пути относительно папки приложения — для полной портативности.

database/  — всё, что составляет базу и переносится экспортом: realty.db, images/, settings.json
data/      — служебное для этого компьютера (кэш окна, файл блокировки)
profiles/  — профили браузера и cookies (привязаны к устройству, не экспортируются)
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "database"
IMAGES = DB_DIR / "images"
BACKUPS = DB_DIR / "backups"
EXPORTS = ROOT / "exports"
DATA = ROOT / "data"
PROFILES = ROOT / "profiles"
LOGS = ROOT / "logs"
WEB = ROOT / "web"
BROWSERS = ROOT / "browsers"

for _d in (DB_DIR, IMAGES, DATA, PROFILES, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

# Переезд базы и настроек из data/ (версии до появления папки database/)
for _name in ("realty.db", "realty.db-wal", "realty.db-shm", "settings.json"):
    _old, _new = DATA / _name, DB_DIR / _name
    if _old.exists() and not _new.exists():
        shutil.move(str(_old), str(_new))

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS))
