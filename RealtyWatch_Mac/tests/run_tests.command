#!/bin/bash
# Тесты RealtyWatch: снятие объявлений, страницы сайтов, расписание, журнал, капча, очистка, система.
# Всё идёт во временной папке на синтетических данных; настоящая база и журналы не затрагиваются,
# к настоящим сайтам тесты не обращаются.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
bash bin/bootstrap.sh
exec "${RW_HOME:-$HOME/Library/Application Support/RealtyWatch}/runtime/venv/bin/python" -m unittest discover -s tests -t . -v
