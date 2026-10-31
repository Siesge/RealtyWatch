"""Все пути относительно папки приложения — для полной портативности."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROFILES = ROOT / "profiles"
LOGS = ROOT / "logs"
WEB = ROOT / "web"
BROWSERS = ROOT / "browsers"

for _d in (DATA, PROFILES, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS))
