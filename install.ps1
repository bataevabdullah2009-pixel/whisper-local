# Developer/source installer. End users can use the standalone Setup.exe.
param([switch]$CpuOnly)
$ErrorActionPreference = 'Stop'
$appSource = $PSScriptRoot
$appInstall = Join-Path $env:LOCALAPPDATA 'Programs\WhisperLocalOpen-source'
$appData = Join-Path $env:LOCALAPPDATA 'WhisperLocalOpen'
New-Item -ItemType Directory -Force -Path $appInstall,$appData | Out-Null
$appPython = Join-Path $appInstall '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $appPython)) {
    py -3.12 -m venv (Join-Path $appInstall '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Для установки из исходников нужен Python 3.12. Готовая сборка Setup.exe включает Python.' }
}
& $appPython -m pip install --disable-pip-version-check -r (Join-Path $appSource 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости приложения.' }
if (-not $CpuOnly -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    & $appPython -m pip install --disable-pip-version-check -r (Join-Path $appSource 'requirements-gpu.txt')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Ускорение NVIDIA не установлено. Приложение сможет работать на процессоре.' }
}
if ($appSource -ne $appInstall) {
    Get-ChildItem -LiteralPath $appSource -File | Where-Object { $_.Extension -in @('.py','.json','.txt','.md') } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $appInstall -Force
    }
    Copy-Item -LiteralPath (Join-Path $appSource 'assets') -Destination $appInstall -Recurse -Force
}
Push-Location $appInstall
try {
    & $appPython -c "from PySide6.QtWidgets import QApplication; from ui import app_icon; a=QApplication([]); app_icon(128).pixmap(128,128).save('WhisperLocal.ico','ICO')"
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать значок.' }
} finally { Pop-Location }
$appPythonw = Join-Path $appInstall '.venv\Scripts\pythonw.exe'
$appMain = Join-Path $appInstall 'app.py'
$appArguments = '"' + $appMain + '" --data-dir "' + $appData + '"'
$appShell = New-Object -ComObject WScript.Shell
foreach ($appShortcutRoot in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $appLink = $appShell.CreateShortcut((Join-Path $appShortcutRoot 'Whisper Local Preview.lnk'))
    $appLink.TargetPath = $appPythonw
    $appLink.Arguments = $appArguments
    $appLink.WorkingDirectory = $appInstall
    $appLink.IconLocation = (Join-Path $appInstall 'WhisperLocal.ico') + ',0'
    $appLink.Save()
}
Start-Process -FilePath $appPythonw -ArgumentList $appArguments -WorkingDirectory $appInstall -WindowStyle Hidden
Write-Output ('Установлено: ' + $appInstall)
Write-Output 'При первом запуске выберите модель. Автозапуск можно включить в настройках.'
