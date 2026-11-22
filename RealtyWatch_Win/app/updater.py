"""Обновление приложения через GitHub Releases (Siesge/RealtyWatch).

Гарантии механизма обновления:
1. Запрашивает последний стабильный релиз на GitHub и выбирает строго переносной архив
   RealtyWatch-*-windows-portable.zip (без чужой базы), проверяет SHA-256 и целостность ZIP.
2. Полная неприкосновенность пользовательских данных: папки database/ (realty.db, заметки,
   объединённые карточки, лайки, дизлайки, настройки поиска, фото, тайлы, бэкапы), profiles/,
   data/, logs/, exports/, runtime/ и browsers/ находятся в жёстком стоп-листе апдейтера.
3. Перед установкой создаётся страховочная копия базы database/backups/перед_обновлением_*.zip
   (с автоматической ротацией старых автокопий обновления).
4. Наведение порядка и уборка мусора: управляемые папки кода и ресурсов (app/, web/, assets/,
   launcher/, tests/) и корневые файлы релиза приводятся в точное соответствие новому релизу —
   исчезнувшие в новой версии файлы, пустые папки, __pycache__, .pyc и временные .tmp/.part/.bak
   удаляются автоматически, а при сбое срабатывает полный откат из локальной копии rollback/.
5. На Windows замена файлов и перезапуск выполняются отдельным фоновым скриптом после штатного
   закрытия процесса pythonw.exe и освобождения data/app.lock, затем при необходимости setup.ps1 -Quick
   доустанавливает новые библиотеки из requirements.txt и запускает RealtyWatch.exe."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import db, paths, settings, transfer

log = logging.getLogger("realtywatch")

GITHUB_REPO = "Siesge/RealtyWatch"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "RealtyWatch-Updater/1.6 (+https://github.com/Siesge/RealtyWatch)"

INTERVAL_SECONDS = {
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
}

# Папки пользователя и среды — НИКОГДА не затираются и не очищаются при обновлении версии
PROTECTED_ROOTS = frozenset({
    "database", "profiles", "data", "logs", "exports", "runtime", "browsers", ".git", ".venv",
})

# Папки кода и ресурсов приложения — полностью синхронизируются с новым релизом (лишнее удаляется)
MANAGED_DIRS = ("app", "web", "assets", "launcher", "tests")

GARBAGE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
GARBAGE_SUFFIXES = (".pyc", ".pyo", ".tmp", ".part", ".bak", ".old", ".orig")

KEEP_UPDATE_BACKUPS = 3
MAX_ZIP_BYTES = 80 * 1024 * 1024
HTTP_TIMEOUT_SEC = 25.0

# Подменяются в тестах для полной изоляции во временной папке
APP_ROOT: Path = paths.ROOT
DATA_DIR: Path = paths.DATA


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _state_file(data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / "updater.json"


def _manifest_file(data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / "installed_files.json"


def _staging_dir(data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / "update_staging"


def _rollback_dir(data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / "update_rollback"


def parse_version(raw: str | None) -> tuple[int, ...]:
    """Разбирает строку версии ('v1.6', 'RealtyWatch 1.5, сборка...', '1.6.0') в кортеж чисел для сравнения."""
    if not raw:
        return ()
    txt = str(raw).strip()
    head = txt.split(",")[0].strip()
    if head.lower().startswith("realtywatch"):
        head = head[len("realtywatch"):].strip()
    head = head.lstrip("vV ").strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", head)
    if not m:
        return ()
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def clean_version_label(raw: str | None) -> str:
    """Человекочитаемый номер версии ('v1.6' -> '1.6')."""
    if not raw:
        return ""
    txt = str(raw).strip().split(",")[0].strip()
    if txt.lower().startswith("realtywatch"):
        txt = txt[len("realtywatch"):].strip()
    txt = txt.lstrip("vV ").strip()
    m = re.match(r"^(\d+(?:\.\d+)*(?:-[A-Za-z0-9._-]+)?)", txt)
    return m.group(1) if m else txt


def read_local_version(root: Path | None = None) -> str:
    """Читает текущую версию установленного приложения из version.txt."""
    f = Path(root or APP_ROOT) / "version.txt"
    try:
        first = f.read_text(encoding="utf-8-sig").strip().splitlines()[0]
    except (OSError, IndexError):
        return ""
    return clean_version_label(first)


def is_dev_worktree(root: Path | None = None) -> bool:
    """True, если программа запущена напрямую из git-репозитория разработки без релизного version.txt."""
    r = Path(root or APP_ROOT)
    return not (r / "version.txt").is_file() and ((r / ".git").exists() or (r.parent / ".git").exists())


def is_newer_version(remote_ver: str, local_ver: str) -> bool:
    rv = parse_version(remote_ver)
    lv = parse_version(local_ver)
    if not rv:
        return False
    if not lv:
        return True
    return rv > lv


def _fetch_bytes(url: str, accept: str = "*/*", timeout: float = HTTP_TIMEOUT_SEC, max_bytes: int = MAX_ZIP_BYTES) -> tuple[int, bytes]:
    """Сетевой загрузчик (в тестах изолируется в tests/support.py аналогично tiles._fetch_url)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode() or 0)
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("размер файла обновления превышает допустимый предел")
            return status, data
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096)
            e.close()
        except Exception:
            body = b""
        return int(e.code or 502), body


def select_portable_asset(release: dict) -> dict:
    """Извлекает метаданные релиза GitHub и выбирает строго portable-архив Windows (без базы)."""
    if not isinstance(release, dict):
        raise ValueError("некорректный ответ GitHub API")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("последний релиз помечен как черновик или пре-релиз")
    tag = str(release.get("tag_name") or "").strip()
    ver = clean_version_label(tag or release.get("name") or "")
    if not parse_version(ver):
        raise ValueError(f"не удалось распознать номер версии релиза: {tag!r}")

    assets = release.get("assets") or []
    chosen = None
    fallback = None
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip()
        low = name.lower()
        if not low.endswith(".zip"):
            continue
        if "базой" in low or "with-db" in low or "with_db" in low:
            continue
        if any(bad in low for bad in ("mac", "darwin", "osx", "linux")):
            continue
        if "windows" in low and "portable" in low:
            chosen = a
            break
        if "portable" in low and fallback is None:
            fallback = a

    asset = chosen or fallback
    if not asset:
        raise ValueError("в релизе GitHub не найден архив RealtyWatch-*-windows-portable.zip")
    dl_url = str(asset.get("browser_download_url") or "").strip()
    if not dl_url:
        raise ValueError("у архива релиза отсутствует ссылка для скачивания")

    digest = str(asset.get("digest") or "").strip()
    sha256 = ""
    if digest.lower().startswith("sha256:"):
        cand = digest.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", cand):
            sha256 = cand

    return {
        "version": ver,
        "tag": tag or f"v{ver}",
        "title": str(release.get("name") or tag or f"RealtyWatch {ver}").strip(),
        "notes": str(release.get("body") or "").strip()[:12000],
        "html_url": str(release.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases").strip(),
        "published_at": str(release.get("published_at") or "").strip(),
        "asset_name": str(asset.get("name") or "").strip(),
        "download_url": dl_url,
        "size": int(asset.get("size") or 0),
        "sha256": sha256,
    }


def validate_and_unpack_release(zip_path: Path, dest_dir: Path, expected_sha256: str = "") -> list[str]:
    """Проверяет SHA-256 и целостность ZIP-архива релиза, защищает от перезаписи database/ и распаковывает в dest_dir."""
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    if not zip_path.is_file():
        raise ValueError("архив обновления не найден на диске")
    raw = zip_path.read_bytes()
    if len(raw) < 64:
        raise ValueError("архив обновления повреждён или пуст")
    if expected_sha256:
        got_hash = hashlib.sha256(raw).hexdigest().lower()
        if got_hash != expected_sha256.lower():
            raise ValueError("контрольная сумма архива обновления (SHA-256) не совпала")

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as e:
        raise ValueError("файл обновления не является корректным ZIP-архивом") from e

    with zf:
        bad_member = zf.testzip()
        if bad_member is not None:
            raise ValueError(f"повреждён файл внутри архива обновления: {bad_member}")
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise ValueError("архив обновления пуст")

        raw_paths = [i.filename.replace("\\", "/").lstrip("/") for i in infos]
        # make_release.py упаковывает все файлы с общим префиксом 'RealtyWatch/'
        first_segs = {p.split("/", 1)[0] for p in raw_paths if "/" in p}
        strip_prefix = ""
        if len(first_segs) == 1 and all("/" in p for p in raw_paths):
            cand_prefix = next(iter(first_segs))
            if cand_prefix.lower().startswith("realtywatch"):
                strip_prefix = cand_prefix + "/"

        entries: list[tuple[zipfile.ZipInfo, str]] = []
        rel_set: set[str] = set()
        for info, full_p in zip(infos, raw_paths):
            rel = full_p[len(strip_prefix):] if strip_prefix and full_p.startswith(strip_prefix) else full_p
            parts = [seg for seg in rel.split("/") if seg]
            if not parts or any(seg in (".", "..") for seg in parts):
                raise ValueError(f"небезопасный путь в архиве обновления: {info.filename}")
            top = parts[0].lower()
            if top == "database":
                raise ValueError("архив содержит папку database — обновление допускается только из portable-архива без базы")
            if top in PROTECTED_ROOTS:
                continue
            norm_rel = "/".join(parts)
            entries.append((info, norm_rel))
            rel_set.add(norm_rel)

        for required in ("app/main.py", "web/index.html"):
            if required not in rel_set:
                raise ValueError(f"в архиве обновления отсутствует обязательный компонент: {required}")

        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for info, norm_rel in entries:
            out_file = dest_dir / norm_rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(zf.read(info))

    return sorted(rel_set)


def sync_release_tree(unpacked_dir: Path, app_root: Path, data_dir: Path | None = None, new_version: str = "") -> dict:
    """Синхронизирует файлы нового релиза в app_root с полным откатом при сбое и уборкой устаревших/мусорных файлов.
    Никогда не затрагивает PROTECTED_ROOTS (database/, profiles/, data/, logs/, exports/, runtime/, browsers/)."""
    unpacked_dir = Path(unpacked_dir).resolve()
    app_root = Path(app_root).resolve()
    eff_data = Path(data_dir).resolve() if data_dir is not None else (app_root / "data").resolve()
    eff_data.mkdir(parents=True, exist_ok=True)

    manifest_path = eff_data / "installed_files.json"
    state_path = eff_data / "updater.json"
    rollback_dir = eff_data / "update_rollback"

    new_files: set[str] = set()
    for p in unpacked_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(unpacked_dir).as_posix()
        top = rel.split("/", 1)[0].lower()
        if top in PROTECTED_ROOTS:
            raise ValueError(f"попытка записи в защищённую папку {top} отклонена")
        if p.name.lower().endswith(GARBAGE_SUFFIXES) or "__pycache__" in p.parts:
            continue
        new_files.add(rel)

    if "app/main.py" not in new_files or "web/index.html" not in new_files:
        raise ValueError("распакованный релиз неполон (нет app/main.py или web/index.html)")

    prev_installed: set[str] = set()
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                prev_installed = {str(x).replace("\\", "/") for x in loaded if isinstance(x, str)}
        except (OSError, ValueError):
            prev_installed = set()

    # Создаём локальную точку отката управляемых папок и корневых файлов на случай сбоя копирования
    if rollback_dir.exists():
        shutil.rmtree(rollback_dir, ignore_errors=True)
    rollback_dir.mkdir(parents=True, exist_ok=True)

    backed_up_roots: list[str] = []
    for dname in MANAGED_DIRS:
        src_d = app_root / dname
        if src_d.is_dir():
            shutil.copytree(
                src_d,
                rollback_dir / dname,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp", "*.part"),
            )
            backed_up_roots.append(dname)
    for item in app_root.iterdir():
        if item.is_file() and item.name.lower() not in PROTECTED_ROOTS:
            shutil.copy2(item, rollback_dir / item.name)
            backed_up_roots.append(item.name)

    removed_files: list[str] = []
    cleaned_garbage: list[str] = []

    try:
        # 1. Атомарно записываем все файлы нового релиза
        for rel in sorted(new_files):
            src_f = unpacked_dir / rel
            dst_f = app_root / rel
            dst_f.parent.mkdir(parents=True, exist_ok=True)
            tmp_f = dst_f.with_name(dst_f.name + ".rw_upd_tmp")
            try:
                shutil.copy2(src_f, tmp_f)
                os.replace(tmp_f, dst_f)
            finally:
                if tmp_f.exists():
                    tmp_f.unlink(missing_ok=True)

        # 2. Удаляем устаревшие файлы и мусор внутри управляемых папок (app/, web/, assets/, launcher/, tests/)
        for dname in MANAGED_DIRS:
            mdir = app_root / dname
            if not mdir.is_dir():
                continue
            for root_s, dirs, files in os.walk(mdir, topdown=False):
                cur_dir = Path(root_s)
                for fn in files:
                    fp = cur_dir / fn
                    rel_p = fp.relative_to(app_root).as_posix()
                    is_junk = cur_dir.name.lower() in GARBAGE_DIRS or fn.lower().endswith(GARBAGE_SUFFIXES)
                    if is_junk:
                        fp.unlink(missing_ok=True)
                        cleaned_garbage.append(rel_p)
                    elif rel_p not in new_files:
                        fp.unlink(missing_ok=True)
                        removed_files.append(rel_p)
                for dn in dirs:
                    dp = cur_dir / dn
                    if dn.lower() in GARBAGE_DIRS:
                        shutil.rmtree(dp, ignore_errors=True)
                        cleaned_garbage.append(dp.relative_to(app_root).as_posix() + "/")
                    else:
                        try:
                            if dp.is_dir() and not any(dp.iterdir()):
                                dp.rmdir()
                        except OSError:
                            pass

        # 3. Удаляем устаревшие корневые файлы из прежнего манифеста и мусорные временные файлы в корне
        for prev_rel in sorted(prev_installed - new_files):
            if "/" not in prev_rel and prev_rel.lower() not in PROTECTED_ROOTS:
                old_root_f = app_root / prev_rel
                if old_root_f.is_file():
                    old_root_f.unlink(missing_ok=True)
                    removed_files.append(prev_rel)

        for item in list(app_root.iterdir()):
            if item.name.lower() in PROTECTED_ROOTS:
                continue
            if item.is_file() and item.name.lower().endswith(GARBAGE_SUFFIXES):
                item.unlink(missing_ok=True)
                cleaned_garbage.append(item.name)
            elif item.is_dir() and item.name.lower() in GARBAGE_DIRS:
                shutil.rmtree(item, ignore_errors=True)
                cleaned_garbage.append(item.name + "/")

        # 4. Сохраняем новый манифест установленных файлов и обновляем состояние апдейтера
        manifest_path.write_text(json.dumps(sorted(new_files), ensure_ascii=False, indent=2), encoding="utf-8")

        st_data = {}
        if state_path.is_file():
            try:
                st_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                st_data = {}
        eff_ver = new_version or read_local_version(app_root)
        st_data.update({
            "ready": False,
            "available_version": "",
            "zip_path": "",
            "sha256": "",
            "last_error": "",
            "just_updated_to": eff_ver,
            "updated_at": _now_iso(),
        })
        state_path.write_text(json.dumps(st_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Полный откат управляемых папок и корневых файлов из rollback_dir при любом сбое
        for dname in MANAGED_DIRS:
            rb_d = rollback_dir / dname
            dst_d = app_root / dname
            if rb_d.is_dir():
                shutil.rmtree(dst_d, ignore_errors=True)
                shutil.copytree(rb_d, dst_d)
        for item in rollback_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, app_root / item.name)
        raise
    finally:
        shutil.rmtree(rollback_dir, ignore_errors=True)

    return {
        "ok": True,
        "version": new_version or read_local_version(app_root),
        "updated_files": len(new_files),
        "removed_files": sorted(set(removed_files)),
        "cleaned_garbage": sorted(set(cleaned_garbage)),
    }


def create_pre_update_backup(target_version: str = "") -> str:
    """Создаёт страховочный бэкап базы перед обновлением и удаляет лишние старые автокопии обновления."""
    slug = re.sub(r"[^0-9A-Za-z._-]+", "_", target_version or "new").strip("_") or "new"
    backup_path = transfer.backup(f"перед_обновлением_v{slug}", with_images=False)
    # Ротация старых копий 'перед_обновлением_*', чтобы не накапливался мусор
    bdir = transfer.BACKUPS
    if bdir.exists():
        upd_backups = sorted(
            bdir.glob("перед_обновлением_*.zip"),
            key=lambda p: p.stat().st_mtime,
        )
        for old_b in upd_backups[:-KEEP_UPDATE_BACKUPS] if len(upd_backups) > KEEP_UPDATE_BACKUPS else []:
            try:
                old_b.unlink(missing_ok=True)
            except OSError:
                pass
    return backup_path


_STANDALONE_APPLY_SCRIPT = '''"""Автономный установщик подготовленного обновления RealtyWatch (запускается с ключом python -I
после закрытия основного процесса, использует исключительно стандартную библиотеку Python)."""
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROTECTED_ROOTS = {"database", "profiles", "data", "logs", "exports", "runtime", "browsers", ".git", ".venv"}
MANAGED_DIRS = ("app", "web", "assets", "launcher", "tests")
GARBAGE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
GARBAGE_SUFFIXES = (".pyc", ".pyo", ".tmp", ".part", ".bak", ".old", ".orig")


def apply_tree(unpacked_dir: Path, app_root: Path, data_dir: Path, new_version: str):
    manifest_path = data_dir / "installed_files.json"
    state_path = data_dir / "updater.json"
    rollback_dir = data_dir / "update_rollback"

    new_files = set()
    for p in unpacked_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(unpacked_dir).as_posix()
        top = rel.split("/", 1)[0].lower()
        if top in PROTECTED_ROOTS:
            raise RuntimeError(f"Refusing to touch protected dir: {top}")
        if p.name.lower().endswith(GARBAGE_SUFFIXES) or "__pycache__" in p.parts:
            continue
        new_files.add(rel)

    prev_installed = set()
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                prev_installed = {str(x).replace("\\\\", "/") for x in loaded if isinstance(x, str)}
        except Exception:
            pass

    if rollback_dir.exists():
        shutil.rmtree(rollback_dir, ignore_errors=True)
    rollback_dir.mkdir(parents=True, exist_ok=True)

    for dname in MANAGED_DIRS:
        src_d = app_root / dname
        if src_d.is_dir():
            shutil.copytree(src_d, rollback_dir / dname, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.tmp", "*.part"))
    for item in app_root.iterdir():
        if item.is_file() and item.name.lower() not in PROTECTED_ROOTS:
            shutil.copy2(item, rollback_dir / item.name)

    try:
        for rel in sorted(new_files):
            src_f = unpacked_dir / rel
            dst_f = app_root / rel
            dst_f.parent.mkdir(parents=True, exist_ok=True)
            tmp_f = dst_f.with_name(dst_f.name + ".rw_upd_tmp")
            try:
                shutil.copy2(src_f, tmp_f)
                os.replace(tmp_f, dst_f)
            finally:
                if tmp_f.exists():
                    tmp_f.unlink(missing_ok=True)

        for dname in MANAGED_DIRS:
            mdir = app_root / dname
            if not mdir.is_dir():
                continue
            for root_s, dirs, files in os.walk(mdir, topdown=False):
                cur_dir = Path(root_s)
                for fn in files:
                    fp = cur_dir / fn
                    rel_p = fp.relative_to(app_root).as_posix()
                    if cur_dir.name.lower() in GARBAGE_DIRS or fn.lower().endswith(GARBAGE_SUFFIXES) or rel_p not in new_files:
                        fp.unlink(missing_ok=True)
                for dn in dirs:
                    dp = cur_dir / dn
                    if dn.lower() in GARBAGE_DIRS:
                        shutil.rmtree(dp, ignore_errors=True)
                    else:
                        try:
                            if dp.is_dir() and not any(dp.iterdir()):
                                dp.rmdir()
                        except OSError:
                            pass

        for prev_rel in sorted(prev_installed - new_files):
            if "/" not in prev_rel and prev_rel.lower() not in PROTECTED_ROOTS:
                old_f = app_root / prev_rel
                if old_f.is_file():
                    old_f.unlink(missing_ok=True)

        for item in list(app_root.iterdir()):
            if item.name.lower() in PROTECTED_ROOTS:
                continue
            if item.is_file() and item.name.lower().endswith(GARBAGE_SUFFIXES):
                item.unlink(missing_ok=True)
            elif item.is_dir() and item.name.lower() in GARBAGE_DIRS:
                shutil.rmtree(item, ignore_errors=True)

        manifest_path.write_text(json.dumps(sorted(new_files), ensure_ascii=False, indent=2), encoding="utf-8")
        st_data = {}
        if state_path.is_file():
            try:
                st_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                st_data = {}
        st_data.update({
            "ready": False,
            "available_version": "",
            "zip_path": "",
            "sha256": "",
            "last_error": "",
            "just_updated_to": new_version,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        state_path.write_text(json.dumps(st_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        for dname in MANAGED_DIRS:
            rb_d = rollback_dir / dname
            dst_d = app_root / dname
            if rb_d.is_dir():
                shutil.rmtree(dst_d, ignore_errors=True)
                shutil.copytree(rb_d, dst_d)
        for item in rollback_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, app_root / item.name)
        raise
    finally:
        shutil.rmtree(rollback_dir, ignore_errors=True)


if __name__ == "__main__":
    unpacked = Path(sys.argv[1]).resolve()
    root = Path(sys.argv[2]).resolve()
    data = Path(sys.argv[3]).resolve()
    ver = sys.argv[4] if len(sys.argv) > 4 else ""
    log_file = root / "logs" / "update.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        apply_tree(unpacked, root, data, ver)
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} успешно обновлено до версии {ver}\\n")
    except Exception:
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} сбой обновления:\\n{traceback.format_exc()}\\n")
        sys.exit(1)
'''


def build_powershell_runner(app_root: Path, data_dir: Path, unpacked_dir: Path, apply_py: Path, pid: int, version: str) -> str:
    """Генерирует PowerShell-скрипт ожидания выхода старого процесса, установки релиза, проверки пакетов и перезапуска."""
    py_exe = app_root / "runtime" / "python" / "python.exe"
    if not py_exe.is_file():
        py_exe = Path(sys.executable)
    setup_ps1 = app_root / "setup.ps1"
    launcher_exe = app_root / "RealtyWatch.exe"
    start_bat = app_root / "start.bat"
    staging = _staging_dir(data_dir)
    q = lambda p: str(p).replace("'", "''")
    return "\n".join([
        "$ErrorActionPreference = 'Continue'",
        f"$TargetPid = {int(pid)}",
        f"$AppRoot   = '{q(app_root)}'",
        f"$DataDir   = '{q(data_dir)}'",
        f"$Unpacked  = '{q(unpacked_dir)}'",
        f"$ApplyPy   = '{q(apply_py)}'",
        f"$PyExe     = '{q(py_exe)}'",
        f"$SetupPs1  = '{q(setup_ps1)}'",
        f"$ExePath   = '{q(launcher_exe)}'",
        f"$StartBat  = '{q(start_bat)}'",
        f"$Staging   = '{q(staging)}'",
        f"$NewVer    = '{q(version)}'",
        "",
        "# 1. Ждём завершения старого процесса RealtyWatch (до 35 с)",
        "for ($i = 0; $i -lt 140; $i++) {",
        "    $proc = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue",
        "    if (-not $proc) { break }",
        "    Start-Sleep -Milliseconds 250",
        "}",
        "Start-Sleep -Milliseconds 400",
        "",
        "# 2. Применяем файлы релиза и вычищаем устаревшие файлы / мусор",
        "& $PyExe -I $ApplyPy $Unpacked $AppRoot $DataDir $NewVer",
        "$ApplyCode = $LASTEXITCODE",
        "",
        "# 3. Если requirements.txt обновился — тихо доустанавливаем библиотеки через setup.ps1 -Quick",
        "if ($ApplyCode -eq 0 -and (Test-Path $SetupPs1)) {",
        "    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SetupPs1 -Quick | Out-Null",
        "}",
        "",
        "# 4. Удаляем временную папку скачивания и распаковки",
        "if (Test-Path $Staging) {",
        "    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue",
        "}",
        "",
        "# 5. Запускаем обновлённое приложение",
        "if (Test-Path $ExePath) {",
        "    Start-Process -FilePath $ExePath -WorkingDirectory $AppRoot",
        "} elseif (Test-Path $StartBat) {",
        "    Start-Process -FilePath $StartBat -WorkingDirectory $AppRoot",
        "}",
        "",
    ])


class UpdaterService:
    """Управление фоновой проверкой релизов по расписанию (день / неделя / месяц), скачиванием и установкой."""

    def __init__(self):
        self._lock = threading.Lock()
        self._op_lock = threading.Lock()
        self._checking = False
        self._downloading = False
        self._dismissed_version = ""
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._shutdown_cb: Callable[[], None] | None = None
        self._notify_cb: Callable[[str, str], None] | None = None

    def set_hooks(self, shutdown_cb: Callable[[], None] | None = None, notify_cb: Callable[[str, str], None] | None = None):
        self._shutdown_cb = shutdown_cb
        self._notify_cb = notify_cb

    def cleanup_leftovers(self):
        """Удаляет остатки временных папок обновления после перезапуска (если нет ожидающего установки архива)."""
        try:
            st = self._read_state()
            if not st.get("ready"):
                shutil.rmtree(_staging_dir(), ignore_errors=True)
            shutil.rmtree(_rollback_dir(), ignore_errors=True)
        except Exception:
            pass

    def start_background(self):
        self.cleanup_leftovers()
        if os.environ.get("RW_NO_UPDATER"):
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._bg_loop, daemon=True, name="updater")
            self._thread.start()

    def stop_background(self):
        self._stop_evt.set()

    def _read_state(self) -> dict:
        sf = _state_file()
        base = {
            "last_checked": "",
            "available_version": "",
            "title": "",
            "notes": "",
            "html_url": "",
            "published_at": "",
            "asset_name": "",
            "size": 0,
            "sha256": "",
            "zip_path": "",
            "ready": False,
            "last_error": "",
            "just_updated_to": "",
            "updated_at": "",
        }
        if sf.is_file():
            try:
                loaded = json.loads(sf.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    base.update(loaded)
            except (OSError, ValueError):
                pass
        # Если ранее скачанный файл был удалён с диска или текущая версия уже догнала скачанную — сбрасываем ready
        local_ver = read_local_version()
        avail = str(base.get("available_version") or "")
        zp = str(base.get("zip_path") or "")
        if base.get("ready"):
            if not zp or not Path(zp).is_file() or (local_ver and avail and not is_newer_version(avail, local_ver)):
                base["ready"] = False
                base["zip_path"] = ""
                if local_ver and avail and not is_newer_version(avail, local_ver):
                    base["available_version"] = ""
        return base

    def _write_state(self, st: dict):
        sf = _state_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        tmp = sf.with_name(sf.name + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, sf)

    def _bump_seq(self):
        with self._lock:
            self._seq += 1

    def get_config(self) -> dict:
        cfg = settings.load().get("updates") or {}
        interval = str(cfg.get("interval") or "week")
        if interval not in INTERVAL_SECONDS:
            interval = "week"
        return {
            "auto_check": bool(cfg.get("auto_check", True)),
            "interval": interval,
        }

    def status(self) -> dict:
        st = self._read_state()
        ucfg = self.get_config()
        local_ver = read_local_version()
        dev = is_dev_worktree()
        with self._lock:
            checking = self._checking
            downloading = self._downloading
            seq = self._seq
            dismissed = self._dismissed_version
        avail = str(st.get("available_version") or "")
        ready = bool(st.get("ready"))
        return {
            "current_version": local_ver,
            "dev_mode": dev,
            "auto_check": ucfg["auto_check"],
            "interval": ucfg["interval"],
            "checking": checking,
            "downloading": downloading,
            "last_checked": st.get("last_checked") or "",
            "last_error": st.get("last_error") or "",
            "available_version": avail,
            "has_update": bool(avail and (not local_ver and not dev or (local_ver and is_newer_version(avail, local_ver)) or (dev and bool(avail)))),
            "ready": ready,
            "dismissed": bool(avail and dismissed == avail),
            "title": st.get("title") or "",
            "notes": st.get("notes") or "",
            "html_url": st.get("html_url") or "",
            "published_at": st.get("published_at") or "",
            "size_kb": round((int(st.get("size") or 0)) / 1024) if st.get("size") else 0,
            "just_updated_to": st.get("just_updated_to") or "",
            "updated_at": st.get("updated_at") or "",
            "seq": seq,
        }

    def summary(self) -> dict:
        """Компактная сводка для /api/status (фоновый опрос раз в 2 с)."""
        s = self.status()
        return {
            "seq": s["seq"],
            "current_version": s["current_version"],
            "available_version": s["available_version"],
            "ready": s["ready"],
            "dismissed": s["dismissed"],
            "title": s["title"],
            "notes": s["notes"],
            "html_url": s["html_url"],
            "dev_mode": s["dev_mode"],
            "just_updated_to": s["just_updated_to"],
        }

    def acknowledge(self, dismiss_banner: bool = False, clear_just_updated: bool = False) -> dict:
        st = self._read_state()
        if dismiss_banner and st.get("available_version"):
            with self._lock:
                self._dismissed_version = str(st["available_version"])
        if clear_just_updated and st.get("just_updated_to"):
            st["just_updated_to"] = ""
            self._write_state(st)
        self._bump_seq()
        return self.status()

    def is_due(self) -> bool:
        ucfg = self.get_config()
        if not ucfg["auto_check"]:
            return False
        st = self._read_state()
        last = st.get("last_checked") or ""
        if not last:
            return True
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            return True
        return elapsed >= INTERVAL_SECONDS[ucfg["interval"]]

    def check_for_updates(self, manual: bool = False, download: bool = True) -> dict:
        """Проверяет последний релиз на GitHub и при наличии более новой версии скачивает и проверяет архив."""
        if not self._op_lock.acquire(blocking=manual):
            return self.status()
        extra_fields: dict = {}
        try:
            with self._lock:
                self._checking = True
            st = self._read_state()
            now_s = _now_iso()
            local_ver = read_local_version()
            dev = is_dev_worktree()

            code, body = _fetch_bytes(
                RELEASES_LATEST_URL,
                accept="application/vnd.github+json",
                timeout=HTTP_TIMEOUT_SEC,
                max_bytes=4 * 1024 * 1024,
            )
            if code == 404:
                st.update({"last_checked": now_s, "last_error": ""})
                self._write_state(st)
                self._bump_seq()
            elif code != 200:
                err_msg = f"GitHub ответил HTTP {code}"
                st.update({"last_checked": now_s, "last_error": err_msg})
                self._write_state(st)
                self._bump_seq()
                if manual:
                    raise ValueError(f"Не удалось проверить обновления ({err_msg})")
            else:
                try:
                    rel_json = json.loads(body.decode("utf-8", errors="replace"))
                except ValueError as e:
                    raise ValueError("некорректный JSON от GitHub Releases API") from e

                info = select_portable_asset(rel_json)
                remote_ver = info["version"]
                st["last_checked"] = now_s
                st["last_error"] = ""

                # Сравнение версий (в режиме разработки без version.txt сообщаем о релизе, но не перезаписываем репо)
                newer = is_newer_version(remote_ver, local_ver) if local_ver else (not dev)
                existing_zip = Path(st["zip_path"]) if st.get("zip_path") else None
                if not newer:
                    if dev:
                        # В git-репозитории просто информируем о последнем опубликованном релизе без фонового скачивания
                        st.update({
                            "available_version": "",
                            "ready": False,
                            "title": info["title"],
                            "notes": info["notes"],
                            "html_url": info["html_url"],
                            "published_at": info["published_at"],
                        })
                    else:
                        st.update({"available_version": "", "ready": False, "zip_path": ""})
                    self._write_state(st)
                    self._bump_seq()
                    extra_fields["latest_github_version"] = remote_ver
                elif (
                    st.get("ready")
                    and st.get("available_version") == remote_ver
                    and existing_zip is not None
                    and existing_zip.is_file()
                ):
                    # Если этот же релиз уже скачан и проверен на диске — повторно не качаем
                    if manual:
                        with self._lock:
                            self._dismissed_version = ""
                    self._write_state(st)
                    self._bump_seq()
                else:
                    st.update({
                        "available_version": remote_ver,
                        "title": info["title"],
                        "notes": info["notes"],
                        "html_url": info["html_url"],
                        "published_at": info["published_at"],
                        "asset_name": info["asset_name"],
                        "size": info["size"],
                        "sha256": info["sha256"],
                        "ready": False,
                    })
                    self._write_state(st)

                    if not download:
                        self._bump_seq()
                    else:
                        with self._lock:
                            self._downloading = True

                        sdir = _staging_dir()
                        sdir.mkdir(parents=True, exist_ok=True)
                        # Удаляем старые архивы из staging, чтобы не копился мусор
                        for old_f in sdir.glob("*.zip*"):
                            old_f.unlink(missing_ok=True)

                        safe_asset = re.sub(
                            r'[\\/:*?"<>|]+',
                            "_",
                            info["asset_name"] or f"RealtyWatch-{remote_ver}-windows-portable.zip",
                        )
                        part_file = sdir / (safe_asset + ".part")
                        final_zip = sdir / safe_asset

                        dl_code, zip_bytes = _fetch_bytes(
                            info["download_url"],
                            accept="application/octet-stream,*/*",
                            timeout=90.0,
                            max_bytes=MAX_ZIP_BYTES,
                        )
                        if dl_code != 200 or not zip_bytes:
                            raise ValueError(f"не удалось скачать архив обновления (HTTP {dl_code})")
                        if info["size"] and len(zip_bytes) != info["size"]:
                            raise ValueError(
                                f"размер скачанного архива ({len(zip_bytes)}) не совпадает с заявленным ({info['size']})"
                            )

                        part_file.write_bytes(zip_bytes)
                        try:
                            verify_dir = sdir / "verify_tmp"
                            validate_and_unpack_release(part_file, verify_dir, expected_sha256=info["sha256"])
                            shutil.rmtree(verify_dir, ignore_errors=True)
                            os.replace(part_file, final_zip)
                        finally:
                            part_file.unlink(missing_ok=True)

                        st["zip_path"] = str(final_zip)
                        st["size"] = len(zip_bytes)
                        st["ready"] = True
                        st["last_error"] = ""
                        self._write_state(st)
                        with self._lock:
                            self._dismissed_version = ""
                        self._bump_seq()

                        from .runner import manager

                        manager.log("db", f"обновление v{remote_ver} скачано и готово к установке")
                        if self._notify_cb and not manual:
                            try:
                                self._notify_cb(
                                    f"Доступно обновление RealtyWatch {remote_ver}",
                                    "Архив скачан и проверен. Откройте окно программы и нажмите «Обновить и перезапустить».",
                                )
                            except Exception:
                                pass
        except Exception as e:
            st = self._read_state()
            st["last_checked"] = _now_iso()
            st["last_error"] = str(e)
            self._write_state(st)
            self._bump_seq()
            if manual:
                raise
            log.warning("Фоновая проверка обновлений не удалась: %s", e)
        finally:
            with self._lock:
                self._checking = False
                self._downloading = False
            self._op_lock.release()
        res = self.status()
        if extra_fields:
            res.update(extra_fields)
        return res

    def apply_update(self, allow_dev: bool = False, spawn_installer: bool = True) -> dict:
        """Готовит страховочный бэкап БД, распаковывает проверенный релиз и запускает обновление с перезапуском."""
        from .runner import manager

        if manager.any_running():
            raise RuntimeError("BUSY_RUNNING")
        if is_dev_worktree() and not allow_dev:
            raise ValueError(
                "Программа запущена напрямую из рабочей папки git-репозитория (без version.txt). "
                "Чтобы не затереть незакоммиченный код разработки, автоматическая перезапись отключена."
            )

        st = self._read_state()
        zip_str = str(st.get("zip_path") or "")
        target_ver = str(st.get("available_version") or "")
        if not st.get("ready") or not zip_str or not Path(zip_str).is_file():
            raise ValueError("Архив обновления ещё не подготовлен — сначала нажмите «Проверить обновление»")

        sdir = _staging_dir()
        unpacked = sdir / "unpacked"
        validate_and_unpack_release(Path(zip_str), unpacked, expected_sha256=str(st.get("sha256") or ""))

        # Страховочный снимок базы (со всеми заметками, лайками, дизлайками и ручными объединениями)
        backup_path = create_pre_update_backup(target_ver)
        try:
            c = db.connect()
            try:
                c.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                c.close()
        except Exception:
            pass

        if not spawn_installer:
            sync_res = sync_release_tree(unpacked, APP_ROOT, DATA_DIR, new_version=target_ver)
            shutil.rmtree(sdir, ignore_errors=True)
            self._bump_seq()
            return {"ok": True, "version": target_ver, "backup": backup_path, **sync_res}

        apply_py = sdir / "apply_staged.py"
        run_ps1 = sdir / "run_update.ps1"
        apply_py.write_text(_STANDALONE_APPLY_SCRIPT, encoding="utf-8")
        ps_body = build_powershell_runner(APP_ROOT, DATA_DIR, unpacked, apply_py, os.getpid(), target_ver)
        run_ps1.write_text("\ufeff" + ps_body.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(run_ps1)],
            cwd=str(APP_ROOT.parent),
            creationflags=creation_flags,
            close_fds=True,
        )
        manager.log("db", f"установка обновления v{target_ver}: резервная копия создана ({Path(backup_path).name}), перезапуск…")

        if self._shutdown_cb:
            threading.Timer(0.45, self._shutdown_cb).start()
        return {"ok": True, "version": target_ver, "backup": backup_path, "restarting": True}

    def _bg_loop(self):
        # Мягкий отложенный первый опрос через 25 с после старта окна, далее проверка каждые 15 мин
        if self._stop_evt.wait(25.0):
            return
        while not self._stop_evt.is_set():
            try:
                from .runner import manager

                if self.is_due() and not manager.any_running():
                    self.check_for_updates(manual=False, download=True)
            except Exception:
                pass
            if self._stop_evt.wait(900.0):
                break


service = UpdaterService()
