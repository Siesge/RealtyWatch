"""Локальный дисковый кэш и прокси тайлов карты: database/tiles/{z}/{x}/{y}.png.

Защищает встроенную карту WebView2 от блокировок OpenStreetMap по браузерному User-Agent/Referer
и позволяет просматривать ранее открытые участки карты без интернета (cache-first + stale-if-error)."""
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import paths

TILES = paths.DB_DIR / "tiles"

TILE_TTL_SEC = 30 * 86400
FETCH_TIMEOUT_SEC = 6.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_TILE_BYTES = 100
MAX_TILE_BYTES = 2 * 1024 * 1024
MAX_CONCURRENT_DOWNLOADS = 6

USER_AGENT = "RealtyWatch/1.6 (+https://github.com/Siesge/RealtyWatch)"
ACCEPT_HEADER = "image/png,*/*"
_CARTO_SUBS = ("a", "b", "c")

_dl_sem = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)
_inflight_lock = threading.Lock()


class _InFlight:
    __slots__ = ("evt", "result")

    def __init__(self):
        self.evt = threading.Event()
        self.result: bytes | None = None


_inflight: dict[tuple[str, int, int, int], _InFlight] = {}


def validate_coords(z: int, x: int, y: int) -> tuple[int, int, int]:
    """Проверяет допустимость координат тайла Web Mercator (z=0..19, 0 <= x,y < 2^z)."""
    if isinstance(z, bool) or isinstance(x, bool) or isinstance(y, bool):
        raise ValueError("координаты тайла должны быть целыми числами")
    try:
        zi, xi, yi = int(z), int(x), int(y)
    except (TypeError, ValueError) as e:
        raise ValueError("координаты тайла должны быть целыми числами") from e
    if not (0 <= zi <= 19):
        raise ValueError(f"недопустимый масштаб z={zi} (допустимо 0..19)")
    limit = 1 << zi
    if not (0 <= xi < limit and 0 <= yi < limit):
        raise ValueError(f"недопустимые координаты тайла z={zi}, x={xi}, y={yi}")
    return zi, xi, yi


def tile_path(z: int, x: int, y: int) -> Path:
    zi, xi, yi = validate_coords(z, x, y)
    return TILES / str(zi) / str(xi) / f"{yi}.png"


def is_valid_png(data: bytes | None) -> bool:
    """Проверяет PNG-сигнатуру и разумные границы размера тайла (> 100 байт и < 2 МБ)."""
    return (
        isinstance(data, (bytes, bytearray))
        and MIN_TILE_BYTES < len(data) < MAX_TILE_BYTES
        and bytes(data[:8]) == PNG_MAGIC
    )


def upstream_urls(z: int, x: int, y: int) -> list[str]:
    zi, xi, yi = validate_coords(z, x, y)
    sub = _CARTO_SUBS[(xi + yi) % len(_CARTO_SUBS)]
    return [
        f"https://tile.openstreetmap.org/{zi}/{xi}/{yi}.png",
        f"https://{sub}.basemaps.cartocdn.com/rastertiles/voyager/{zi}/{xi}/{yi}.png",
    ]


def _fetch_url(url: str, timeout: float = FETCH_TIMEOUT_SEC) -> tuple[int, bytes]:
    """Загружает один тайл по HTTP/HTTPS с корректными заголовками приложения."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": ACCEPT_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode() or 0)
            body = resp.read(MAX_TILE_BYTES + 1)
            return status, body
    except urllib.error.HTTPError as e:
        try:
            e.close()
        except Exception:
            pass
        return int(e.code or 502), b""


def _read_cached(path: Path) -> tuple[bytes | None, bool]:
    """Возвращает (bytes, is_fresh) для валидного PNG-файла на диске или (None, False)."""
    if not path.is_file():
        return None, False
    try:
        st = path.stat()
        raw = path.read_bytes()
    except OSError:
        return None, False
    if not is_valid_png(raw):
        return None, False
    is_fresh = (time.time() - st.st_mtime) < TILE_TTL_SEC
    return raw, is_fresh


def _write_atomic(path: Path, data: bytes):
    """Атомарно сохраняет валидный PNG через временный .part-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.part")
    try:
        part.write_bytes(data)
        part.replace(path)
    except OSError:
        part.unlink(missing_ok=True)


def get_tile(z: int, x: int, y: int) -> bytes | None:
    """Возвращает PNG-байты тайла (из свежего кэша, из сети или из просроченного кэша при обрыве связи)."""
    zi, xi, yi = validate_coords(z, x, y)
    path = TILES / str(zi) / str(xi) / f"{yi}.png"

    cached, is_fresh = _read_cached(path)
    if cached is not None and is_fresh:
        return cached

    key = (str(TILES), zi, xi, yi)
    with _inflight_lock:
        existing = _inflight.get(key)
        if existing is None:
            slot = _InFlight()
            _inflight[key] = slot
            leader = True
        else:
            slot = existing
            leader = False

    if not leader:
        slot.evt.wait(timeout=FETCH_TIMEOUT_SEC * 3 + 5.0)
        return slot.result if slot.result is not None else cached

    try:
        # Повторная быстрая проверка диска, если соседний запрос только что обновил файл
        disk_now, fresh_now = _read_cached(path)
        if disk_now is not None:
            cached = disk_now
            if fresh_now:
                slot.result = disk_now
                return disk_now

        with _dl_sem:
            for url in upstream_urls(zi, xi, yi):
                try:
                    status, raw = _fetch_url(url, timeout=FETCH_TIMEOUT_SEC)
                except Exception:
                    continue
                if status == 200 and is_valid_png(raw):
                    _write_atomic(path, raw)
                    slot.result = raw
                    return raw

        # Все источники недоступны: отдаём просроченный валидный тайл с диска без перезаписи
        if cached is not None:
            slot.result = cached
            return cached
        return None
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        slot.evt.set()
