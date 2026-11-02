#!/bin/bash
# Доставка среды выполнения RealtyWatch в ~/Library/Application Support/RealtyWatch:
# uv, Python 3.12, библиотеки из requirements.txt и Chromium для patchright.
# Повторный запуск безопасен: уже установленное пропускается, занимает меньше секунды.
# Внутрь системы ничего не ставится, всё лежит в рабочей папке программы.
set -euo pipefail

UV_VERSION="0.13.6"
PY_VERSION="3.12"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE="${RW_CODE:-$(cd "$HERE/.." && pwd)}"          # папка с app/, web/, requirements.txt
HOME_DIR="${RW_HOME:-$HOME/Library/Application Support/RealtyWatch}"
RUNTIME="$HOME_DIR/runtime"
VENV="$RUNTIME/venv"
PY="$VENV/bin/python"
UV="$RUNTIME/uv"
BROWSERS="$HOME_DIR/browsers"
STAMP="$RUNTIME/installed.txt"

mkdir -p "$RUNTIME" "$BROWSERS" "$HOME_DIR/logs"
WANT="$(shasum -a 256 "$CODE/requirements.txt" | cut -d' ' -f1)"

ready() {
  [ -x "$PY" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$WANT" ] &&
    compgen -G "$BROWSERS/chromium-*" > /dev/null
}

if ready; then
  [ "${1:-}" = "--check" ] && exit 0
  exit 0
fi
[ "${1:-}" = "--check" ] && exit 2

echo "RealtyWatch: доставляю компоненты в $HOME_DIR"
echo "Это один раз, примерно 350 МБ. Окно можно закрыть, когда напишет «готово»."
echo

case "$(uname -m)" in
  arm64) TRIPLE="aarch64-apple-darwin" ;;
  *)     TRIPLE="x86_64-apple-darwin" ;;
esac

if [ ! -x "$UV" ]; then
  echo "[1/4] uv $UV_VERSION ($TRIPLE)"
  TMP="$(mktemp -d)"
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TRIPLE.tar.gz" -o "$TMP/uv.tar.gz"
  tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
  mv "$TMP/uv-$TRIPLE/uv" "$UV"
  chmod +x "$UV"
  rm -rf "$TMP"
  xattr -dr com.apple.quarantine "$UV" 2>/dev/null || true
fi

# Python ставим рядом с собой, а не в ~/.local — чтобы всё жило в одной папке
export UV_PYTHON_INSTALL_DIR="$RUNTIME/python"
echo "[2/4] Python $PY_VERSION"
"$UV" python install "$PY_VERSION" --quiet
[ -x "$PY" ] || "$UV" venv --python "$PY_VERSION" --quiet "$VENV"

echo "[3/4] библиотеки"
"$UV" pip install --quiet --python "$PY" -r "$CODE/requirements.txt"

echo "[4/4] браузер Chromium"
PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" "$PY" -m patchright install chromium
# Скачанный браузер попадает под карантин Gatekeeper и молча не запускается — снимаем метку
xattr -dr com.apple.quarantine "$BROWSERS" 2>/dev/null || true

echo "$WANT" > "$STAMP"
echo
echo "готово"

# Запущено из приложения — вернём его на экран
if [ -n "${RW_APP:-}" ] && [ -d "$RW_APP" ]; then
  sleep 1
  open "$RW_APP"
fi
