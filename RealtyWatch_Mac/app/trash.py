"""Удаление файлов в Корзину macOS — чтобы «удалить» можно было отменить.

«В Корзину» и «насовсем» для человека не одно и то же, поэтому программа различает эти случаи
и говорит, что именно произошло.

На Windows это делает одна команда оболочки, на macOS — три разных способа, и какой из них
доступен, зависит от сборки. Поэтому они пробуются по очереди, от родного к грубому:

1. NSFileManager через pyobjc. Родной способ: Finder запоминает, откуда файл, и предлагает
   «Положить обратно». pyobjc приходит вместе с pywebview (окно программы — Cocoa), но
   полагаться на это нельзя: сборка портативная, список библиотек может поменяться.
2. Finder через osascript. Тоже помнит, откуда файл. Finder на macOS работает всегда, а команда
   delete без activate не поднимает его поверх окна программы.
3. Перенос в ~/.Trash своими руками. «Положить обратно» Finder уже не предложит — он не знает,
   откуда файл, — но из Корзины его видно и достать можно. Это и есть смысл затеи.

Ничего из этого не должно ронять программу: не вышло в Корзину — файл удаляется обычным
способом, и пользователю пишется «безвозвратно».
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

MAC = sys.platform == "darwin"


def _volume_trash(path):
    """Папка Корзины для тома, на котором лежит path, — даже если её ещё нет. None — тома не найти.

    У домашнего тома это ~/.Trash. У остальных (внешний диск, образ) — .Trashes/<uid> в корне тома:
    корзина там своя, и перенос в домашнюю уводил бы файл с его тома.
    """
    p = Path(path)
    while not p.exists() and p != p.parent:     # папки копий может ещё не быть
        p = p.parent
    try:
        dev = p.stat().st_dev
        home = Path.home()
        if dev == home.stat().st_dev:
            return home / ".Trash"
        root = p                                # вверх, пока не сменился том — это и есть корень
        while root != root.parent and root.parent.stat().st_dev == dev:
            root = root.parent
        return root / ".Trashes" / str(os.getuid())
    except OSError:
        return None


def available(path):
    """Есть ли куда положить файл с этого пути.

    Домашнюю Корзину система создаёт сама, когда в неё впервые что-то кладут, — на свежем Маке
    папки ~/.Trash может не быть, и отказываться из-за этого от Корзины было бы неправильно.
    Поэтому для домашнего тома достаточно права писать в домашнюю папку. У чужого тома Корзина
    должна уже существовать: создавать её за систему программа не берётся.
    """
    if not MAC:
        return False
    t = _volume_trash(path)
    if t is None:
        return False
    if t.is_dir():
        return os.access(t, os.W_OK)
    return t.parent == Path.home() and os.access(Path.home(), os.W_OK)


def count(path):
    """Сколько всего лежит в Корзине этого тома — для проверки, что файл действительно туда попал.
    None — посмотреть не удалось."""
    t = _volume_trash(path) if MAC else None
    if t is None:
        return None
    if not t.exists():
        return 0                                # Корзины ещё нет — значит, в ней ничего и нет
    try:
        return len(list(t.iterdir()))
    except OSError:
        return None


def _ns_trash(files):
    """Способ 1: NSFileManager. False — pyobjc нет или он не справился."""
    try:
        from Foundation import NSURL, NSFileManager
    except ImportError:
        return False
    fm = NSFileManager.defaultManager()
    done = 0
    for p in files:
        ok, _, _ = fm.trashItemAtURL_resultingItemURL_error_(NSURL.fileURLWithPath_(str(p)), None, None)
        done += bool(ok)
    return done == len(files)


def _applescript_path(p):
    """Путь внутрь AppleScript: обратная косая и кавычка в строке экранируются."""
    s = str(p).replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')
    return 'POSIX file "' + s + '"'


def _finder_trash(files):
    """Способ 2: Finder. Одной командой на все файлы; activate не говорим, окна он не поднимает."""
    if not shutil.which("osascript"):
        return False
    items = ", ".join(_applescript_path(p.resolve()) for p in files)
    script = "tell application " + chr(34) + "Finder" + chr(34) + " to delete {" + items + "}"
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and all(not p.exists() for p in files)


def _free_name(folder, name):
    """Свободное имя в Корзине: рядом с «копия.zip» кладётся «копия 2.zip», как делает сам Finder."""
    p = folder / name
    if not p.exists():
        return p
    stem, suffix = Path(name).stem, Path(name).suffix
    for i in range(2, 1000):
        p = folder / f"{stem} {i}{suffix}"
        if not p.exists():
            return p
    return folder / f"{stem} {os.getpid()}{suffix}"


def _move_trash(files):
    """Способ 3: перенести самим. Только в пределах тома — иначе это была бы копия на другой диск,
    а не перенос в Корзину."""
    t = _volume_trash(files[0])
    if t is None:
        return False
    try:
        t.mkdir(parents=True, exist_ok=True)    # домашней Корзины может ещё не быть
    except OSError:
        return False
    done = 0
    for p in files:
        if _volume_trash(p) != t:
            continue
        try:
            shutil.move(str(p), str(_free_name(t, p.name)))
            done += 1
        except (OSError, shutil.Error):
            pass
    return done == len(files)


def route(path=None):
    """Каким способом файлы уйдут в Корзину — для журнала и для проверки сборки.
    Способ выбирается заранее, по тому, что есть в системе; какой сработал на самом деле,
    показывает delete()."""
    if not MAC:
        return None
    try:
        from Foundation import NSFileManager  # noqa: F401
        return "NSFileManager"
    except ImportError:
        pass
    if shutil.which("osascript"):
        return "Finder"
    if available(path or Path.home()):
        return "перенос в ~/.Trash"
    return None


def _to_trash(files):
    """Пробует способы по очереди. False — ни один не сработал.

    В тестах и проверках эту функцию подменяют пустышкой: Корзину хозяина трогать нельзя."""
    if not MAC or not files or not available(files[0]):
        return False
    for route_fn in (_ns_trash, _finder_trash, _move_trash):
        try:
            if route_fn(files):
                return True
        except Exception:  # способ не вышел — пробуем следующий, программа не падает
            pass
    return False


def delete(paths):
    """Удаляет файлы: в Корзину, если получилось, иначе насовсем.
    Возвращает (сколько удалено, попали ли в Корзину)."""
    files = [p for p in (Path(x) for x in paths) if p.is_file()]
    if not files:
        return 0, False
    trashed = _to_trash(files)
    if not trashed:
        for p in files:
            try:
                p.unlink()
            except OSError:
                pass
    return len([p for p in files if not p.exists()]), trashed
