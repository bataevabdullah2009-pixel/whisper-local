$ErrorActionPreference = 'Stop'
$testRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$testBuild = Join-Path $testRoot 'build'
$testInstall = Join-Path $testBuild 'installed'
$testData = Join-Path $testBuild 'installer-smoke'
$testInstaller = Join-Path $testRoot 'dist\WhisperLocal-Windows-Setup.exe'

function Wait-TestProcess($process, $label, $timeout = 60000) {
    if (-not $process.WaitForExit($timeout)) {
        Stop-Process -Id $process.Id
        throw "$label did not exit."
    }
    if ($process.ExitCode -ne 0) { throw "$label failed: $($process.ExitCode)" }
}

New-Item -ItemType Directory -Path $testBuild -Force | Out-Null
$testSetup = Start-Process -FilePath $testInstaller -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/DIR="' + $testInstall + '"')
) -PassThru -WindowStyle Hidden
Wait-TestProcess $testSetup 'Installer' 180000
try {
    # Prove the installed binaries work without finding the runner's Python.
    $testEnvironment = @{}
    foreach ($name in @('PATH', 'PYTHONHOME', 'PYTHONPATH', 'QT_QPA_PLATFORM')) {
        $testEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
    }
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        $env:QT_QPA_PLATFORM = 'offscreen'
        [Environment]::SetEnvironmentVariable('PYTHONHOME', $null)
        [Environment]::SetEnvironmentVariable('PYTHONPATH', $null)
        $testGui = Start-Process -FilePath (Join-Path $testInstall 'WhisperLocal.exe') -ArgumentList @(
            '--smoke-test', '--data-dir', ('"' + $testData + '"')
        ) -PassThru -WindowStyle Hidden
        Wait-TestProcess $testGui 'Installed GUI'
        $testProbe = Start-Process -FilePath (Join-Path $testInstall 'WhisperWorker.exe') -ArgumentList 'probe' `
            -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $testBuild 'installed-probe.json')
        Wait-TestProcess $testProbe 'Installed helper'
        $testProbeEvent = Get-Content -LiteralPath (Join-Path $testBuild 'installed-probe.json') -Raw | ConvertFrom-Json
        if ($testProbeEvent.type -ne 'hardware') { throw 'Installed helper returned no hardware event.' }
    } finally {
        foreach ($name in $testEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $testEnvironment[$name])
        }
    }
    python (Join-Path $PSScriptRoot 'smoke_asr.py') --worker (Join-Path $testInstall 'WhisperWorker.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Installed recognition helper failed.' }
} finally {
    $testUninstaller = Start-Process -FilePath (Join-Path $testInstall 'unins000.exe') `
        -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -PassThru -WindowStyle Hidden
    Wait-TestProcess $testUninstaller 'Uninstaller'
}
if (Test-Path -LiteralPath (Join-Path $testInstall 'WhisperLocal.exe')) {
    throw 'Uninstall left the installed application executable.'
}
Write-Output 'Installer, GUI/helper without host Python, offline ASR and uninstall passed.'
