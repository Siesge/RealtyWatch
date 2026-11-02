"""Очистка кэшей при закрытии программы: папку сразу можно заархивировать и переслать.

Удаляется только то, что браузер и программа создадут заново: кэш страниц, кода и шейдеров Chromium
в profiles/ (это сотни мегабайт), такой же кэш окна программы в data/webview/ и скомпилированные .pyc.
Cookies и вход на сайты, настройки, база, фотографии и журналы остаются: без cookies Авито увидел бы
новый профиль и сразу показал капчу. Кэш, который успел занять другой процесс, пропускается —
освободится при следующем закрытии."""
import os
import shutil
import time
from pathlib import Path

from .paths import DATA, PROFILES, ROOT

# Папки (на любой глубине), которые браузер и Python наполняют заново
CACHE_DIRS = {
    "cache", "code cache", "gpucache", "dawncache", "dawngraphitecache", "dawnwebgpucache",
    "grshadercache", "shadercache", "cachestorage", "scriptcache", "component_crx_cache",
    "extensions_crx_cache", "ondeviceheadsuggestmodel", "crashpad", "speech recognition",
    "widevinecdm", "subresource filter", "optimization_guide_model_store", "__pycache__",
}
CACHE_FILES = (".pma",)  # BrowserMetrics-spare.pma и подобные заготовки Chromium
BUDGET_S = 25  # дольше закрытие программы не задерживаем


def places():
    """Где искать кэши: профили сайтов, окно программы и скомпилированный код самой программы."""
    return [PROFILES, DATA / "webview", ROOT / "app", ROOT / "tests"]


def free_caches(roots=None, budget_s=BUDGET_S):
    """Удаляет кэши. Возвращает, сколько освободилось, в байтах."""
    end, freed = time.monotonic() + budget_s, 0
    for base in (roots if roots is not None else places()):
        base = Path(base)
        if not base.exists():
            continue
        for p in _targets(base):
            if time.monotonic() > end:
                return freed
            freed += _wipe(p)
    return freed


def _targets(base):
    """Кэши внутри base: вложенные в уже найденную папку отдельно не ищутся."""
    out = []
    for root, dirs, files in os.walk(base, onerror=lambda e: None):
        hit = [d for d in dirs if d.lower() in CACHE_DIRS]
        out += [Path(root) / d for d in hit]
        out += [Path(root) / f for f in files if f.lower().endswith(CACHE_FILES)]
        dirs[:] = [d for d in dirs if d not in hit]
    return out


def _wipe(p):
    """Удаляет папку или файл. Возвращает, сколько места освободилось."""
    before = _size(p)
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass
    return max(0, before - _size(p))


def _size(p):
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
