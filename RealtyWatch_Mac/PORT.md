# Порт RealtyWatch на macOS: чем завернуть и как отдать пользователю

Разведка вариантов (ноябрь 2026). Код ещё не менялся: это обзор, чтобы выбрать путь.
Windows-версия лежит в `RealtyWatch_Win/` и в этом форке не трогается — только читается.

## Что вообще должно поехать

Программа — это локальный сервер (FastAPI + uvicorn) с веб-интерфейсом, база SQLite,
сборщики на `curl_cffi` и браузер `patchright` (Chromium). Всё это кросс-платформенно и
переносится как есть. Привязаны к Windows только оболочка (окно `pywebview` + значок в трее
`pystray`), звук, уведомления, блокировка файла, тема и установщик `setup.ps1`.

Важно для macOS: Windows-версия портативная — Python, Chromium, база и профили лежат внутри
папки программы. У `.app` так нельзя: бандл в `/Applications` не должен писать внутрь себя
(и это ломает подпись). На Mac данные и среду надо класть в `~/Library/Application Support/RealtyWatch/`,
а в бандле держать только код.

## Главная засада — не упаковка, а Gatekeeper

Это решается до выбора инструмента, иначе «без танцев с бубном» не выйдет ни с каким.

- Gatekeeper цепляется не к «неподписанности», а к метке карантина `com.apple.quarantine`.
  Её ставит то приложение, которое скачало файл: браузер, почта, AirDrop, мессенджер.
- **Файл, собранный или созданный на этом же Mac, метки не получает и открывается молча —
  даже неподписанный.** Это самый дешёвый путь к «просто работает».
- В macOS 15 Sequoia Apple убрала обход «правый клик → Открыть». Теперь скачанное
  неподписанное приложение открывается через «Системные настройки → Конфиденциальность и
  безопасность → Всё равно открыть», и кнопка живёт ограниченное время.
- Команда `xattr -dr com.apple.quarantine /путь/RealtyWatch.app` снимает метку и работает
  до сих пор, в том числе на macOS 26 Tahoe (по сообщениям сообщества; Apple это не документирует).
- Отдельная мина: **скачанный Chromium для patchright тоже получает карантин**, и на Tahoe это
  даёт «браузер не запустился» без внятной ошибки. Лечится тем же `xattr -dr` по пути
  `PLAYWRIGHT_BROWSERS_PATH` — это может делать сам установщик, молча, без участия человека.
- Честный вывод: по-настоящему «ноль телодвижений» для **скачанного** приложения даёт только
  Apple Developer ID ($99/год) + нотаризация. Без него — либо собирать на самом Mac, либо один
  раз выполнить одну команду / нажать «Всё равно открыть».

## Варианты обёртки

### A. Python → `.app` (заморозка кода в бандл)

| Инструмент | Что даёт | Чем хорош | Чем плох |
|---|---|---|---|
| [py2app](https://pypi.org/project/py2app/) | настоящий `.app` | только macOS, живой (0.28.10, февраль 2026), **официально рекомендован документацией pywebview** | известная заноза: внутри бандла Playwright не находит браузер ([issue #477](https://github.com/ronaldoussoren/py2app/issues/477)) |
| [PyInstaller](https://pyinstaller.org) | `.app` через `--windowed` | самый документированный, умеет x86_64 / arm64 / universal2, есть официальный рецепт для Playwright (`PLAYWRIGHT_BROWSERS_PATH=0`) | не кросс-компилятор (собирать на Mac), крупный бандл, ложные срабатывания антивирусов |
| [Briefcase (BeeWare)](https://briefcase.beeware.org/) | `.app` + `.dmg` | единственный с нормальной обвязкой подписи и нотаризации, с 0.3.24 умеет подписывать **чужие** бандлы, умеет возобновлять прерванную нотаризацию | тяжелее в настройке, заточен под приложения с нативным Python-GUI |
| [PyApp](https://github.com/ofek/pyapp) | один бинарник на Rust | сам докачивает python-build-standalone при первом запуске, можно и вшить всё внутрь | это не `.app`, нужен ещё обёртыватель (Platypus/appify) |
| [ux-py](https://pypi.org/project/ux-py/) / pyappdist | `.app` + подпись + `.dmg` | обещают ровно то, что нужно, «из коробки» | молодые, alpha — только как подсмотреть решения |

### B. Скрипт → `.app` (без заморозки, запускается наш же код)

| Инструмент | Что даёт | Заметки |
|---|---|---|
| [Platypus](https://github.com/sveinbjornt/Platypus) | `.app` вокруг любого скрипта | 5.5.0 (декабрь 2025), BSD, `brew install --cask platypus`, есть режимы «прогресс-бар», «окно вывода», **«WebKit-вид»** и **«значок в строке меню»** — почти готовая оболочка |
| [appify](https://gist.github.com/leogama/cff40e27634a9f94dc3c94e3d53491fd) | тот же бандл, но это 20 строк shell | ноль зависимостей, всё под контролем: `Contents/MacOS/запускатор` + `Info.plist` + иконка |
| Automator → «Запустить shell-скрипт» → сохранить как «Программа» | `.app` | встроено в macOS, третьих сторон не нужно; подводный камень — `PATH`, пути к бинарникам писать полностью |

### C. Обернуть веб-интерфейс в нативное окно

| Инструмент | Что даёт | Заметки |
|---|---|---|
| **pywebview** (уже в проекте) | окно WebKit | на macOS идёт через pyobjc; замораживать — py2app. Главный риск: Cocoa требует главный поток, и `pystray` на macOS **тоже** требует главный цикл — значок в трее и окно одновременно сделать трудно ([pystray#16](https://github.com/moses-palmer/pystray/issues/16), [#94](https://github.com/moses-palmer/pystray/issues/94), [pywebview#720](https://github.com/r0x0r/pywebview/issues/720)) |
| [Tauri v2 + sidecar](https://github.com/dieharders/example-tauri-v2-python-server-sidecar) | `.app` на Rust + системный WKWebView | рабочий пример ровно нашего случая: FastAPI-бэкенд, собранный PyInstaller, лежит в бандле как `externalBin` с суффиксом `aarch64-apple-darwin`; бинарник внутри подписанного `.app` не ловит Gatekeeper |
| [Pake](https://github.com/tw93/Pake) | `.app`/`.dmg` из веб-страницы | CLI поверх Tauri, бандл единицы-десятки МБ; по умолчанию открывает живой URL — то есть может просто показывать `http://127.0.0.1:8765`, но сервер должен уже работать |
| Electron + PyInstaller | `.app` | проверено временем, но 300+ МБ; есть проект, который так же носит внутри браузеры Playwright |

### D. Упаковка и доставка

- [create-dmg](https://github.com/create-dmg/create-dmg) — shell, красивый `.dmg` одной командой, умеет подпись и нотаризацию.
- [dmgbuild](https://github.com/al45tair/dmgbuild) — то же на Python, не зависит от Finder.
- `codesign --deep -s -` — ad-hoc подпись: карантин не снимает, но убирает часть претензий к бандлу.
- Developer ID + `notarytool` — единственный путь к настоящему «скачал и открыл».

## Что предлагаю

Два слоя, независимые друг от друга.

**Оболочка.** Начать с B+C: `.app`, собранный appify-скриптом (или Platypus, если захочется
GUI-настройки), внутри которого `Contents/MacOS/RealtyWatch` — наш shell-запускатор. Он при
первом запуске доставляет среду (python-build-standalone + `pip install -r requirements.txt` +
Chromium для patchright) в `~/Library/Application Support/RealtyWatch/`, снимает с Chromium
карантин и запускает `python -m app.main`. Снаружи это обычная иконка в `/Applications`,
внутри — наш же код, который можно править без пересборки. Нативное окно — на `pywebview`
(он уже в проекте), значок в строке меню на первом этапе не делать: это главный известный
конфликт на macOS. Если позже захочется более нативной оболочки — `.app` меняется на Tauri
или Pake, не трогая Python-часть.

**Доставка.** Если приложение собирается на том же Mac, где работает, — Gatekeeper молчит,
и «танцев» нет вообще. Для передачи на чужой Mac: либо $99/год и нотаризация, либо один раз
команда `xattr -dr com.apple.quarantine`. Промежуточного не существует.

## Что нужно узнать у владельца

1. Mac на Apple Silicon или Intel и какая версия macOS — от этого зависят сборки Python и Chromium.
2. Приложение будет жить только на его Mac или раздаваться другим? От этого зависит, нужен ли Developer ID.
3. Где держать базу: `~/Library/Application Support/RealtyWatch/` (по-маковски) или рядом с
   приложением, как на Windows (портативно, но с `.app` это костыль).

## Источники

[py2app](https://pypi.org/project/py2app/) ·
[PyInstaller](https://pyinstaller.org) ·
[Briefcase: релизы](https://briefcase.beeware.org/en/v0.3.24/about/releases.html) ·
[PyApp](https://github.com/ofek/pyapp/blob/master/docs/index.md) ·
[Platypus](https://github.com/sveinbjornt/Platypus/) ·
[appify](https://gist.github.com/leogama/cff40e27634a9f94dc3c94e3d53491fd) ·
[Tauri + Python sidecar](https://github.com/dieharders/example-tauri-v2-python-server-sidecar) ·
[Pake](https://yobi.tw93.fun/projects/pake) ·
[create-dmg](https://github.com/create-dmg/create-dmg) ·
[dmgbuild](https://github.com/al45tair/dmgbuild) ·
[pywebview: freezing](https://pywebview.flowrl.com/guide/freezing.html) ·
[Playwright: библиотека и упаковка](https://playwright.dev/python/docs/library) ·
[py2app + Playwright, issue](https://github.com/ronaldoussoren/py2app/issues/477) ·
[pystray и главный поток](https://github.com/moses-palmer/pystray/issues/16) ·
[Sequoia убрала обход Gatekeeper (Apple)](https://developer.apple.com/news/?id=saqachfa) ·
[то же, разбор](https://mjtsai.com/blog/2024/07/05/sequoia-removes-gatekeeper-contextual-menu-override/) ·
[карантин и флаг карантина](https://eclecticlight.co/2020/10/29/quarantine-and-the-quarantine-flag/) ·
[снятие карантина с браузеров Playwright](https://git.supported.systems/warehack.ing/playwright-mcp/commit/4bb1b261371e1da1471f3c12eb47410c0ed740c5) ·
[python-build-standalone](https://docs.astral.sh/python-build-standalone/running/)
