param([Parameter(Mandatory = $true)][ValidateSet('prepare', 'cleanup')][string]$Action)

$ErrorActionPreference = 'Stop'
# This helper is intentionally limited to temporary GitHub-hosted build runners.
if (-not $env:RUNNER_TEMP -or -not $env:GITHUB_ENV -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Signing sessions require a temporary GitHub-hosted runner.'
}
$signingStatePath = Join-Path $env:RUNNER_TEMP 'whisperlocal-signing-thumbprint.txt'
if ($Action -eq 'cleanup') {
    if (Test-Path -LiteralPath $signingStatePath) {
        $signingThumbprint = (Get-Content -LiteralPath $signingStatePath -Raw).Trim()
        if ($signingThumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Invalid signing cleanup state.' }
        $signingCertificatePath = "Cert:\CurrentUser\My\$signingThumbprint"
        if (Test-Path -LiteralPath $signingCertificatePath) {
            Remove-Item -LiteralPath $signingCertificatePath -DeleteKey
        }
        Remove-Item -LiteralPath $signingStatePath
    }
    exit 0
}
if (Test-Path -LiteralPath $signingStatePath) { throw 'A signing session already exists.' }
foreach ($signingSecretName in @('WINDOWS_CERTIFICATE_PFX_BASE64', 'WINDOWS_CERTIFICATE_PASSWORD')) {
    if (-not [Environment]::GetEnvironmentVariable($signingSecretName)) {
        throw "Missing release credential: $signingSecretName"
    }
}
$signingPfxPath = Join-Path $env:RUNNER_TEMP ("whisperlocal-signing-" + [Guid]::NewGuid() + '.pfx')
try {
    [IO.File]::WriteAllBytes($signingPfxPath, [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE_PFX_BASE64))
    $signingPassword = ConvertTo-SecureString $env:WINDOWS_CERTIFICATE_PASSWORD -AsPlainText -Force
    $signingPfx = Get-PfxData -FilePath $signingPfxPath -Password $signingPassword
    if (@($signingPfx.EndEntityCertificates).Count -ne 1) {
        throw 'PFX must contain only one end-entity identity; unrelated private keys are forbidden.'
    }
    $signingCandidates = @($signingPfx.EndEntityCertificates | Where-Object {
        @($_.EnhancedKeyUsageList | Where-Object { $_.ObjectId -eq '1.3.6.1.5.5.7.3.3' }).Count -gt 0
    })
    if ($signingCandidates.Count -ne 1) { throw 'PFX must contain exactly one code-signing identity.' }
    $signingCertificate = $signingCandidates[0]
    if ($signingCertificate.Subject -eq $signingCertificate.Issuer) {
        throw 'Self-signed development certificates are forbidden for releases.'
    }
    $signingThumbprint = $signingCertificate.Thumbprint
    if (Test-Path -LiteralPath "Cert:\CurrentUser\My\$signingThumbprint") {
        throw 'Refusing to overwrite an existing runner signing identity.'
    }
    # Record ownership first, so an interrupted import is cleaned up by always().
    Set-Content -LiteralPath $signingStatePath -Value $signingThumbprint -NoNewline
    Import-PfxCertificate -FilePath $signingPfxPath -Password $signingPassword `
        -CertStoreLocation Cert:\CurrentUser\My | Out-Null
    $signingImportedCertificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$signingThumbprint"
    if (-not $signingImportedCertificate.HasPrivateKey) { throw 'PFX contains no usable private key.' }
    $signingChain = [Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $signingChain.ChainPolicy.RevocationMode = 'Online'
        if (-not $signingChain.Build($signingImportedCertificate)) {
            throw 'Certificate does not validate against the runner public trust store.'
        }
    } finally { $signingChain.Dispose() }
    Add-Content -LiteralPath $env:GITHUB_ENV -Value "WHISPERLOCAL_WINDOWS_CERTIFICATE_SHA1=$signingThumbprint"
} finally {
    if (Test-Path -LiteralPath $signingPfxPath) { Remove-Item -LiteralPath $signingPfxPath }
}
