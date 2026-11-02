"""Где что лежит.

Код (app/, web/, assets/) — рядом с этим файлом: на macOS он внутри RealtyWatch.app и только читается.
Всё, что программа пишет, живёт в рабочей папке:
  macOS   — ~/Library/Application Support/RealtyWatch (так принято, и .app не должен писать внутрь себя)
  Windows — сама папка программы (портативно, как было)
Переменная RW_HOME задаёт рабочую папку вручную — ею же пользуются тесты и портативный режим.

database/  — всё, что составляет базу и переносится экспортом: realty.db, images/, settings.json
data/      — служебное для этого компьютера (кэш окна, файл блокировки)
profiles/  — профили браузера и cookies (привязаны к устройству, не экспортируются)
runtime/, browsers/ — Python и Chromium, их приносит установщик (bin/bootstrap.sh)
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # код программы
WEB = ROOT / "web"
ASSETS = ROOT / "assets"


def _home():
    if os.environ.get("RW_HOME"):
        return Path(os.environ["RW_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "RealtyWatch"
    return ROOT


HOME = _home()                                          # всё, что программа пишет
DB_DIR = HOME / "database"
IMAGES = DB_DIR / "images"
BACKUPS = DB_DIR / "backups"
EXPORTS = HOME / "exports"
DATA = HOME / "data"
PROFILES = HOME / "profiles"
LOGS = HOME / "logs"
BROWSERS = HOME / "browsers"

for _d in (DB_DIR, IMAGES, DATA, PROFILES, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

# Переезд базы и настроек из data/ (версии до появления папки database/)
for _name in ("realty.db", "realty.db-wal", "realty.db-shm", "settings.json"):
    _old, _new = DATA / _name, DB_DIR / _name
    if _old.exists() and not _new.exists():
        shutil.move(str(_old), str(_new))

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS))
