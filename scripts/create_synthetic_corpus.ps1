# Developer-owned speech fixtures, generated offline by Windows SAPI. No microphone.
param([string]$OutputDirectory = 'build/int8-corpus')
$ErrorActionPreference = 'Stop'
$corpusDirectory = [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDirectory))
New-Item -ItemType Directory -Path $corpusDirectory -Force | Out-Null
$phrases = @(
    'Сегодня утром я проверил почту и составил список задач на неделю.',
    'Пожалуйста, перенеси встречу с пятницы на понедельник после обеда.',
    'Открой настройки приложения и освободи оперативную память.',
    'После короткого перерыва модель снова готова к распознаванию речи.',
    'В конце рабочего дня нужно сохранить документ и выключить компьютер.',
    'Сначала проверь микрофон, затем выбери русский язык и начни запись.',
    'На улице идёт дождь, поэтому мы останемся дома и приготовим ужин.',
    'Я не согласен с этим решением и предлагаю обсудить другой вариант.'
)
$voice = New-Object -ComObject SAPI.SpVoice
$russianVoice = @($voice.GetVoices() | Where-Object { $_.GetAttribute('Language') -match '(^|;)419(;|$)' })
if (-not $russianVoice.Count) { throw 'Install a Russian SAPI voice before generating the synthetic fixture.' }
$voice.Voice = $russianVoice[0]
$voice.Rate = 0
$manifest = @()
for ($i = 0; $i -lt $phrases.Count; $i++) {
    $audioName = "ru-$i.wav"
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Format.Type = 18 # SAFT16kHz16BitMono
    $stream.Open((Join-Path $corpusDirectory $audioName), 3, $false)
    try {
        $voice.AudioOutputStream = $stream
        $voice.Speak($phrases[$i]) | Out-Null
    } finally { $stream.Close() }
    $manifest += @{audio=$audioName; reference=$phrases[$i]; language='ru'}
}
$jfkFixture = Join-Path (Get-Location) 'build/jfk.wav'
if (Test-Path -LiteralPath $jfkFixture) {
    $fixtureHash = (Get-FileHash -LiteralPath $jfkFixture -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($fixtureHash -ne '59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e') { throw 'Public fixture checksum mismatch' }
    Copy-Item -LiteralPath $jfkFixture -Destination (Join-Path $corpusDirectory 'jfk.wav')
    $manifest += @{audio='jfk.wav'; language='en'; reference='And so my fellow Americans ask not what your country can do for you ask what you can do for your country'}
}
ConvertTo-Json -InputObject @($manifest) -Depth 3 | Set-Content -LiteralPath (Join-Path $corpusDirectory 'corpus.json') -Encoding utf8
Write-Output "Created $($manifest.Count) developer fixtures with $($voice.Voice.GetDescription()). No microphone was opened."
