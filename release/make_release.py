"""Сборка переносного архива RealtyWatch: dist/RealtyWatch-portable-<дата>.zip.

В архив попадает только программа из последнего коммита (git archive HEAD:"Parser App"), README.md и
«Как запустить.txt». База, профили с cookies, журналы, Python и Chromium в архив не попадают: на новом
компьютере RealtyWatch.exe скачает Python, библиотеки и Chromium сам при первом запуске.

Запуск из корня репозитория:  "Parser App\\runtime\\python\\python.exe" release\\make_release.py
"""
import datetime
import io
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FORBIDDEN = ("database/", "profiles/", "logs/", "runtime/", "browsers/", "data/", "__pycache__", ".db")


def main():
    if subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "Parser App"], capture_output=True, text=True).stdout.strip():
        sys.exit("В папке Parser App есть незакоммиченные изменения — сначала закоммитьте их: архив собирается из коммита")
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    raw = subprocess.run(["git", "-C", str(REPO), "archive", "--format=zip", "--prefix=RealtyWatch/", "HEAD:Parser App"],
                         capture_output=True, check=True).stdout
    out = REPO / "dist" / f"RealtyWatch-portable-{datetime.date.today().isoformat()}.zip"
    out.parent.mkdir(exist_ok=True)
    howto = (REPO / "release" / "Как запустить.txt").read_text(encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for info in zin.infolist():
            if not info.is_dir():
                zout.writestr(info.filename, zin.read(info.filename))
        zout.write(REPO / "README.md", "RealtyWatch/README.md")
        # с BOM и переводами строк Windows — чтобы Блокнот на любом компьютере открыл текст правильно
        zout.writestr("RealtyWatch/Как запустить.txt", "﻿" + howto.replace("\r\n", "\n").replace("\n", "\r\n"))
        zout.writestr("RealtyWatch/version.txt", f"RealtyWatch, сборка {datetime.date.today().isoformat()}, коммит {commit}\r\n")
    names = zipfile.ZipFile(out).namelist()
    bad = [n for n in names if any(x in n for x in FORBIDDEN)]
    if bad:
        out.unlink()
        sys.exit(f"В архив попали лишние файлы: {bad[:5]}")
    print(f"{out} — {out.stat().st_size // 1024} КБ, {len(names)} файлов, коммит {commit}")


if __name__ == "__main__":
    main()
