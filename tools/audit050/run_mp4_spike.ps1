[CmdletBinding()]
param([int]$DurationSeconds = 5)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ff = Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\bin\ffmpeg.exe'
$fp = Join-Path $repo 'FTHRcapture\FTHRclips\third_party\ffmpeg\bin\ffprobe.exe'
if (-not (Test-Path -LiteralPath $ff)) { throw "ffmpeg not found: $ff" }
if (-not (Test-Path -LiteralPath $fp)) { throw "ffprobe not found: $fp" }

$root = Join-Path $env:TEMP ('audit050-mp4-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$rows = @()

foreach ($count in 1, 4, 8) {
    $ffArgs = @('-hide_banner', '-loglevel', 'error', '-y')
    $ffArgs += @('-f', 'lavfi', '-i', "color=c=black:s=640x360:r=30:d=$DurationSeconds")
    for ($i = 0; $i -lt $count; $i++) {
        $frequency = 440 + ($i * 37)
        $ffArgs += @('-f', 'lavfi', '-i', "sine=frequency=${frequency}:sample_rate=48000:duration=${DurationSeconds}")
    }
    $mixInputs = (1..$count | ForEach-Object { "[$($_):a]" }) -join ''
    $filter = "${mixInputs}amix=inputs=${count}:duration=longest:normalize=0[mix]"
    $ffArgs += @('-filter_complex', $filter, '-map', '0:v', '-map', '[mix]')
    for ($i = 1; $i -le $count; $i++) { $ffArgs += @('-map', "${i}:a") }
    $ffArgs += @('-c:v', 'mpeg4', '-q:v', '5', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-ac', '2', '-shortest',
        '-metadata:s:a:0', 'handler_name=Default Mix',
        '-disposition:a:0', 'default')
    for ($i = 1; $i -le $count; $i++) {
        $streamMeta = '-metadata:s:a:{0}' -f $i
        $streamDisp = '-disposition:a:{0}' -f $i
        $ffArgs += $streamMeta
        $ffArgs += ('handler_name=Source {0}' -f $i)
        $ffArgs += $streamDisp
        $ffArgs += '0'
    }
    $source = Join-Path $root ("default-plus-$count-stems.mp4")
    $ffArgs += $source
    & $ff @ffArgs
    if ($LASTEXITCODE -ne 0) { throw "FFmpeg failed for $count stems." }

    $probe = & $fp -v error -show_entries `
        'stream=index,codec_type,codec_name,disposition:stream_tags=handler_name' `
        -of json $source | ConvertFrom-Json
    $copy = Join-Path $root ("copy-no-map-$count-stems.mp4")
    & $ff -hide_banner -loglevel error -y -ss 0 -i $source -t 2 -c copy $copy
    if ($LASTEXITCODE -ne 0) { throw "FFmpeg copy export failed for $count stems." }
    $copyProbe = & $fp -v error -select_streams a -show_entries `
        'stream=index,codec_name:stream_tags=handler_name' -of json $copy |
        ConvertFrom-Json
    $mapped = Join-Path $root ("copy-map-all-$count-stems.mp4")
    & $ff -hide_banner -loglevel error -y -ss 0 -i $source -t 2 -map 0 -c copy $mapped
    if ($LASTEXITCODE -ne 0) { throw "FFmpeg map-all export failed for $count stems." }
    $mappedProbe = & $fp -v error -select_streams a -show_entries `
        'stream=index,codec_name:stream_tags=handler_name' -of json $mapped |
        ConvertFrom-Json

    $rows += [pscustomobject]@{
        StemCount = $count
        Source = $source
        SourceAudioStreams = @($probe.streams | Where-Object codec_type -eq 'audio').Count
        NoMapAudioStreams = @($copyProbe.streams).Count
        MapAllAudioStreams = @($mappedProbe.streams).Count
        SourceBytes = (Get-Item -LiteralPath $source).Length
        NoMapBytes = (Get-Item -LiteralPath $copy).Length
        MapAllBytes = (Get-Item -LiteralPath $mapped).Length
    }
}

$report = Join-Path $root 'report.json'
$rows | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $report -Encoding UTF8
Write-Output "AUDIT050_MP4_ROOT=$root"
$rows | Format-Table -AutoSize
Write-Output "AUDIT050_MP4_REPORT=$report"
