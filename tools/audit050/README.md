# AUDIT-050 spike harnesses

These harnesses are isolated evidence tools. They are not imported by the
production engine, viewer, packager, or Shared Memory contract.

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\audit050\run_windows_spikes.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\audit050\run_windows_spikes.ps1 -RunProcessLoopback -TargetPid <pid>
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\audit050\run_mp4_spike.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\audit050\run_playback_spike.ps1 -MediaPath <mp4>
python .\tools\audit050\manifest_transaction_probe.py
python .\tools\audit050\activity_gate_probe.py
python .\tools\audit050\qmediaplayer_probe.py <mp4>
python .\tools\audit050\qaudiosink_probe.py
```

The Windows script discovers the installed Visual Studio developer environment
and compiles directly into `%TEMP%`; it does not use the production project.
The AAC benchmark is intentionally opt-in because the 1/4/8 × 30/60/300-second
matrix takes about two minutes on the qualification host:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\audit050\run_windows_spikes.ps1 -RunAacBenchmark
```

All output media, CSV files, executables, and temporary publication cases are
created below the user TEMP directory.
