"""Удаление файлов в корзину Windows — чтобы «удалить» можно было отменить.

Корзина есть не у каждого диска: на сетевом, сменном или с отключённой корзиной файл стирается
обычным способом. Программа различает эти случаи и сообщает, что именно произошло, — «в корзину»
и «насовсем» для пользователя не одно и то же.

Через ctypes: оболочка Windows умеет это сама, отдельная библиотека не нужна (сборка — портативная).
"""
import ctypes
import sys
from pathlib import Path

FO_DELETE = 3
FOF_SILENT = 0x4            # без окна прогресса
FOF_NOCONFIRMATION = 0x10   # без вопросов: подтверждение спрашивает сама программа
FOF_ALLOWUNDO = 0x40        # в корзину, а не насовсем
FOF_NOERRORUI = 0x400       # без системных сообщений об ошибке — ошибку разбираем сами


def _shell():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.shell32
    except (AttributeError, OSError):
        return None


def available(path):
    """Есть ли у диска, на котором лежит path, корзина."""
    sh = _shell()
    if sh is None:
        return False

    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]

    info = INFO()
    info.cbSize = ctypes.sizeof(INFO)
    try:
        root = Path(path).resolve().anchor or str(Path(path).resolve())
        return sh.SHQueryRecycleBinW(ctypes.c_wchar_p(root), ctypes.byref(info)) == 0
    except (AttributeError, OSError, ValueError):
        return False


def count(path):
    """Сколько файлов лежит в корзине диска (для проверки, что удаление действительно в неё попало)."""
    sh = _shell()
    if sh is None:
        return None

    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]

    info = INFO()
    info.cbSize = ctypes.sizeof(INFO)
    try:
        if sh.SHQueryRecycleBinW(ctypes.c_wchar_p(Path(path).resolve().anchor), ctypes.byref(info)) != 0:
            return None
    except (AttributeError, OSError, ValueError):
        return None
    return info.i64NumItems


def _to_trash(files):
    """Одной операцией оболочки. False — корзины нет или оболочка не справилась."""
    sh = _shell()
    if sh is None or not available(files[0]):
        return False

    class OP(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint32), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", ctypes.c_int32),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    # список путей для оболочки заканчивается двумя нулями: один ставит сам буфер
    names = ctypes.create_unicode_buffer(chr(0).join(str(p.resolve()) for p in files) + chr(0))
    op = OP(None, FO_DELETE, ctypes.cast(names, ctypes.c_void_p), None,
            FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT, 0, None, None)
    try:
        return sh.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
    except (AttributeError, OSError, ValueError):
        return False


def delete(paths):
    """Удаляет файлы: в корзину, если она есть, иначе насовсем.
    Возвращает (сколько удалено, попали ли в корзину)."""
    files = [p for p in (Path(x) for x in paths) if p.is_file()]
    if not files:
        return 0, False
    if _to_trash(files):
        return len([p for p in files if not p.exists()]), True
    n = 0
    for p in files:
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n, False
