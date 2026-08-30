# Release Checklist — 1.0.0-alpha

This checklist records release gates, not aspirations. A public build must not
be tagged or uploaded while any required item is `FAIL`, `BLOCKED`, or
`NOT RUN`. Last reviewed: 2026-08-30.

## Current decision

**PUBLIC ALPHA: BLOCKED**

Current blockers:

1. The asset/provenance gate fails for shipped assets that are missing or
   inconsistent in `tools/release_asset_manifest.json`.
2. NVIDIA H.264 and AV1 replay capture stalled during physical qualification.
3. HEVC editor playback failed and the latest microphone track was silent.
4. AMD, Intel, Windows 11 per-app audio, and representative Linux desktops are
   not physically qualified.
5. No approved Windows code-signing identity is configured.

## Source gates

Run from a clean checkout:

```powershell
python -m pytest tests/
python -m ruff check .
python -m compileall -q FTHR_UI tests tools
python tools/verify_release_licenses.py --tree .
python tools/verify_version_consistency.py
python tools/verify_shared_memory_contract.py
python tools/verify_engine_response_contract.py
python tools/verify_exception_handling.py
python tools/scan_repo_hygiene.py
```

Required result: every command exits successfully and neither the tests nor the
gates create an untracked file. A test count is evidence for one run only and is
not hard-coded here.

## Windows build and package gates

- Build `FTHRcapture/FTHRcapture.sln` in Release/x64 from a clean native output.
- Run the native test executable and record its real check count.
- Build optional dormant packages, then run `pyinstaller FTHR.spec --clean`.
- Verify the onedir bundle contains the engine, playback mixer, FFmpeg tools,
  licence texts, approved assets, and only the reviewed Qt runtime.
- The bundle must contain no `icuuc.dll`, `icudt*.dll`, or `icuin*.dll` copied
  from another program on `PATH`.
- Build the installer and run the installer lifecycle verifier.
- Launch the exact packaged app, start capture, save and fully decode a clip,
  open it in the editor, verify tray restore, then shut down without orphans.
- Test the exact installer output on a clean or disposable profile. Verify
  install, update, uninstall, shortcuts, autostart ownership, and preservation
  of user clips/settings.
- Sign public artifacts and verify their Authenticode status.

## Physical capture qualification

For every advertised vendor/codec combination:

- Wait for healthy capture and a growing frame counter.
- Save 30- and 60-second clips plus ten rapid 5-second clips.
- Require fresh frames after the rapid saves; old ring contents do not prove a
  live capture backend.
- Fully decode every file and verify duration, resolution, codec, timestamps,
  audio streams, unique names, and absence of partial files.
- Listen to system and microphone tracks separately.
- Exercise editor play/pause/seek on every advertised codec.
- Exercise primary and secondary monitors, selected-monitor screenshots,
  background/tray operation, hotkeys, restart recovery, and graceful shutdown.

Current physical boundary: Windows 10 + RTX 4060 Ti. HEVC replay passed; H.264
and AV1 stalled. AMD, Intel and Windows 11-specific features are `NOT RUN`.

## Linux gates

- Build against the pinned LGPL FFmpeg inputs and run every CTest target.
- Build and verify the AppImage from a clean Linux environment.
- On representative native Wayland and X11 desktops, record visible pixels,
  save and fully decode clips, verify audio content, hotkeys, multi-monitor,
  restart recovery, shutdown bounds, and absence of stale IPC resources.
- Do not count WSLg bounded-failure evidence as visible desktop capture.

## Licence and privacy gates

- Every delivered image, icon, font, sound, binary and optional package must be
  present in the machine-readable allowlist with an exact hash, origin,
  licence, notice and approved redistribution status.
- Git history alone is not redistribution evidence.
- No credentials, tokens, user media, logs, machine identifiers, absolute user
  paths, caches, debug symbols, or test outputs may enter an artifact.
- Optional upload packages remain dormant until explicit user consent.

## Tagging

Only after all applicable gates above pass on the exact artifact:

```powershell
git status --porcelain
git tag -a v1.0.0-alpha -m "FTHR Clips 1.0.0-alpha — verified release"
```

Attach hashes and a qualification record naming the hardware, operating system,
commands run, and every item that was not run.
