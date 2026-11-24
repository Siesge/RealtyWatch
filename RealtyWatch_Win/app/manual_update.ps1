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
    $RootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $LockFile = Join-Path $RootPrefix 'data\app.lock'
    $warned = $false
    while ($true) {
        $procs = @(Get-Process -Name pythonw, python, RealtyWatch -ErrorAction SilentlyContinue | Where-Object {
            $_.Id -ne $PID -and $_.Path -and $_.Path.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        })
        $locked = $false
        if (Test-Path -LiteralPath $LockFile) {
            try {
                $fs = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
                $fs.Close()
            } catch [System.IO.FileNotFoundException] {
                $locked = $false
            } catch [System.IO.DirectoryNotFoundException] {
                $locked = $false
            } catch {
                $locked = $true
            }
        }
        if ($procs.Count -eq 0 -and -not $locked) {
            break
        }
        if (-not $warned) {
            Write-Host ""
            Write-Host "RealtyWatch сейчас запущен в этой папке." -ForegroundColor Yellow
            Write-Host "Закройте программу (значок в трее -> правая кнопка -> Выход) — ожидаю закрытия..." -ForegroundColor Yellow
            $warned = $true
        }
        Start-Sleep -Milliseconds 500
    }
    if ($warned) {
        Start-Sleep -Milliseconds 400
    }
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
