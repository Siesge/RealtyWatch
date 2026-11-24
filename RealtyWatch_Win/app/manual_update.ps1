﻿$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$StagedPs1 = Join-Path $Root 'data\update_staging\run_update.ps1'
$VerFile = Join-Path $Root 'version.txt'

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RealtyWatch — помощник ручного обновления" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

if (Test-Path -LiteralPath $StagedPs1) {
    Write-Host "Найден подготовленный пакет обновления: $StagedPs1" -ForegroundColor Green
    Write-Host "Если окно RealtyWatch сейчас открыто — закройте его (в трее: правая кнопка -> Выход)." -ForegroundColor Yellow
    Write-Host "Запускаю установку обновления..." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StagedPs1
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host ""
        Write-Host "Обновление успешно установлено! Программа запускается автоматически." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Установка завершилась с кодом $code. Подробности в logs\update.log." -ForegroundColor Red
        Read-Host "Нажмите Enter для закрытия окна"
    }
    exit $code
}

$curVer = ""
if (Test-Path -LiteralPath $VerFile) {
    $curVer = (Get-Content -LiteralPath $VerFile -TotalCount 1 -Encoding UTF8).Trim()
}

if ($curVer) {
    Write-Host "Текущая установленная сборка: $curVer"
}
Write-Host "Подготовленный пакет в data\update_staging не найден." -ForegroundColor Yellow
Write-Host ""
Write-Host "Что сделать:" -ForegroundColor Cyan
Write-Host "  1. Если у вас уже установлена версия 1.8 или новее — просто откройте RealtyWatch,"
Write-Host "     перейдите в «Настройки -> Обновление программы» и нажмите «Проверить обновление»."
Write-Host "  2. Если у вас версия 1.6 или 1.7 (или <= 1.5):"
Write-Host "     - закройте RealtyWatch (через «Выход» в меню значка в трее);"
Write-Host "     - скачайте свежий архив RealtyWatch-*-windows-portable.zip со страницы:"
Write-Host "       https://github.com/Siesge/RealtyWatch/releases/latest"
Write-Host "     - распакуйте содержимое папки RealtyWatch из архива поверх этой папки с заменой файлов"
Write-Host "       (папка database\ со всей базой, заметками, лайками и дизлайками НЕ затрагивается);"
Write-Host "     - запустите RealtyWatch.exe."
Write-Host ""
Read-Host "Нажмите Enter для закрытия окна"
