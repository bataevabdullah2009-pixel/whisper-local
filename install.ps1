$ErrorActionPreference = 'Stop'
$appSource = $PSScriptRoot
$appInstall = Join-Path $env:USERPROFILE 'Documents\WhisperLocal'
$appData = Join-Path $appInstall 'data'
New-Item -ItemType Directory -Force -Path $appInstall,$appData | Out-Null
$appPython = Join-Path $appInstall '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $appPython)) {
    python -m venv (Join-Path $appInstall '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать среду Python.' }
}
& $appPython -m pip install --disable-pip-version-check -r (Join-Path $appSource 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости оболочки.' }
foreach ($appFile in @('app.py','ui.py','audio_capture.py','windows_native.py','asr_worker.py','sound_cues.py','config.example.json','requirements.txt','README.md','PRODUCT.md','DESIGN.md')) {
    if ($appSource -ne $appInstall) {
        Copy-Item -LiteralPath (Join-Path $appSource $appFile) -Destination (Join-Path $appInstall $appFile) -Force
    }
}
if ($appSource -ne $appInstall) {
    Copy-Item -LiteralPath (Join-Path $appSource 'assets') -Destination $appInstall -Recurse -Force
}
$appConfig = Join-Path $appData 'config.json'
if (-not (Test-Path -LiteralPath $appConfig)) {
    Copy-Item -LiteralPath (Join-Path $appSource 'config.example.json') -Destination $appConfig
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
    $appLink = $appShell.CreateShortcut((Join-Path $appShortcutRoot 'Whisper Local.lnk'))
    $appLink.TargetPath = $appPythonw
    $appLink.Arguments = $appArguments
    $appLink.WorkingDirectory = $appInstall
    $appLink.IconLocation = (Join-Path $appInstall 'WhisperLocal.ico') + ',0'
    $appLink.Description = 'Локальная диктовка: удерживайте левый Alt и говорите.'
    $appLink.Save()
}
$appRunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$appSavedConfig = Get-Content -LiteralPath $appConfig -Raw | ConvertFrom-Json
if ($appSavedConfig.autostart) {
    New-ItemProperty -Path $appRunKey -Name 'WhisperLocal' -PropertyType String -Value ('"' + $appPythonw + '" ' + $appArguments + ' --background') -Force | Out-Null
}
Start-Process -FilePath $appPythonw -ArgumentList $appArguments -WorkingDirectory $appInstall -WindowStyle Hidden
Write-Output ('Установлено: ' + $appInstall)
Write-Output 'Ярлык Whisper Local создан на рабочем столе и в меню Пуск.'
