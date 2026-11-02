#!/bin/bash
# Запуск без сборки .app — двойным кликом из этой папки (удобно при разработке).
# Первый запуск доставит Python, библиотеки и Chromium в ~/Library/Application Support/RealtyWatch.
cd "$(dirname "${BASH_SOURCE[0]}")"
bash bin/bootstrap.sh
exec "$HOME/Library/Application Support/RealtyWatch/runtime/venv/bin/python" -m app.main "$@"
