"""Сборка переносного архива RealtyWatch: dist/RealtyWatch-portable-<дата>.zip.

В архив попадает только программа из последнего коммита (git archive HEAD:"RealtyWatch_Win"), README.md и
«Как запустить.txt». База, профили с cookies, журналы, Python и Chromium в архив не попадают: на новом
компьютере RealtyWatch.exe скачает Python, библиотеки и Chromium сам при первом запуске.

С ключом --with-db в архив кладётся и база: database/realty.db (согласованный снимок — можно собирать
и при работающей программе), database/settings.json и фотографии database/images. Такой архив
(dist/RealtyWatch-с-базой-<дата>.zip) распаковывают на новом компьютере и сразу запускают: все квартиры,
история цен и фотографии уже на месте, ничего импортировать не нужно. Профили браузера с cookies не
попадают и туда: они привязаны к устройству, на чужом компьютере от них только вред.

С ключом --version 1.0 архив получает имя релиза (dist/RealtyWatch-1.0-windows-portable.zip), и номер
версии попадает в version.txt внутри архива. Без ключа имя и version.txt остаются с датой сборки.

Текст описания релиза пишется для чистой установки на новой системе: конкретных чисел из чьей-то базы
(сколько объявлений снялось с продажи, сколько объектов осталось) в нём быть не должно — релиз скачивают
и через месяц, а на новом компьютере база пустая, карточек в релизе нет вовсе. Размеры файлов, требования
к системе, объём доставляемой среды, счёт тестов и коммит сборки — это факты о самой сборке, их можно.

Запуск из корня репозитория:
  "RealtyWatch_Win\\runtime\\python\\python.exe" release\\make_release.py [--with-db] [--version 1.0]
"""
import datetime
import io
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "RealtyWatch_Win"
FORBIDDEN = ("database/", "profiles/", "logs/", "runtime/", "browsers/", "data/", "__pycache__", ".db")
DB_PREFIX = "RealtyWatch/database/"


def add_db(zout):
    """Кладёт в архив базу: снимок realty.db, настройки и фотографии. Возвращает число файлов."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "realty.db"
        src, out = sqlite3.connect(APP / "database" / "realty.db"), sqlite3.connect(snap)
        with out:  # снимок согласован, даже если программа сейчас пишет в базу
            src.backup(out)
        out.close()
        src.close()
        zout.write(snap, DB_PREFIX + "realty.db")
    n = 1
    cfg = APP / "database" / "settings.json"
    if cfg.exists():
        zout.write(cfg, DB_PREFIX + "settings.json")
        n += 1
    images = APP / "database" / "images"
    for p in sorted(images.rglob("*")) if images.exists() else []:
        if p.is_file() and p.suffix != ".part":
            # фотографии уже сжаты — не тратим время на повторное сжатие
            zout.write(p, DB_PREFIX + "images/" + p.relative_to(images).as_posix(), compress_type=zipfile.ZIP_STORED)
            n += 1
    return n


def main():
    sys.stdout.reconfigure(errors="replace")  # консоль Windows может не знать кириллицу
    args = sys.argv[1:]
    with_db = "--with-db" in args
    if "--version" in args and args.index("--version") + 1 >= len(args):
        sys.exit("--version без номера: укажите, например, --version 1.0")
    ver = args[args.index("--version") + 1] if "--version" in args else ""
    if subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "RealtyWatch_Win"], capture_output=True, text=True).stdout.strip():
        sys.exit("В папке RealtyWatch_Win есть незакоммиченные изменения — сначала закоммитьте их: архив собирается из коммита")
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    raw = subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "--prefix=RealtyWatch/", "HEAD:RealtyWatch_Win"],
                         capture_output=True, check=True).stdout
    today = datetime.date.today().isoformat()
    kind = "с-базой" if with_db else "portable"
    out = REPO / "dist" / (f"RealtyWatch-{ver}-windows-{kind}.zip" if ver else f"RealtyWatch-{kind}-{today}.zip")
    out.parent.mkdir(exist_ok=True)
    howto = (REPO / "release" / "Как запустить.txt").read_text(encoding="utf-8")
    if with_db:
        howto = howto.replace("3. При первом запуске", "3. База уже внутри: квартиры, история цен и фотографии на месте, импортировать ничего не нужно.\n"
                                                       "   При первом запуске", 1)
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for info in zin.infolist():
            if not info.is_dir():
                zout.writestr(info.filename, zin.read(info.filename))
        zout.write(REPO / "README.md", "RealtyWatch/README.md")
        # с BOM и переводами строк Windows — чтобы Блокнот на любом компьютере открыл текст правильно
        zout.writestr("RealtyWatch/Как запустить.txt", "﻿" + howto.replace("\r\n", "\n").replace("\n", "\r\n"))
        zout.writestr("RealtyWatch/version.txt", f"RealtyWatch{' ' + ver if ver else ''}, сборка {today}, "
                                                 f"коммит {commit}{', с базой и фотографиями' if with_db else ''}\r\n")
        files = add_db(zout) if with_db else 0
    names = zipfile.ZipFile(out).namelist()
    bad = [n for n in names if any(x in n for x in FORBIDDEN) and not (with_db and n.startswith(DB_PREFIX))]
    if bad:
        out.unlink()
        sys.exit(f"В архив попали лишние файлы: {bad[:5]}")
    size = out.stat().st_size
    print(f"{out} — {size // 1024} КБ, {len(names)} файлов, коммит {commit}"
          + (f" (из них {files} файлов базы и фотографий)" if with_db else ""))


if __name__ == "__main__":
    main()
