#!/bin/bash
# Собирает RealtyWatch.dmg — один файл, который можно просто переслать. Получатель открывает его
# двойным кликом и перетаскивает приложение в «Программы»; больше в образе ничего нет.
# Запуск на Mac из этой папки:   ./build_dmg.sh   [куда положить, по умолчанию ./dist]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/dist}"
APP="$OUT/RealtyWatch.app"
DMG="$OUT/RealtyWatch.dmg"

[ "$(uname)" = "Darwin" ] || { echo "Собирать .dmg нужно на macOS"; exit 1; }
[ -d "$APP" ] || "$HERE/build_app.sh" "$OUT"

# В образе — приложение и ярлык «Программы»: перетащить одно на другое и есть вся установка
TMP="$(mktemp -d)"
STAGE="$TMP/RealtyWatch"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Программы"

rm -f "$DMG"
hdiutil create -volname "RealtyWatch" -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG"
rm -rf "$TMP"

echo "готово: $DMG ($(du -h "$DMG" | cut -f1))"
echo "Этот файл и пересылают. На чужом Маке первый запуск: «Системные настройки» →"
echo "«Конфиденциальность и безопасность» → «Открыть всё равно» (подписи Apple у нас нет)."
