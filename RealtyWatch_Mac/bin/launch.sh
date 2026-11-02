#!/bin/bash
# Это Contents/MacOS/RealtyWatch внутри .app: с него начинается запуск программы.
# Первый запуск — доставка среды в отдельном окне Терминала (видно, что происходит),
# дальше сразу окно программы. Работает и из папки с исходниками (./bin/launch.sh).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$HERE/../Resources/app" ]; then           # внутри бандла
  CODE="$(cd "$HERE/../Resources" && pwd)"
  APP="$(cd "$HERE/../.." && pwd)"
else                                                # запуск из папки с исходниками
  CODE="$(cd "$HERE/.." && pwd)"
  APP=""
fi
export RW_CODE="$CODE"
HOME_DIR="${RW_HOME:-$HOME/Library/Application Support/RealtyWatch}"
PY="$HOME_DIR/runtime/venv/bin/python"
LOG="$HOME_DIR/logs/console.log"
mkdir -p "$HOME_DIR/logs"

if ! bash "$CODE/bin/bootstrap.sh" --check; then
  # Среды ещё нет: показываем установку в Терминале, программа откроется сама по окончании
  CMD="RW_APP=$(printf %q "$APP") RW_CODE=$(printf %q "$CODE") bash $(printf %q "$CODE/bin/bootstrap.sh")"
  osascript -e "tell application \"Terminal\" to do script \"$CMD\"" \
            -e 'tell application "Terminal" to activate' >/dev/null
  exit 0
fi

cd "$CODE"
exec "$PY" -m app.main "$@" >> "$LOG" 2>&1
