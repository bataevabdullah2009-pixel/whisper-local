param([Parameter(Mandatory = $true)][string[]]$Path)

$ErrorActionPreference = 'Stop'
$signingThumbprint = $env:WHISPERLOCAL_WINDOWS_CERTIFICATE_SHA1
if ($signingThumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
    throw 'A release signing session with a trusted certificate is required.'
}
$signingCertificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$signingThumbprint"
if (-not $signingCertificate.HasPrivateKey -or $signingCertificate.Subject -eq $signingCertificate.Issuer) {
    throw 'A self-signed or public-only certificate cannot sign release artifacts.'
}
$signingTimestamp = $env:WHISPERLOCAL_WINDOWS_TIMESTAMP_URL
if (-not $signingTimestamp) { $signingTimestamp = 'http://timestamp.digicert.com' }
$signingTimestampUri = $null
if (-not [Uri]::TryCreate($signingTimestamp, [UriKind]::Absolute, [ref]$signingTimestampUri) -or
    $signingTimestampUri.Scheme -notin @('http', 'https')) {
    throw 'The RFC 3161 timestamp URL must use HTTP or HTTPS.'
}
$signingTool = Get-ChildItem -LiteralPath "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
    -Filter signtool.exe -Recurse | Where-Object { $_.Directory.Name -eq 'x64' } |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signingTool) { throw 'Windows SDK SignTool was not found.' }

foreach ($signingFile in $Path) {
    $signingFilePath = (Resolve-Path -LiteralPath $signingFile).Path
    & $signingTool.FullName sign /sha1 $signingThumbprint /s My /fd SHA256 /td SHA256 `
        /tr $signingTimestamp $signingFilePath
    if ($LASTEXITCODE -ne 0) { throw 'Authenticode signing or timestamping failed.' }
    & $signingTool.FullName verify /pa /all /tw $signingFilePath
    if ($LASTEXITCODE -ne 0) { throw 'Public-trust Authenticode verification failed.' }
    $signingResult = Get-AuthenticodeSignature -LiteralPath $signingFilePath
    if ($signingResult.Status -ne 'Valid' -or
        $signingResult.SignerCertificate.Thumbprint -ne $signingThumbprint -or
        -not $signingResult.TimeStamperCertificate) {
        throw 'Release artifacts require the selected trusted signer and a timestamp.'
    }
}
