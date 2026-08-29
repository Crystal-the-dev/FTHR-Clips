# Encoder and settings update

This change makes video encoding an explicit, device-aware choice and removes settings that did not represent real app behavior.

## Encoder discovery and selection

The Clip tab now contains Encoder, Codec, and Active Encoder controls. The first
discovery runs outside the UI thread and performs a real one-frame FFmpeg encode
for every candidate; an encoder being compiled into FFmpeg is not enough. The
validated device profile is cached against the platform and exact FFmpeg binary,
so later starts load immediately. A **Redetect** action is available for driver
or hardware changes.

The available backend keys are:

- `auto` — use the normal engine policy.
- `nvenc` — NVIDIA NVENC.
- `amf` — AMD AMF.
- `qsv` — Intel Quick Sync.
- `software` — Linux-only reviewed software fallback.

The codec list is rebuilt for the selected backend and can contain H.264, HEVC, and AV1 only when that exact combination passed discovery. NVIDIA NVENC additionally exposes presets P1 through P7. The preset control is hidden for every other backend.

On the development laptop used for verification, discovery returned NVIDIA NVENC with H.264/HEVC and Intel Quick Sync with H.264/HEVC. AMD and AV1 were correctly omitted.

## Windows engine behavior

The UI passes the encoder preference as argument 17. Automatic selection preserves the same-adapter policy: capture on Intel uses QSV, capture on NVIDIA uses NVENC, and so on.

An explicit NVIDIA choice is different. If capture occurs on an Intel-driven display, the engine creates a separate D3D11 device on NVIDIA and uses the existing NVENC CPU-input path:

1. The capture frame is copied to a readable staging texture on the capture adapter.
2. The mapped BGRA frame is submitted to NVENC on the NVIDIA device.
3. Encoding remains hardware accelerated; only the cross-adapter pixel transfer touches system memory.

Explicit cross-adapter AMD and Intel selections remain rejected because those paths do not have an equivalent qualified bridge. Windows software replay remains unavailable.

NVENC now maps the selected P1-P7 value to the corresponding native `NV_ENC_PRESET_P*_GUID` instead of always using P2.

## Linux parity

Linux consumes the same argument 17 and filters its FFmpeg candidate list by the requested backend. Automatic selection is backend-first so a working hardware H.264 encoder wins over a slower software AV1 fallback. The Linux software candidates remain OpenH264, Kvazaar, SVT-AV1, libaom AV1, and rav1e where available.

The common UI discovery, backend/codec filtering, NVIDIA preset UI, audio-off control, and multiband control are all available on Linux. Linux continues to use its existing PulseAudio category-WAV post-mix for multiband clips.

## Audio controls

Performance now exposes:

- Enable audio capture.
- The NVIDIA preset selector when NVIDIA hardware encoding is available.

The Audio tab exposes the same audio setting. Changing either copy synchronizes
the other immediately. The NVIDIA preset selector is also available from both
Clip and Performance and uses the same saved encoder preset.

Argument 14 disables all native audio capture. Argument 13 enables multiband only when audio is enabled. On Windows, disabling multiband skips the per-application WASAPI session manager while retaining the normal desktop mix and microphone. On Linux, it selects the normal desktop-audio path instead of category capture.

## Apply state and settings cleanup

The capture and source popups now show a small `Capturing` state beside the action. Any popup change reveals an `Apply` button. While the capture engine applies the requested generation, the state reads `Applying Settings`; after healthy frames arrive it returns to `Capturing` and hides the action.

The Version & Updates tab and its dead manual-update text were removed. Encoder
controls remain in Clip, with the NVIDIA preset duplicated in Performance for
quick access.

## Direct launcher shortcut

The existing FTHR desktop shortcuts were updated to launch `FTHR_UI/main.py` directly with `pythonw.exe`. [`tools/update_dev_shortcut.ps1`](../tools/update_dev_shortcut.ps1) recreates that setup and updates every existing FTHR shortcut on the desktop. Source-mode engine discovery now prefers the supported solution output at `FTHRcapture/x64/Release/FTHRclips.exe`, avoiding older project-local binaries.

Packaged Windows installer shortcuts already target the packaged application executable directly and were not changed.

## Native argument additions

The relevant tail of the shared Windows/Linux launch contract is now:

| Argument | Meaning |
| --- | --- |
| 11 | Codec: 0 Auto, 1 H.264, 2 HEVC, 3 AV1 |
| 12 | Encoder preset P1-P7 |
| 13 | Multiband audio enabled |
| 14 | All audio enabled |
| 15 | Windows microphone endpoint ID |
| 16 | Windows microphone gain, 0-200% |
| 17 | Encoder: 0 Auto, 1 NVIDIA, 2 AMD, 3 Intel, 4 Software |

## Verification

- Python capability, settings, popup, and PySide smoke tests pass.
- Windows Release/x64 solution build succeeds.
- The native test executable passes 382 checks, including explicit Intel-capture to NVIDIA-NVENC policy coverage.
- Offscreen visual renders were checked at 1400×900 for the Clip and Performance tabs.
- A second live native startup was not performed because an older engine instance was already capturing through the fixed shared-memory name. That user process was left untouched.
