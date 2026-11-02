"""Подробный журнал каждого прогона и снимки страниц при сбоях.

logs/app.log            — общий журнал (ротация: 2 МБ × 5 файлов)
logs/runs/<id>_<сайт>.log — всё о прогоне: каждый запрос (способ, код ответа, время, размер),
                          страницы выдачи (найдено / новых), заявлено сайтом и собрано, проверка снятых
logs/snapshots/          — HTML/JSON страницы, на которой что-то пошло не так (капча, пустая страница,
                          ограничение IP, ошибка сервера): по нему видно, что на самом деле показал сайт
"""
import logging
import logging.handlers
import re
import threading
from datetime import datetime

from .paths import LOGS

RUNS_DIR = LOGS / "runs"
SNAP_DIR = LOGS / "snapshots"
KEEP_RUN_LOGS = 300
KEEP_SNAPSHOTS = 60
SNAPSHOT_MAX_BYTES = 3_000_000


def setup_logging():
    """Общий журнал с ротацией. Если журнал уже настроен (тесты), ничего не меняет."""
    root = logging.getLogger()
    if root.handlers:
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOGS / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(h)
    root.setLevel(logging.INFO)


def _prune(folder, pattern, keep):
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime)
    for p in files[:-keep] if len(files) > keep else []:
        try:
            p.unlink()
        except OSError:
            pass


def run_log_path(run_id):
    return next(iter(sorted(RUNS_DIR.glob(f"{int(run_id):06d}_*.log"))), None)


class RunLog:
    """Подробный журнал одного прогона. Пишется из потока прогона (и потоков проверки фото)."""

    def __init__(self, run_id, source):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.run_id, self.source = run_id, source
        self.path = RUNS_DIR / f"{int(run_id):06d}_{source}.log"
        self._f = open(self.path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self.requests = 0
        _prune(RUNS_DIR, "*.log", KEEP_RUN_LOGS)

    def line(self, msg):
        with self._lock:
            if self._f:
                self._f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
                self._f.flush()

    def request(self, kind, method, url, status=None, ms=None, size=None, note=""):
        self.requests += 1
        parts = [f"→ {kind:7}", method, str(status or "—"), f"{ms} мс" if ms is not None else "", f"{size // 1024} КБ" if size else "", url]
        self.line(" ".join(p for p in parts if p) + (f" — {note}" if note else ""))

    def snapshot(self, body, reason, ext="html"):
        """Сохраняет страницу, на которой произошёл сбой (или картинку задания капчи). Возвращает имя файла."""
        if not body:
            return None
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w\-]+", "_", reason.lower())[:40].strip("_")
        name = f"{int(self.run_id):06d}_{self.source}_{datetime.now():%Y%m%d-%H%M%S}_{slug}.{ext}"
        try:
            if isinstance(body, bytes):
                (SNAP_DIR / name).write_bytes(body[:SNAPSHOT_MAX_BYTES])
            else:
                (SNAP_DIR / name).write_text(body[:SNAPSHOT_MAX_BYTES], encoding="utf-8")
        except OSError:
            return None
        what = "картинка" if isinstance(body, bytes) else "страница"
        self.line(f"📄 сохранена {what} ({reason}): logs/snapshots/{name}")
        _prune(SNAP_DIR, "*.*", KEEP_SNAPSHOTS)
        return name

    def close(self):
        with self._lock:
            if self._f:
                self._f.close()
                self._f = None


class NullRunLog:
    """Заглушка, когда прогон не ведёт подробный журнал (проверка снятых из настроек и т.п.)."""
    requests = 0

    def line(self, msg):
        pass

    def request(self, *a, **k):
        pass

    def snapshot(self, *a, **k):
        return None

    def close(self):
        pass
