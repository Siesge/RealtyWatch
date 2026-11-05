#!/bin/bash
# Собирает RealtyWatch.app — обычный бандл macOS вокруг нашего же кода (способ «appify»).
# Запуск на Mac из этой папки:   ./build_app.sh   [куда положить, по умолчанию ./dist]
#
# Что получается:
#   RealtyWatch.app/Contents/MacOS/RealtyWatch  — запускатор (bin/launch.sh)
#   RealtyWatch.app/Contents/Resources/         — app/, web/, assets/, bin/, requirements.txt
#   RealtyWatch.app/Contents/Resources/icon.icns
#   RealtyWatch.app/Contents/Resources/version.txt — отсюда окно «О программе» берёт номер версии
# Среда выполнения (Python, библиотеки, Chromium) в бандл не кладётся: её приносит первый запуск
# в ~/Library/Application Support/RealtyWatch — так бандл остаётся на 2 МБ и не зависит от процессора.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/dist}"
APP="$OUT/RealtyWatch.app"
VERSION="${RW_VERSION:-$(date +%Y.%m.%d)}"   # для релиза задают номер, иначе — дата сборки
ID="ru.realtywatch.yalta"

[ "$(uname)" = "Darwin" ] || { echo "Собирать .app нужно на macOS"; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# код
for item in app web assets bin requirements.txt; do
  cp -R "$HERE/$item" "$APP/Contents/Resources/"
done
find "$APP/Contents/Resources" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "$APP/Contents/Resources/bin/"*.sh

# Номер версии — файлом рядом с кодом: его читает окно «О программе» (app/about.py).
# Первая строка в том же виде, что у Windows-сборки, — разбор у них общий.
COMMIT="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || true)"
printf 'RealtyWatch %s, сборка %s%s\n' "$VERSION" "$(date +%F)" "${COMMIT:+, коммит $COMMIT}" \
  > "$APP/Contents/Resources/version.txt"

# запускатор
cp "$HERE/bin/launch.sh" "$APP/Contents/MacOS/RealtyWatch"
chmod +x "$APP/Contents/MacOS/RealtyWatch"

# иконка: .icns собирается из assets/icon.png средствами системы
ICONSET="$(mktemp -d)/icon.iconset"
mkdir -p "$ICONSET"
SRC="$(sips -g pixelWidth "$HERE/assets/icon.png" | awk '/pixelWidth/ {print $2}')"
for size in 16 32 64 128 256 512; do
  sips -z $size $size "$HERE/assets/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  # вдвое больше исходника не рисуем: вышла бы размытая картинка, системе она не поможет
  if [ "$((size * 2))" -le "${SRC:-0}" ]; then
    sips -z $((size * 2)) $((size * 2)) "$HERE/assets/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  fi
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns"
rm -rf "$(dirname "$ICONSET")"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>RealtyWatch</string>
  <key>CFBundleDisplayName</key>       <string>RealtyWatch</string>
  <key>CFBundleExecutable</key>        <string>RealtyWatch</string>
  <key>CFBundleIdentifier</key>        <string>$ID</string>
  <key>CFBundleIconFile</key>          <string>icon</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key>           <string>$VERSION</string>
  <key>LSMinimumSystemVersion</key>    <string>11.0</string>
  <key>NSHighResolutionCapable</key>   <true/>
  <key>NSAppleEventsUsageDescription</key>
  <string>Чтобы показать окно браузера, когда сайт просит пройти проверку.</string>
  <key>NSDesktopFolderUsageDescription</key>
  <string>Чтобы сохранить на рабочий стол выгрузку базы или страницу с избранным.</string>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Чтобы сохранить в «Документы» выгрузку базы или страницу с избранным.</string>
  <key>NSDownloadsFolderUsageDescription</key>
  <string>Чтобы сохранить в «Загрузки» выгрузку базы или страницу с избранным.</string>
</dict>
</plist>
PLIST

# Ad-hoc подпись: Developer ID нет, но с подписью бандл выглядит целым и не ругается на перенос
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "codesign не отработал — это не мешает запуску"
# На этом Mac приложение собрано локально, метки карантина у него нет: откроется двойным кликом
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "готово: $APP"
echo "Перенесите его в «Программы» и запустите. Первый запуск доставит Python и Chromium:"
echo "качает около 300 МБ, на диске занимает примерно 800 МБ."
