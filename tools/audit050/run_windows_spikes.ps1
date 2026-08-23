[CmdletBinding()]
param(
    [int]$TargetPid = 0,
    [switch]$RunProcessLoopback,
    [switch]$RunAacBenchmark
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$devcmd = Get-ChildItem 'C:\Program Files\Microsoft Visual Studio' -Recurse `
    -Filter 'VsDevCmd.bat' -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $devcmd) { throw 'Visual Studio VsDevCmd.bat was not found.' }

function Invoke-Cl([string]$commandBody) {
    $command = 'call "{0}" -arch=x64 -host_arch=x64 && cd /d "{1}" && {2}' -f `
        $devcmd, $buildRoot, $commandBody
    & cmd.exe /d /s /c $command
    if ($LASTEXITCODE -ne 0) { throw "Native spike compilation failed ($LASTEXITCODE)." }
}

$buildRoot = Join-Path $env:TEMP ('audit050-native-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildRoot | Out-Null
$procSrc = (Resolve-Path (Join-Path $PSScriptRoot 'windows_process_loopback_probe.cpp')).Path
$procExe = Join-Path $buildRoot 'windows_process_loopback_probe.exe'
Invoke-Cl ('cl /nologo /std:c++17 /EHsc /MD /W4 /O2 /DUNICODE /D_UNICODE "{0}" /Fe:"{1}" /link Mmdevapi.lib Ole32.lib Avrt.lib' -f $procSrc, $procExe)
$sessionSrc = (Resolve-Path (Join-Path $PSScriptRoot 'windows_session_discovery_probe.cpp')).Path
$sessionExe = Join-Path $buildRoot 'windows_session_discovery_probe.exe'
Invoke-Cl ('cl /nologo /std:c++17 /EHsc /MD /W4 /O2 /DUNICODE /D_UNICODE "{0}" /Fe:"{1}" /link Mmdevapi.lib Ole32.lib' -f $sessionSrc, $sessionExe)

$aacSrc = (Resolve-Path (Join-Path $PSScriptRoot 'aac_ring_benchmark.cpp')).Path
$encoderSrc = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\src\audio_encoder.cpp')).Path
$engineInclude = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\include')).Path
$ffmpegInclude = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\include')).Path
$ffmpegLib = (Resolve-Path (Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\lib')).Path
$aacExe = Join-Path $buildRoot 'aac_ring_benchmark.exe'
Invoke-Cl ('cl /nologo /std:c++17 /EHsc /MD /W4 /O2 /DUNICODE /D_UNICODE /I"{0}" /I"{1}" "{2}" "{3}" /Fe:"{4}" /link /LIBPATH:"{5}" avcodec.lib avutil.lib Psapi.lib' -f $engineInclude, $ffmpegInclude, $aacSrc, $encoderSrc, $aacExe, $ffmpegLib)

$ffmpegBin = Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\bin'
$env:Path = "$ffmpegBin;$env:Path"

& $sessionExe
if ($LASTEXITCODE -ne 0) { throw "Session discovery probe failed ($LASTEXITCODE)." }

if ($RunProcessLoopback) {
    if ($TargetPid -le 0) { throw '-TargetPid is required with -RunProcessLoopback.' }
    $wav = Join-Path $env:TEMP ('audit050-process-loopback-' + $TargetPid + '.wav')
    & $procExe $TargetPid $wav 3
    if ($LASTEXITCODE -eq 2) {
        Write-Warning 'Process-loopback runtime is unavailable on this host; this is an expected capability result on Windows 10.'
    } elseif ($LASTEXITCODE -ne 0) {
        throw "Process-loopback probe failed ($LASTEXITCODE)."
    }
}

if ($RunAacBenchmark) {
    $csv = Join-Path $env:TEMP ('audit050-aac-ring-' + [guid]::NewGuid().ToString('N') + '.csv')
    & $aacExe $csv
    if ($LASTEXITCODE -ne 0) { throw "AAC benchmark failed ($LASTEXITCODE)." }
    Write-Output "AAC benchmark report: $csv"
}

Write-Output "AUDIT050_NATIVE_BUILD_ROOT=$buildRoot"
