# Windows 11 Qualification

Use this checklist on a physical Windows 11 system before changing per-app
audio or borderless capture from `CODE READY / HW UNVERIFIED` to `READY`.

## Required host

- Windows 11 with a same-adapter display and supported NVIDIA, AMD, or Intel GPU.
- The final FTHR installer built from the commit under test.
- Two independently controllable audio applications. Use continuous 440 Hz and
  880 Hz sources for the controlled pass, then repeat with Discord and Spotify.
- FFprobe/FFmpeg from the installed FTHR bundle for inspection.

Record the Windows build, GPU and driver, audio endpoints, FTHR commit, artifact
hashes, and whether the package has identity, capture capability, and user
consent for the official Windows borderless-capture API.

## Test sequence

1. Fresh-install FTHR, launch it, and confirm capture health becomes active.
2. Start Source A at 440 Hz and Source B at 880 Hz in separate processes.
3. Save a 30-second clip and inspect its `.fthr-audio.json` sidecar.
4. Confirm Default Mix, Source A, and Source B are separate named streams with
   stable source IDs and valid timestamps.
5. Decode each track separately. Confirm A is dominated by 440 Hz, B by 880 Hz,
   and neither app stem contains material energy from the other tone.
6. In the FTHR viewer, mute and change gain for every row, then verify Master
   gain affects the combined output without changing the source controls.
7. Repeatedly seek near the beginning, middle, and end. Listen for crackle,
   stale audio, jumps, and drift; verify 30- and 60-second clips remain in sync.
8. Repeat with Discord and Spotify. Confirm names and icons are correct and the
   two applications remain isolated while both are audible in Default Mix.
9. Restart one source process. Confirm recovery creates a current source rather
   than reusing stale process identity, and save another valid clip.
10. Repeat capture, save, seek, gain, and mute under representative gaming load;
    record CPU, RAM, capture cadence, dropped-frame evidence, and save latency.
11. Repeat H.264, HEVC, and AV1 for each physically present hardware family.
12. If all official Windows capability prerequisites are present, enable the
    existing borderless policy and verify the capture border result. Otherwise
    record the exact missing prerequisite and leave the feature unverified.

## Pass boundary

Do not infer a pass from automated tests or a Windows 10 run. Attach the clip,
sidecar manifest, per-track spectral results, logs, system inventory, timing
measurements, and artifact hashes to the qualification record. Any missing app
stem, cross-contamination, unstable identity, playback crackle, A/V drift,
capture regression, or package-capability failure keeps the affected status
unverified or failed.
