[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$MediaPath)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$media = (Resolve-Path $MediaPath).Path
$devcmd = Get-ChildItem 'C:\Program Files\Microsoft Visual Studio' -Recurse `
    -Filter 'VsDevCmd.bat' -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $devcmd) { throw 'Visual Studio VsDevCmd.bat was not found.' }
$ffmpegInclude = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\include')).Path
$ffmpegLib = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\lib')).Path
$ffmpegBin = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\bin')).Path
$buildRoot = Join-Path $env:TEMP ('audit050-playback-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildRoot | Out-Null
$src = (Resolve-Path (Join-Path $PSScriptRoot 'ffmpeg_audio_mix_probe.cpp')).Path
$exe = Join-Path $buildRoot 'ffmpeg_audio_mix_probe.exe'
$csv = Join-Path $buildRoot 'mix.csv'
$command = 'call "{0}" -arch=x64 -host_arch=x64 && cd /d "{1}" && cl /nologo /std:c++17 /EHsc /MD /W4 /O2 /I"{2}" "{3}" /Fe:"{4}" /link /LIBPATH:"{5}" avformat.lib avcodec.lib avutil.lib swresample.lib' -f $devcmd, $buildRoot, $ffmpegInclude, $src, $exe, $ffmpegLib
& cmd.exe /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "Playback native probe compilation failed ($LASTEXITCODE)." }
$env:Path = "$ffmpegBin;$env:Path"
& $exe $media $csv
if ($LASTEXITCODE -ne 0) { throw "Playback native probe failed ($LASTEXITCODE)." }
Write-Output '--- FFmpeg decode/mix ---'
Get-Content -LiteralPath $csv
$env:QT_QPA_PLATFORM = 'offscreen'
Write-Output '--- QMediaPlayer ---'
& python (Join-Path $PSScriptRoot 'qmediaplayer_probe.py') $media
if ($LASTEXITCODE -ne 0) { throw "QMediaPlayer probe failed ($LASTEXITCODE)." }
Write-Output '--- QAudioSink ---'
& python (Join-Path $PSScriptRoot 'qaudiosink_probe.py')
if ($LASTEXITCODE -ne 0) { throw "QAudioSink probe failed ($LASTEXITCODE)." }
Write-Output "AUDIT050_PLAYBACK_BUILD_ROOT=$buildRoot"
