# Установка портативной среды RealtyWatch: всё ставится внутрь папки приложения.
# Запуск: setup.bat (повторный запуск безопасен, уже установленное пропускается).
# -Quick: вызывается из start.bat при каждом запуске — мгновенно выходит, если всё на месте
# и requirements.txt не менялся с последней установки; иначе доустанавливает недостающее.
param([switch]$Quick)
$ErrorActionPreference = "Stop"
trap { Write-Host "`nНе удалось установить компоненты: $_" -ForegroundColor Red; exit 1 }
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root "runtime"
$PyDir   = Join-Path $Runtime "python"
$PyExe   = Join-Path $PyDir "python.exe"
$PyVer   = "3.12.10"
$Stamp   = Join-Path $Runtime "installed.txt"
$ReqHash = (Get-FileHash (Join-Path $Root "requirements.txt") -Algorithm SHA256).Hash
$HasChromium = [bool](Get-ChildItem (Join-Path $Root "browsers") -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue)

if ($Quick) {
    if ((Test-Path $PyExe) -and $HasChromium -and (Test-Path $Stamp) -and ((Get-Content $Stamp -Raw).Trim() -eq $ReqHash)) { exit 0 }
    Write-Host "Обновляю компоненты программы (один раз после установки или обновления)..."
}

New-Item -ItemType Directory -Force $Runtime, (Join-Path $Root "data"), (Join-Path $Root "profiles"), (Join-Path $Root "logs") | Out-Null

if (-not (Test-Path $PyExe)) {
    Write-Host "[1/4] Скачиваю Python $PyVer (embeddable)..."
    $zip = Join-Path $Runtime "python.zip"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $PyDir -Force
    Remove-Item $zip
    # Включаем site-packages и путь к коду приложения
    $pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
    @("python312.zip", ".", "..\..", "import site") | Set-Content $pth.FullName -Encoding ascii
} else { Write-Host "[1/4] Python уже установлен" }

if (-not (Test-Path (Join-Path $PyDir "Scripts\pip.exe"))) {
    Write-Host "[2/4] Устанавливаю pip..."
    $gp = Join-Path $Runtime "get-pip.py"
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $gp
    & $PyExe $gp --no-warn-script-location
    Remove-Item $gp
} else { Write-Host "[2/4] pip уже установлен" }

Write-Host "[3/4] Устанавливаю библиотеки..."
# setuptools нужен для пакетов, которые ставятся только из исходников (proxy_tools для pywebview)
& $PyExe -m pip install --no-warn-script-location --disable-pip-version-check -q setuptools wheel
& $PyExe -m pip install --no-warn-script-location --disable-pip-version-check -q -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "[4/4] Устанавливаю Chromium в browsers\ ..."
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root "browsers"
& $PyExe -m patchright install chromium
if ($LASTEXITCODE -ne 0) { throw "chromium install failed" }

Set-Content $Stamp $ReqHash -Encoding ascii
Write-Host "Готово. Запуск: start.bat"
