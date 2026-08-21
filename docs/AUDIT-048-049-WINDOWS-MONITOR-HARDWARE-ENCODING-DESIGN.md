# AUDIT-048 / AUDIT-049 Windows monitor and hardware-replay decision

Date: **2026-08-16**<br>
Baseline: `a873db8588ad3d1730dd4bf52702731c7d2ca4f6`<br>
Working branch: `fix/windows-alpha-hardware-paths`

## Decision summary

| Finding | Status | Release impact |
|---|---|---|
| AUDIT-048 — Windows monitor identity and adapter/output mapping | **OPEN, P0** | A selected secondary monitor can be ignored or mapped to the wrong DXGI output. |
| AUDIT-049 — Windows vendor/codec hardware replay and hybrid-GPU policy | **OPEN, P0 / ARCHITECTURE APPROVAL REQUIRED** | Only NVIDIA H.264 has a continuously compressed replay path. AMD, Intel, HEVC, and AV1 are not integrated. |

**Stop-gate decision:** stop after this design report. Do not implement product code in
this change. Supporting AMD and Intel safely, and supporting the requested 3 vendors ×
3 codecs, requires a new codec-neutral replay-encoder boundary, codec-neutral packet
metadata, D3D11 GPU conversion for QSV, adapter-aware device ownership, truthful
capacity reporting, and physical AMD/Intel validation. It is not a bounded encoder-name
substitution.

The smallest robust target architecture is:

1. Resolve the chosen monitor from a stable display path to its current adapter LUID,
   target/source IDs, `HMONITOR`, and matching `IDXGIOutput`.
2. Capture and encode on the monitor-owning adapter whenever that adapter has the
   requested hardware codec.
3. Preserve the proven native NVENC hot path and put it behind a small codec-neutral
   replay-encoder interface.
4. Add one in-process FFmpeg D3D11 encoder backend for AMD AMF and Intel QSV. Reuse
   the already pinned FFmpeg libraries, but only after real `avcodec_open2` and encode
   probes on the selected adapter.
5. Keep frames GPU-resident. AMF may wrap a same-device D3D11 surface directly; QSV
   uses same-device GPU conversion from capture BGRA to NV12/P010 and a QSV frames
   context derived from that D3D11 device.
6. Treat a different-adapter encoder as unsupported until its transfer path is explicitly
   measured. Never hide a GPU→CPU→GPU transfer behind “hardware encoding.”
7. Default `Auto` to H.264 for interoperability. HEVC and AV1 are explicit capabilities,
   not optimistic fallbacks. A requested codec that cannot actually open must fail
   truthfully.
8. Do not advertise 30/60 seconds when only the raw 2 GiB BGRA pool is active.

This architecture does not modify the Linux capture paths and does not require a new
process or executable.

## Baseline and preserved contracts

The authoritative worktree was clean at the required baseline before this documentation
branch was created. The branch is based directly on `a873db8`. Read-only inspection
confirmed the following completed work remains present:

- AUDIT-013 PySide6 and asset-provenance/licence gates;
- AUDIT-028 transactional save (`.partial` → trailer/close → atomic commit);
- AUDIT-042 timestamp-based replay selection;
- pinned FFmpeg manifest and package hashes;
- engine response ordering/error contract;
- shared-memory v4 layout and contract tests;
- capture health/generation work from AUDIT-022/023/035.

No product code, shared-memory layout, Linux code, or assets were changed for this
decision.

## Confirmed current architecture

### Monitor/configuration path

| Transition | Current identifier/owner | Stability and correctness |
|---|---|---|
| Qt UI enumeration | `QApplication.screens()`; label and value use `QScreen.name()` | A display name is useful for presentation but the UI does not persist a Windows target path or adapter ownership. Enumeration order is not a contract with DXGI. |
| Settings | `capture_monitor` string | Persisted, but only as the Qt name. |
| Engine launch | UI passes `capture_monitor` as `argv[10]` | `main.cpp` explicitly documents it as ignored on Windows; no field enters `CaptureConfig`. |
| WGC desktop item | Engine calls `MonitorFromPoint({0,0}, MONITOR_DEFAULTTOPRIMARY)` | Always resolves the current primary monitor, independent of the user's selection. An `HMONITOR` is only valid while that monitor remains in the desktop. |
| WGC device | NVIDIA-only heuristic: any NVIDIA + any Intel means “Optimus”; otherwise often NVIDIA or the default adapter | It does not resolve the selected monitor's adapter. AMD + NVIDIA is misclassified. |
| DXGI fallback | Candidate adapter, then `adapter->EnumOutputs(0)` | Output index 0 is adapter-local, not the UI monitor index. The fallback may silently choose another output. |
| Encoder choice | Native NVENC if the NVIDIA heuristic succeeds; otherwise raw BGRA | No AMD or Intel continuous hardware encoder is opened. |
| Replay | NVENC packets in `EncodedRingBuffer`; otherwise native BGRA in a ≤2 GiB `FramePool` | Only the NVENC path can represent normal 30/60-second replay. |
| Save | Encoded H.264 snapshot is muxed; raw snapshot is converted/encoded with OpenH264 at save time | AUDIT-042 and AUDIT-028 logic is present, but the encoded muxer is hard-coded to H.264/AVCC. |

Relevant code evidence:

- `FTHR_UI/main.py`: `SourcePopup`, `_on_monitor_changed`, and `start_engine`.
- `FTHRcapture/FTHRclips/src/main.cpp:123`: `argv[10]` is documented as ignored.
- `FTHRcapture/FTHRclips/src/capture_engine.cpp:2156`: `InitializeWGC`.
- `FTHRcapture/FTHRclips/src/capture_engine.cpp:2264`: primary-monitor lookup.
- `FTHRcapture/FTHRclips/src/capture_engine.cpp:2312`: WGC `CreateForMonitor`.
- `FTHRcapture/FTHRclips/src/capture_engine.cpp:2831`: `InitializeD3D11`.
- `FTHRcapture/FTHRclips/src/capture_engine.cpp:2904`: adapter-local output 0.
- `FTHRcapture/FTHRclips/src/capture_engine.cpp:1292`: encoded save is hard-coded to `AV_CODEC_ID_H264`.

### Concrete wrong-monitor proof

Assume Windows has a primary display and a secondary display, and Qt returns the
secondary screen selected by the user. The UI stores and passes that screen name.
The engine discards the value and calls `MonitorFromPoint({0,0},
MONITOR_DEFAULTTOPRIMARY)`. WGC therefore receives the primary `HMONITOR`. The same
selection also cannot survive DXGI fallback because `EnumOutputs(0)` means “first
output on this adapter,” not “the selected Qt screen.”

Therefore **UI monitor N does not mean engine monitor N**, and the mismatch is a direct
consequence of ignored input plus unrelated enumeration domains. No unusual race is
needed to reproduce it.

### Current WGC pixel paths

1. **NVIDIA selected by heuristic:** WGC D3D11 texture → same-device
   `CopyResource` → registered NVENC texture → H.264 packet ring. No full-frame CPU
   readback.
2. **Intel + NVIDIA (“Optimus”) heuristic:** WGC/default-adapter texture → staging
   texture → blocking `Map` → CPU copy into NVIDIA NVENC system-memory input → H.264
   packet ring.
3. **No NVENC:** WGC texture → staging texture → blocking `Map` → BGRA `FramePool` →
   swscale/OpenH264 during save.

The current code comments occasionally call the raw save encoder “x264”; the shipped
implementation selects `libopenh264`, then `h264_mf`. The current `h264_mf` use does
not force hardware mode.

### Current DXGI fallback path

The DXGI path enumerates a NVIDIA adapter first, attempts `EnumOutputs(0)` on it, then
falls back to a default hardware device and again uses output 0. It does not map a
chosen monitor to an adapter LUID/output. On `DXGI_ERROR_ACCESS_LOST` it clears replay
and reinitializes, but repeats the same selection logic, so recovery can change the
captured display.

## Current GPU/encoder scenario matrix

The capacity values below assume 60 FPS, native BGRA, and the existing 2 GiB raw cap.
“Likely” describes Windows default-adapter behavior; it is not a guarantee.

| Scenario | Capture/D3D11 adapter | Encoder actually opened | Pixel path | Replay and actual capacity |
|---|---|---|---|---|
| A. NVIDIA display → NVIDIA | NVIDIA when the heuristic and output happen to match | Native NVENC H.264 | Same-GPU `CopyResource`; no CPU readback/upload | Compressed; requested duration subject to packet-slot history |
| B. AMD display → AMD | Default AMD | None continuously; OpenH264 only on save | GPU→CPU `Map`, BGRA CPU copy, later CPU BGRA→YUV420 | Raw; ~4.30 s 1080p, 2.42 s 1440p, 1.07 s 4K |
| C. Intel display → Intel | Default Intel | Same raw/save fallback | Same as B | Same raw limits |
| D. Intel display + NVIDIA dGPU | Likely Intel capture, separate NVIDIA encoder | Native NVENC H.264 in CPU-input mode | Intel GPU→CPU readback, CPU copy into NVENC input, NVIDIA upload/encode | Compressed duration, but expensive full-frame transfer every frame |
| E. AMD display + NVIDIA dGPU | The code may force a NVIDIA D3D11 device because no Intel is present | Native NVENC H.264 may open on the wrong adapter | Monitor/device ownership is not proven; WGC/DXGI can fail or capture the wrong output | Non-deterministic and not alpha-safe |
| F. Monitor on GPU A, encoder on GPU B | Only Intel+NVIDIA is deliberately special-cased | NVIDIA H.264 only in that special case | Hidden GPU→CPU→GPU transfer; other combinations are wrong/raw | Not a general supported model |
| G. No usable hardware encoder | Default capture adapter | No continuous encoder | GPU→CPU BGRA raw ring, then CPU save encode | Raw 2 GiB cap; UI can still imply the configured 30/60 seconds |

## Requested 3 × 3 codec matrix: what is actually integrated

The UI already displays `Auto`, `H.264`, `HEVC`, and `AV1`, and shared memory already
contains `cfg_codec_pref`. That is not implementation:

- launch arguments 11/12 are not used to configure the encoder;
- `RECONFIGURE_ENCODER` is a logged stub;
- native NVENC initialization always sets `NV_ENC_CODEC_H264_GUID`;
- `EncodedRingBuffer` comments and data conversion are H.264 AVCC/SPS/PPS-specific;
- `MuxEncodedClip` hard-codes `AV_CODEC_ID_H264`;
- `active_codec` is hard-coded to `h264_nvenc` or `libopenh264`.

| Vendor | H.264 | HEVC | AV1 |
|---|---|---|---|
| NVIDIA | **Integrated**: native NVENC continuous replay | **Not integrated** | **Not integrated** |
| AMD | **Not integrated** | **Not integrated** | **Not integrated** |
| Intel | **Not integrated** | **Not integrated** | **Not integrated** |

The pinned packaged FFmpeg does expose all nine encoder frontends:

`h264_nvenc`, `hevc_nvenc`, `av1_nvenc`, `h264_amf`, `hevc_amf`, `av1_amf`,
`h264_qsv`, `hevc_qsv`, and `av1_qsv`.

That proves build-time availability only. It does **not** prove a driver/runtime,
hardware generation, selected adapter, input surface, codec profile, or successful
encode session.

## Canonical Windows monitor identity

### Selected representation

Persist only the normalized `monitorDevicePath` returned in
`DISPLAYCONFIG_TARGET_DEVICE_NAME`. Resolve it against the active topology at every
capture generation into this transient record:

```text
MonitorIdentity
  persistent_key: normalized monitorDevicePath
  adapter_luid: DISPLAYCONFIG path target/source adapterId
  target_id: DISPLAYCONFIG targetInfo.id
  source_id: DISPLAYCONFIG sourceInfo.id
  source_gdi_name: DISPLAYCONFIG_SOURCE_DEVICE_NAME.viewGdiDeviceName
  hmonitor: current handle only
  desktop_rect: current physical-pixel bounds
  friendly_name / EDID ids / connector instance: diagnostics only
```

Why this is the smallest robust model:

- [`QueryDisplayConfig`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-querydisplayconfig)
  returns active source→target paths and explicitly supports obtaining source/target
  names through `DisplayConfigGetDeviceInfo`.
- [`DISPLAYCONFIG_TARGET_DEVICE_NAME`](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-displayconfig_target_device_name)
  carries the monitor device path, friendly name, EDID IDs, connector type, and
  connector instance.
- [`IDXGIFactory4::EnumAdapterByLuid`](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgifactory4-enumadapterbyluid)
  resolves the adapter by its LUID.
- [`DXGI_OUTPUT_DESC`](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_output_desc)
  exposes both the output device name and current `HMONITOR`, so the resolved monitor
  can be matched without relying on output index.
- WGC [`CreateForMonitor`](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createformonitor)
  consumes that current `HMONITOR` and is available from Windows 10 1903.

Do not persist `HMONITOR`, UI order, adapter order, or output order. Microsoft documents
that an `HMONITOR` can become invalid or change after `WM_DISPLAYCHANGE`. Likewise,
DXGI adapter enumeration must be recreated after an adapter/topology change.

The monitor path is a connection/device identity, not a universal lifetime identity for
a panel moved between docks or ports. EDID serial data may be shown to the user, but it
must not silently auto-bind a different active target.

### Required resolution algorithm

1. Loop `GetDisplayConfigBufferSizes` + `QueryDisplayConfig` with
   `QDC_ONLY_ACTIVE_PATHS | QDC_VIRTUAL_MODE_AWARE`; retry
   `ERROR_INSUFFICIENT_BUFFER`.
2. Get source and target device names for every active path.
3. Find exactly one normalized `monitorDevicePath` match. Zero matches means
   `MONITOR_NOT_FOUND`; multiple matches are an explicit ambiguity error.
4. Obtain the current `HMONITOR` from the source GDI name/monitor enumeration.
5. Recreate the DXGI factory, resolve `adapterId` by LUID, enumerate that adapter's
   outputs, and match `DXGI_OUTPUT_DESC.Monitor` and source device name.
6. Create the D3D11 capture device on that adapter and pass the same current
   `HMONITOR` to WGC.
7. Log both stable and transient identifiers for later benchmarking.

No fallback is allowed to primary monitor or output 0 after a requested identity was
provided.

### Hotplug/topology state machine

| Event | Required behavior |
|---|---|
| Selected monitor disconnected/disabled | Stop accepting frames; mark `DISPLAY_DISCONNECTED`; increment/invalidate generation; clear encoded/raw replay; retain the requested path for bounded re-resolution; never select another monitor. |
| Resolution/rotation change on same target | Pause; rebuild frame pool/conversion/encoder surfaces; clear replay; start a fresh generation after the same identity is resolved. |
| Refresh-rate change only | Re-evaluate cadence and configured FPS; restart generation only if timing resources require it. |
| Primary monitor changes | Continue the selected monitor if its device path is unchanged; primary status has no selection meaning. |
| Adapter/output disappears or device is removed | Tear down encoder before D3D11 device, clear replay, report `CAPTURE_DEVICE_LOST`, recreate factory/topology, and recover only if the exact path resolves again. |
| Encoder runtime failure | Stop publishing replay, clear generation, report `ENCODER_RUNTIME_FAILED`; a fallback is allowed only if it was explicitly selected and its capacity is reported. |

A hidden/message-only window or equivalent Windows event source should receive
`WM_DISPLAYCHANGE`; WGC item closure and D3D11 device-removed errors provide additional
signals. Shutdown and retries remain bounded. A save from a stale generation must be
rejected.

## Hardware-encoder architecture study

### Evidence from the pinned FFmpeg source

The package is FFmpeg `n8.1.2-21-gce3c09c101`; its manifest identifies the BtbN LGPL
shared build. Inspection of that exact upstream commit shows:

- [`libavcodec/amfenc.c`](https://github.com/FFmpeg/FFmpeg/blob/ce3c09c101c83add623774d414a9f9498caf5c25/libavcodec/amfenc.c#L428-L474)
  accepts `AV_PIX_FMT_D3D11`, takes `ID3D11Texture2D` from the frame, and wraps it with
  `CreateSurfaceFromDX11Native`; its non-hardware default allocates a host surface and
  copies.
- [`libavcodec/qsvenc_h264.c`](https://github.com/FFmpeg/FFmpeg/blob/ce3c09c101c83add623774d414a9f9498caf5c25/libavcodec/qsvenc_h264.c#L195-L209)
  accepts NV12 or QSV frames, not captured BGRA directly.
- [`libavutil/hwcontext_qsv.c`](https://github.com/FFmpeg/FFmpeg/blob/ce3c09c101c83add623774d414a9f9498caf5c25/libavutil/hwcontext_qsv.c#L2410-L2499)
  can derive QSV from a D3D11 device and pass `MFX_HANDLE_D3D11_DEVICE`.
- [`libavcodec/mfenc.c`](https://github.com/FFmpeg/FFmpeg/blob/ce3c09c101c83add623774d414a9f9498caf5c25/libavcodec/mfenc.c#L330-L405)
  can wrap a D3D11 texture in an MF sample and set a DXGI device manager; the same
  source requires NV12 for D3D11 H.264 input and leaves `hw_encoding` false by default.
- [`libavutil/hwcontext_d3d11va.c`](https://github.com/FFmpeg/FFmpeg/blob/ce3c09c101c83add623774d414a9f9498caf5c25/libavutil/hwcontext_d3d11va.c#L617-L689)
  can select an adapter index, but a FTHR wrapper around the already-created,
  monitor-owning D3D11 device is preferable to a second enumeration.

Local inspection of the shipped binaries also confirmed all nine encoder names. The
three AMF encoders advertise D3D11 and BGRA-family inputs; NVENC advertises D3D11 and
BGRA-family inputs; QSV H.264/AV1 require NV12/QSV and QSV HEVC has a broader list.
Runtime opening remains mandatory.

### Option comparison

| Option | Coverage and input | Copies/performance | Dependencies/licensing/package | Device loss, diagnostics, tests | Complexity/risk |
|---|---|---|---|---|---|
| A. Native AMD AMF | AMD H.264/HEVC/AV1; D3D11 surfaces and native BGRA support are available where hardware permits | Same-adapter GPU-resident path is feasible; AMF may perform internal conversion | AMF SDK is MIT; runtime comes with supported AMD driver. A new direct SDK integration would add headers/notices but normally no redistributed runtime DLL | Good vendor status codes; FTHR must own recovery/caps tests | Medium-high; solves AMD only and duplicates packet/config logic |
| B. Native Intel oneVPL/QSV | Intel H.264/HEVC/AV1 depending generation; D3D11 surface/VPP support | Same-adapter zero-system-copy is feasible; BGRA generally needs GPU VPP to NV12/P010 | oneVPL dispatcher is MIT; an implementation/driver is required. Packaging/discovery must be constrained | Strong adapter filters but legacy/runtime matrix is broad | High; solves Intel only and needs VPP/surface lifetime work |
| C. Media Foundation hardware MFT | Vendor-neutral H.264 and potentially vendor-installed HEVC/AV1 MFTs; actual availability varies | D3D11-aware MFTs can receive surfaces, but NV12 conversion is still required and an MFT may fall back to software | Windows component, no new shipped SDK runtime | Must enumerate with hardware flags and prove actual hardware; async/driver behavior varies | Medium-high; attractive common fallback, insufficiently deterministic as the sole alpha path |
| D. FFmpeg D3D11 hwcontext + vendor encoders | Existing pinned library exposes all nine names; AMF wraps D3D11, QSV derives from D3D11 | Same-adapter GPU-resident path is feasible; QSV still needs GPU conversion | No new FTHR binary/DLL in the current package. Existing FFmpeg LGPL/source-offer gates remain applicable; vendor driver runtimes remain required | Common packet/extradata API and error surface; must test device loss and driver-specific options | Medium-high, but least duplicated vendor glue |
| E. Common FTHR D3D11 encoder abstraction | Can host native NVENC plus FFmpeg AMF/QSV/MF | Adds only virtual/control overhead off the GPU hot operation; enables explicit surface-format/copy accounting | No inherent dependency | Centralizes capabilities, generation, packets, status | Required small abstraction; not itself an encoder |
| F. Three separate vendor-native implementations | Maximum control for all vendors/codecs | Best theoretical tuning | Three SDK/runtime/licence/package tracks | Three error and test matrices | Highest code and maintenance risk; rejected for alpha |

#### Required capability and surface attributes

| Option | AMD | Intel | NVIDIA impact | D3D11 texture input | BGRA | NV12/P010 | Hardware surfaces | Zero-system-copy feasibility | One-GPU copy feasibility | Cross-adapter behavior |
|---|---|---|---|---|---|---|---|---|---|---|
| A. Native AMF | H.264/HEVC/AV1 where GPU/driver supports them | No | None; native NVENC stays separate | Yes, AMF wraps DX11 surfaces | Yes for supported AMF encode paths; must probe per codec | Yes | Yes | Yes on the same AMD adapter | Yes; persistent copy/converter surfaces | No portable zero-copy guarantee; reject until measured |
| B. Native oneVPL/QSV | No | H.264/HEVC/AV1 depending Intel generation/runtime | None; native NVENC stays separate | Yes via `MFX_HANDLE_D3D11_DEVICE`/surface import | Not a common direct encode input; use VPP | Yes; canonical encode input | Yes | Yes on same Intel adapter when surface import/VPP remains GPU-resident | Yes; D3D11/VPP surfaces | No portable zero-copy guarantee; reject until measured |
| C. Media Foundation hardware MFT | Possible only when AMD driver exposes a hardware MFT | Possible only when Intel driver exposes a hardware MFT | Could replace/fallback NVENC, but would risk the proven path | Yes for a D3D11-aware MFT | H.264 encoder input is not BGRA; conversion required | NV12 baseline; other formats codec/MFT-specific | Yes if the selected MFT is D3D-aware | Feasible, but Microsoft permits software fallback, so hardware must be proved | Yes through GPU Video Processor + DXGI manager | Driver-specific; not assumed |
| D. FFmpeg D3D11 + AMF/QSV/NVENC | All three frontend names present | All three frontend names present | Can coexist with native NVENC; do not replace it initially | Yes: direct AMF/NVENC D3D11; QSV derived from D3D11 | AMF/NVENC advertise it; common QSV path should not depend on it | Required/available for QSV; available for AMF/NVENC | Yes | Feasible on same adapter and evidenced by pinned source, not yet runtime-proved | Yes; persistent pool/conversion | FFmpeg does not make cross-adapter transfer free; reject until measured |
| E. Common FTHR D3D11 abstraction | Through hosted backend | Through hosted backend | Preserves native NVENC behind the same control contract | Pass-through surface ownership, not an encoder itself | Declares actual input format | Declares conversion requirement | Enforces hardware confirmation | Enables accounting; provides no zero-copy by itself | Negligible control overhead; GPU work is backend-owned | Makes cross-adapter policy explicit and default-denied |
| F. Separate vendor-native implementations | Full AMF implementation | Full oneVPL implementation | Full NVENC implementation | Yes in each vendor backend | Vendor-specific | Vendor-specific | Yes | Best theoretical same-adapter control | Yes | Still requires a separately engineered transfer mechanism |

#### Required deployment and operational attributes

| Option | Windows 10 | Windows 11 | Runtime dependencies | Licence / redistribution | Binary-size impact | Driver requirements | Error/device-lost behavior | Testability | Implementation / maintenance | Expected performance and alpha-blocker risk |
|---|---|---|---|---|---|---|---|---|---|---|
| A. Native AMF | Supported by current AMF upstream within FTHR's 1903 floor | Supported | AMD driver AMF runtime; SDK headers at build time | SDK MIT notice; driver runtime not redistributed; codec patent review remains separate | Small engine growth; no expected runtime DLL | Supported AMD GPU and compatible Radeon driver | AMF status/device termination must be mapped into FTHR recovery | Requires AMD generation matrix and injected failures | Medium-high; separate codec configs/extradata for three codecs | Strong same-adapter potential; medium risk because it solves only AMD and is untested locally |
| B. Native oneVPL/QSV | Supported for appropriate Intel runtimes/hardware | Supported | oneVPL dispatcher plus Intel VPL GPU runtime or legacy Media SDK implementation | Dispatcher/API MIT; implementation normally driver-installed; discovery path must be constrained | Dispatcher/runtime packaging decision could add DLLs if not consumed through FFmpeg | Intel generation/runtime-specific | oneVPL statuses, async sync points, and device loss need a new recovery layer | Requires common Intel iGPU, legacy Intel, and Arc tests | High; VPP, external surfaces, dispatcher/session selection | Strong same-adapter potential; high alpha risk from generation/runtime breadth |
| C. Media Foundation hardware MFT | OS API supported; actual vendor hardware MFT not guaranteed | Same | Windows Media Foundation plus vendor driver MFT | OS component; no new redistributed SDK runtime; codec legal review remains | Minimal | Vendor must register/enable a hardware encoder MFT; OEM can affect availability | MFT async/drain/device-manager failures; software fallback must be detected/rejected | Deterministic fakes are harder; physical vendor tests mandatory | Medium-high; common API but variable implementations | Potentially good; high false-hardware and driver-variance risk if used alone |
| D. FFmpeg D3D11 + vendor encoders | Pinned build and APIs fit FTHR's 1903 floor; backend runtime still decides | Same | Existing FFmpeg DLLs plus vendor driver AMF/oneVPL/NVENC runtime | Existing LGPL package/source/notice gate; no new SDK binary in selected stage; codec legal review remains | No new DLL observed; moderate engine code growth | Backend-specific supported driver/GPU | Common `avcodec_send_frame`/receive/error layer plus backend-specific device-loss mapping | Encoder factory, packet/mux, and failures are injectable; physical matrix still mandatory | Medium-high initially; lowest duplication across six new AMD/Intel paths | Best balance; medium risk, reduced by staged enablement and preserved NVENC |
| E. Common FTHR D3D11 abstraction | Pure application code | Pure application code | None itself | Project code; no new third-party terms | Small | Backend-dependent | Central place for generation, drain, failure type, and shutdown contracts | High: interface and policy can be unit tested without GPUs | Medium initial seam, low long-term maintenance | Negligible hot-path dispatch; lowers blocker risk if introduced behavior-preservingly |
| F. Separate vendor-native implementations | API-dependent | API-dependent | NVIDIA, AMD, and Intel SDK/runtime tracks | Three notice/redistribution reviews plus codec review | Largest engine/source and possible package growth | Three driver matrices | Three distinct recovery implementations | Largest physical and fault-injection matrix | Very high implementation and permanent maintenance | Potentially optimal per vendor, but highest chance of new P0/P1 alpha blockers |

Official upstream evidence:

- AMD describes AMF as a DirectX 11-capable interoperability framework supporting
  Windows 10/11, with the SDK under MIT; the runtime is driver-provided. See the
  [AMF project](https://github.com/GPUOpen-LibrariesAndSDKs/AMF) and its
  [FFmpeg hardware guide](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/wiki/FFmpeg-and-AMF-HW-Acceleration).
- Intel documents that oneVPL needs at least one installed implementation, uses a
  dispatcher, and covers different generations through the oneVPL runtime or legacy
  Media SDK. See [libvpl](https://github.com/intel/libvpl). Its
  [shared-surface API](https://intel.github.io/libvpl/v2.10/programming_guide/VPL_prg_surface_sharing.html)
  explicitly prefers zero-copy D3D11 import while permitting a copy when zero-copy is
  unavailable.
- Microsoft documents D3D-aware MFT surface handling and also permits a D3D-aware MFT
  to fall back to software. See
  [Direct3D-aware MFTs](https://learn.microsoft.com/en-us/windows/win32/medfound/direct3d-aware-mfts)
  and [`MFTEnumEx`](https://learn.microsoft.com/en-us/windows/win32/api/mfapi/nf-mfapi-mftenumex).

### Selected implementation architecture

#### 1. Codec-neutral replay contract

Introduce a narrow `IReplayEncoder` boundary. It owns no monitor enumeration and no
files. Its conceptual contract is:

```text
EncoderRequest
  codec: H264 | HEVC | AV1
  width, height, fps, bitrate, preset, GOP policy
  capture_adapter_luid
  ID3D11Device / ID3D11DeviceContext

EncoderCapabilities
  backend: NVENC_NATIVE | FFMPEG_AMF | FFMPEG_QSV | FFMPEG_MF
  vendor, codec, profile, input_format
  capture_adapter_luid, encode_adapter_luid
  hardware_confirmed
  cpu_readbacks_per_frame, cpu_uploads_per_frame

EncodedVideoConfig
  AVCodecID, codec_tag/profile/level, time_base
  FFmpeg-produced codec extradata

EncodedPacket
  bytes, pts, dts, duration, keyframe, capture_qpc, generation
```

The ring stores encoded packets plus `EncodedVideoConfig`; it must not parse every
codec as H.264 AVCC. FFmpeg backends should keep the packet and extradata in the form
expected by the MP4 muxer. Native NVENC needs codec-specific H.264/HEVC/AV1 sequence
handling, isolated within that backend.

`MuxEncodedClip` selects `AV_CODEC_ID_H264`, `AV_CODEC_ID_HEVC`, or `AV_CODEC_ID_AV1`
from the snapshot config rather than hard-coding H.264. AUDIT-042 presentation-window
logic and AUDIT-028 transactional output remain above this boundary and unchanged in
semantics.

#### 2. Backend and codec policy

| Monitor-owning adapter | Requested codec | First candidate | Secondary candidate | Policy |
|---|---|---|---|---|
| NVIDIA | H.264/HEVC/AV1 | Native NVENC extended per codec | FFmpeg NVENC only as a diagnostic/reference path | Preserve current H.264 path; only expose HEVC/AV1 after caps/open/packet/mux/decode tests |
| AMD | H.264/HEVC/AV1 | FFmpeg AMF with the existing D3D11 device | Hardware MF only after positive probe | Same-adapter only for first alpha |
| Intel | H.264/HEVC/AV1 | FFmpeg QSV derived from the existing D3D11 device | Hardware MF only after positive probe | GPU convert BGRA→NV12/P010; same-adapter only |
| Other/no candidate | Any explicit codec | None | Optional bounded continuous software mode in a later task | Fail `ENCODER_NOT_AVAILABLE`; never claim normal replay from raw pool |

Codec `Auto` should initially choose H.264 on the capture adapter because it has the
broadest edit/play/upload compatibility. Explicit HEVC or AV1 must not silently become
H.264; report `UNSUPPORTED_CODEC_ON_ADAPTER` and keep capture stopped or ask the user
to choose Auto/H.264.

Actual codec support varies by GPU generation. For example, the presence of
`av1_nvenc`, `av1_amf`, or `av1_qsv` in FFmpeg is not evidence that the installed GPU
can encode AV1. The factory must positively create a hardware session and perform a
small encode probe before declaring support.

#### 3. GPU color conversion

- WGC/DXGI capture surfaces are BGRA.
- Native NVENC and pinned AMF advertise D3D11/BGRA inputs. Use a persistent pool and
  same-device GPU copy/wrap; verify whether the driver performs an internal GPU color
  conversion.
- QSV H.264/AV1 require NV12/QSV; HEVC should use the same predictable NV12/P010 path.
  Add a persistent, same-device D3D11 VideoProcessor conversion pool. A shader fallback
  is acceptable only if the format/output bindings are proved on target Intel hardware.
- Media Foundation, if retained as a fallback, also uses an NV12 D3D11 surface and an
  `IMFDXGIDeviceManager`.
- No steady-state `Map`, swscale, or CPU framebuffer copy is allowed in a path labelled
  hardware-ready.

[`ID3D11VideoContext::VideoProcessorBlt`](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11videocontext-videoprocessorblt)
is the first conversion implementation to prototype; input/output format support must
be queried on the real adapter.

#### 4. Same-adapter and hybrid-GPU policy

The deterministic automatic policy is:

1. Resolve the monitor-owning capture adapter.
2. Prefer a hardware encoder for the requested codec on that same adapter.
3. Do not choose a nominally faster different-adapter encoder merely because it exists.
4. A cross-adapter candidate can be enabled only by an explicit capability record with
   a measured GPU-resident transfer path and bounded synchronization.
5. Otherwise fail or use an explicitly labelled limited fallback.

D3D11 shared handles and keyed mutexes permit resources to be opened by another D3D11
device, but Microsoft's resource-sharing documentation does not guarantee a portable,
zero-copy cross-adapter implementation or its performance. Consequently, cross-adapter
sharing is **unverified**, not a basis for alpha support. The existing Intel→CPU→NVIDIA
path remains known-expensive and should be removed from automatic selection once the
same-adapter Intel path is ready.

#### 5. Replay capacity and status ABI

The existing `nvenc_active` boolean and `active_codec[64]` cannot report the required
adapter, hardware, and capacity facts. Do not repurpose unrelated fields. A later
implementation should append a versioned shared-memory v5 status block and update both
C++/ctypes contract tests. It should include at least:

- configured replay duration and effective replay capacity;
- selected codec/backend and `hardware_confirmed`;
- capture and encoder adapter LUIDs plus diagnostic names;
- capture and encode resolutions;
- steady-state transfer classification;
- encoder/capture drop counters and generation.

Until that ABI is approved, structured engine logs may aid diagnostics but are not an
acceptable UI capability contract.

### Why the alternatives were rejected

- **All encoders through FFmpeg, including NVIDIA:** rejected for the first stage because
  it would replace the only physically validated fast path without a demonstrated
  benefit.
- **Direct native AMF + direct native oneVPL:** rejected for alpha scope because it
  duplicates codec setup, packet/extradata, lifecycle, and error handling while the
  pinned FFmpeg already exposes the required D3D11 integrations.
- **Media Foundation as the only common backend:** rejected because hardware selection
  and no-software-fallback proof are less deterministic, input still needs NV12, and
  vendor MFT behavior must be validated per driver.
- **Different-adapter NVENC by default:** rejected because the existing implementation
  performs a full BGRA GPU readback and CPU copy every frame on Optimus, and AMD+NVIDIA
  ownership is currently incorrect.
- **FFmpeg CLI/subprocess:** rejected; it adds process/IPC/lifecycle complexity and
  cannot accept FTHR's in-process D3D11 textures without a new frame-transfer channel.
- **Continuous raw/software as the normal path:** rejected because it violates the
  lightweight gaming and 30/60-second product requirements unless an independently
  benchmarked, reduced mode is explicitly selected.

## Performance and memory model

### Existing raw BGRA path

| Resolution @ 60 | Bytes/frame | Full-frame bytes/s | One stream | Frames in 2 GiB | Effective history |
|---|---:|---:|---:|---:|---:|
| 1920×1080 | 8,294,400 (7.910 MiB) | 497,664,000 (474.609 MiB/s) | 3.981 Gbit/s | 258 | 4.300 s |
| 2560×1440 | 14,745,600 (14.063 MiB) | 884,736,000 (843.750 MiB/s) | 7.078 Gbit/s | 145 | 2.417 s |
| 3840×2160 | 33,177,600 (31.641 MiB) | 1,990,656,000 (1,898.438 MiB/s) | 15.925 Gbit/s | 64 | 1.067 s |

The raw path performs a GPU→CPU readback and at least one CPU-side frame copy. The
Optimus NVENC path then copies into an encoder input buffer. Two CPU memory passes alone
represent approximately 949.219, 1,687.500, and 3,796.875 MiB/s at the three
resolutions, excluding GPU transfer, synchronization stalls, cache traffic, and encode.

### Target same-adapter hardware path

At the current nominal 16 Mbit/s video bitrate:

- compressed data rate is about 1.907 MiB/s;
- 30 seconds is about 57.2 MiB;
- 60 seconds is about 114.4 MiB;
- audio, vector capacity, packet bursts, codec config, and safety headroom add overhead.

| Metric | Old raw/no-NVENC | Target same-adapter hardware |
|---|---|---|
| Full-frame CPU copies/frame | At least 1; Optimus path effectively adds an encoder-input copy | 0 |
| GPU→CPU framebuffer bytes/s | 474.6 / 843.8 / 1,898.4 MiB/s | 0 expected; must be measured |
| CPU→GPU framebuffer bytes/s | 0 for raw save; present in cross-adapter NVENC input | 0 expected on same adapter |
| GPU work | staging copy + sync/readback | one persistent GPU copy or BGRA→NV12 conversion plus encode |
| Ring data rate @16 Mbit/s | Resolution×4×FPS | ~1.907 MiB/s plus overhead |
| 30/60-second ring | Impossible under 2 GiB at these modes | ~57/~114 MiB video payload plus bounded headroom |

These are architecture estimates, not benchmark results. AMF internal conversion, QSV
VideoProcessor cost, GPU copy queues, process CPU, hottest core, and game-frame impact
must be measured on physical hardware. A path that maps every captured texture to CPU
is not performance-resolved regardless of the encoder name.

## GOP, timestamps, save, and error contracts

The current native NVENC H.264 setup uses a four-second GOP/IDR period and no B-frames,
with a forced periodic IDR. New codecs/backends must expose requested and observed GOP,
keyframe flags, DTS/PTS, and encoder delay. Initial alpha policy should use no B-frames
and a bounded keyframe interval no longer than the current four seconds; the final
interval is accepted only after 30/60-second duration and seek/decode tests.

Each backend must preserve:

- QPC capture timestamps and AUDIT-042 selection of
  `[T_save - requested_duration, T_save]` when full history exists;
- decode pre-roll from the preceding keyframe without extending the presented duration;
- warm-up and post-recovery partial-history semantics;
- AAC synchronization against the same QPC window;
- AUDIT-028 `.partial` mux/trailer/close/atomic commit;
- no `CLIP_SAVED` on encoder, mux, trailer, or commit failure.

Required typed failure reasons include `MONITOR_NOT_FOUND`, `DISPLAY_DISCONNECTED`,
`CAPTURE_DEVICE_LOST`, `ENCODER_NOT_AVAILABLE`, `ENCODER_INIT_FAILED`,
`ENCODER_RUNTIME_FAILED`, `UNSUPPORTED_CODEC_ON_ADAPTER`, `UNSUPPORTED_GPU_PATH`, and
`REPLAY_CAPACITY_LIMITED`.

## Windows/API, package, and licence implications

- WGC `CreateForMonitor` sets the practical minimum at **Windows 10 version 1903
  (build 18362)**. The proposed monitor APIs and D3D11 are available within that floor.
- Windows 11 uses the same proposed path, but is not runtime-verified here.
- The current FFmpeg package was built with `--enable-amf`, `--enable-libvpl`, and
  `--enable-libopenh264`, and contains no separate AMF/oneVPL DLL in the release tree.
  PE dependency inspection found no static import of a new vendor DLL; AMF/Intel
  implementations remain driver/runtime responsibilities.
- The FFmpeg package remains the existing LGPLv3-or-later BtbN shared distribution
  covered by the current source/notice/hash gates.
- AMF SDK and oneVPL are MIT-licensed upstream, but the selected design consumes them
  through the already-built FFmpeg libraries and does not add SDK binaries in the first
  stage.
- Hardware H.264/HEVC/AV1 codec availability and patent/licensing obligations are not
  established merely by an SDK licence. Product/legal review of enabled distribution
  and use remains separate, especially before HEVC/AV1 are public defaults.
- No new executable and no subprocess are proposed. Binary-size change should be
  limited to FTHR engine code because the encoder frontends already exist in the shipped
  FFmpeg DLLs.
- Packaging must continue to pass the FFmpeg manifest, licence, Qt/PySide6, asset
  provenance, and repository hygiene gates.

## Test strategy and acceptance evidence required

### Deterministic tests before hardware tests

1. Pure monitor resolver fixtures:
   one monitor; two outputs on one adapter; outputs across adapters; reordered Qt/DXGI
   enumeration; primary change; duplicate friendly names; target removal; topology
   buffer retry.
2. Encoder-candidate fixtures across vendor ID, adapter LUID, requested codec, runtime
   caps, and same/cross-adapter status.
3. Codec-neutral packet/ring/mux fixtures for H.264, HEVC, and AV1 extradata, keyframe,
   PTS/DTS, recovery generation, and malformed encoder output.
4. Capability/status v5 layout tests on C++ and Python, including backwards/version
   rejection behavior.
5. Fault injection for device removed, display disconnect, encoder init/runtime failure,
   save during recovery, and bounded shutdown.

### Physical NVIDIA matrix

- RTX 4060 Ti on Windows 10 22H2: H.264 regression, then HEVC and AV1 caps/open tests.
- Primary and secondary monitor with unmistakably different pixels; switch and save.
- ≥35 s + 30 s save, ≥65 s + 60 s save, rapid saves, ffprobe, full decode.
- Record process CPU/hottest core, GPU 3D/copy/encode, RAM/private bytes/VRAM, capture
  and encode drops, save latency, codec/profile/GOP/keyframe interval.
- Compare native H.264 before/after abstraction. No material regression is accepted.

### Physical AMD matrix

For each supported H.264/HEVC/AV1 candidate, record GPU model/generation, driver,
Windows version, monitor attachment, capture/encode adapter LUIDs, actual AMF encoder,
hardware confirmation, input surface format, 1080p60, 1440p60 if supported, 30/60-second
saves, rapid save, ffprobe, full decode, drops, CPU, GPU encode engine, RAM/VRAM, and
save latency. Include an older AMD GPU without AV1 to prove truthful rejection.

### Physical Intel matrix

Prefer a common integrated Intel display owner as well as an Arc-capable system. For
each codec candidate, record the same data as AMD plus oneVPL implementation/runtime,
QSV adapter binding, D3D11→NV12/P010 conversion path, and whether surface import copied.
Include a generation without AV1 encode to prove truthful rejection.

### Hybrid matrix

- Intel display + NVIDIA dGPU: compare same-adapter QSV with existing CPU-transfer
  NVENC; automatic policy must choose the measured lower-impact path.
- AMD display/APU + NVIDIA dGPU: same-adapter AMF must win unless a measured
  GPU-resident cross-adapter path is explicitly enabled.
- External monitor on dGPU vs internal display on iGPU; monitor selection changes both
  capture and encode adapter deterministically.

### Regression/gates

After implementation, run the full requested Windows native/Python/build suite,
AUDIT-042 duration tests, transactional-save tests, contract gates, Ruff, compileall,
version/exception/hygiene/licence/Qt/asset gates, and Linux Release/CTest/Python
regression. The known unrelated Linux no-display lifecycle failure must be reported
separately, not “fixed” by this Windows work.

## Support matrix at this stop gate

The read-only host inventory found Windows 10 Pro 22H2 (build 19045), an RTX 4060 Ti,
and two active physical monitor identities, plus a Meta virtual display adapter. No AMD
or Intel GPU is present. This is sufficient to prove that a future real secondary-
monitor test is possible, but no pixel-correct runtime test was performed because this
stop-gate made no implementation.

| Configuration | Capture | Encoder | Replay | Runtime verified | Alpha status |
|---|---|---|---|---|---|
| NVIDIA, monitor directly on NVIDIA, primary/current expected output | WGC/DXGI | Native NVENC H.264 | Compressed | Previous RTX 4060 Ti evidence exists; not rerun in this docs-only gate | **LIMITED COHORT ONLY** |
| NVIDIA secondary monitor | Selection is currently ignored/wrong-risk | Native NVENC H.264 if device heuristic succeeds | Compressed but source can be wrong | Two physical monitors detected; pixel-correct switch test not run | **NOT READY** |
| NVIDIA HEVC / AV1 | Current capture | Not integrated | No codec-correct replay path | FFmpeg symbol only; RTX hardware not exercised | **NOT READY** |
| AMD H.264 / HEVC / AV1 | WGC default/primary-risk | Not integrated | Raw ≤2 GiB | No AMD hardware present | **NOT READY** |
| Intel H.264 / HEVC / AV1 | WGC default/primary-risk | Not integrated | Raw ≤2 GiB | No Intel hardware present | **NOT READY** |
| Intel display + NVIDIA | Likely Intel | NVENC H.264 CPU-input | Compressed with full-frame CPU transfer | No such local hardware | **NOT READY** |
| AMD display + NVIDIA | Adapter heuristic is incorrect | Indeterminate NVENC H.264 attempt | Indeterminate | No such local hardware | **NOT READY** |
| No usable hardware encoder | WGC/DXGI | OpenH264 only during save | Raw ~1–4 seconds at common 60 FPS modes | Architecture confirmed in code | **NOT READY** |

## Required next implementation prompt

Use the following as the next task after explicit architecture approval:

> Continue from `fix/windows-alpha-hardware-paths` and the committed
> `docs/AUDIT-048-049-WINDOWS-MONITOR-HARDWARE-ENCODING-DESIGN.md`. Implement only
> Stage 1 and Stage 2 below; do not begin AMD/Intel codec backends yet.
>
> **Stage 1 — AUDIT-048 monitor correctness:** add an injectable Windows monitor
> topology/resolver using `QueryDisplayConfig`; persist normalized
> `monitorDevicePath`; resolve it to current adapter LUID, source/target IDs,
> `HMONITOR`, and matching `IDXGIOutput`; feed the selected HMONITOR to WGC and exact
> output to DXGI; never fall back to primary/output0; implement topology-change
> generation invalidation and typed errors; add deterministic resolver tests and real
> primary/secondary pixel tests on the current two-monitor machine.
>
> **Stage 2 — codec-neutral replay seam without changing behavior:** introduce
> `IReplayEncoder`, `EncodedVideoConfig`, and codec-neutral ring/mux metadata; place the
> existing native NVENC H.264 path behind it; keep H.264 output byte/PTS/keyframe/save
> behavior equivalent; do not enable HEVC/AV1/AMF/QSV yet; add H.264 regression,
> AUDIT-042, AUDIT-028, error, and device-loss tests. Propose and version shared-memory
> v5 status fields separately before changing the ABI.
>
> Preserve the NVIDIA same-adapter GPU path, PySide6, Linux scope boundaries, licence
> gates, and transactional/timestamp contracts. Stop if the native NVENC abstraction
> adds measurable hot-path regression. Commit Stage 1 and Stage 2 separately and report
> all runtime evidence honestly.

After those stages are green, use separate implementation tasks for:

1. NVIDIA HEVC/AV1 native NVENC capability/config/extradata/mux support;
2. AMD H.264/HEVC/AV1 through FFmpeg AMF D3D11;
3. Intel H.264/HEVC/AV1 through D3D11 VideoProcessor + derived QSV;
4. capability/status v5 UI and explicit raw/no-hardware policy;
5. physical vendor/hybrid benchmark and controlled-alpha qualification.

## Final requested 54-field report at this decision point

1. **Branch:** `fix/windows-alpha-hardware-paths`.
2. **HEAD:** baseline `a873db8` before the documentation commit; final docs commit is
   reported in the task handoff.
3. **Audit IDs:** AUDIT-048 and AUDIT-049, allocated after full-history/map search found
   no existing Windows IDs.
4. **Monitor root cause:** UI value is ignored; WGC forces primary; DXGI forces
   adapter-local output 0.
5. **Monitor identity before:** Qt screen name in UI; no engine identity.
6. **Monitor identity after:** design only: persistent monitor device path plus transient
   LUID/target/source/HMONITOR/output mapping; not implemented.
7. **Multi-adapter model:** current vendor-presence heuristic; replacement is
   monitor-owner/same-adapter policy.
8. **WGC monitor mapping:** currently primary; corrected mapping designed, not built.
9. **DXGI monitor mapping:** currently output 0; corrected mapping designed, not built.
10. **Selected hardware architecture:** native NVENC preserved; in-process FFmpeg
    D3D11 AMF/QSV behind a codec-neutral interface; GPU conversion for QSV.
11. **Rejected alternatives:** all-generic NVIDIA replacement, three direct vendor
    backends, MF-only, unmeasured cross-adapter NVENC, CLI subprocess, normal raw replay.
12. **NVIDIA path:** native H.264 only; same-adapter path is the current best path.
13. **AMD path:** no integrated continuous hardware encoder.
14. **Intel path:** no integrated continuous hardware encoder.
15. **Hybrid policy:** current Intel/NVIDIA CPU path; replacement designed as same
    adapter first.
16. **No-hardware fallback:** raw BGRA + save-time OpenH264; must become explicit/limited
    or unsupported.
17. **Raw BGRA remains:** yes, current non-NVENC path.
18. **Where/why:** capture thread staging readback into `FramePool`, because only NVENC
    feeds the encoded ring.
19. **1080p60 old/new:** 4.300 s in 2 GiB; target compressed 30/60 s (~57/~114 MiB at
    16 Mbit/s), not implemented.
20. **1440p60 old/new:** 2.417 s; same bitrate-based target estimate, not implemented.
21. **4K60 old/new:** 1.067 s; same bitrate-based target estimate, not implemented.
22. **Steady CPU full-frame copies old/new:** ≥1 (≥2 memory passes in cross-adapter
    input) / target 0, unmeasured.
23. **GPU→CPU readbacks old/new:** one per frame on raw/Optimus / target 0, unmeasured.
24. **CPU→GPU uploads old/new:** cross-adapter NVENC input / target 0 same-adapter.
25. **Ring memory old/new:** ≤2 GiB raw / estimated ~57 or ~114 MiB video payload at
    16 Mbit/s, plus bounded overhead.
26. **GOP/keyframes:** current H.264 four-second forced IDR, no B-frames; other codecs
    unimplemented and unmeasured.
27. **AUDIT-042:** preserved in current code; no new backend test run.
28. **30-second save:** not run in this docs-only stop gate.
29. **60-second save:** not run.
30. **Rapid save:** not run.
31. **ffprobe:** not run on a new artifact.
32. **Full decode:** not run on a new artifact.
33. **Secondary-monitor runtime:** not verified; current code is demonstrably unsafe.
34. **Monitor switch:** not run.
35. **NVIDIA runtime:** prior cohort evidence only; not rerun.
36. **AMD runtime:** hardware absent; code not implemented.
37. **Intel runtime:** hardware absent; code not implemented.
38. **Hybrid runtime:** matching hardware absent; not verified.
39. **Windows 10:** design APIs support current Windows 10 22H2; implementation not
    qualified.
40. **Windows 11:** API-compatible by documentation; runtime not verified.
41. **Device lost:** current DXGI recovery exists but reselects unsafely; new state
    machine is design only.
42. **Save latency:** not measured.
43. **CPU/GPU benchmarks:** not run; bandwidth estimates reported above.
44. **Windows build:** not run because no product code changed.
45. **Linux regression:** not run; Linux untouched.
46. **Python tests:** not run for a docs-only architecture stop gate.
47. **Native tests:** not run.
48. **Contract gates:** baseline verified; not rerun after product changes because none
    exist.
49. **Licence gate:** existing package inspected; no dependency added; full gate not
    rerun.
50. **Asset gate:** assets untouched; full gate not rerun.
51. **Package impact:** selected future design adds no executable/DLL; driver runtimes
    required; future engine code and notices/status may change.
52. **Commits:** final documentation commit reported in handoff.
53. **Final git status:** reported after commit.
54. **Unresolved P0/P1:** AUDIT-048 and AUDIT-049; wrong-monitor risk, no AMD/Intel
    compressed replay, eight missing vendor/codec combinations beyond current NVIDIA
    H.264, dishonest raw capacity, and unverified hybrid transfers.

## Release answer

**WINDOWS SMALL ALPHA OVERALL: NO**

- **NVIDIA: LIMITED COHORT ONLY** — H.264, known primary/direct NVIDIA display setups,
  with prior runtime evidence; no general multi-monitor claim.
- **AMD: NOT READY**
- **INTEL: NOT READY**
- **MULTI-MONITOR: NOT READY**
- **HYBRID GPU: NOT READY**

The only configuration that can be considered for the next tightly controlled Windows
alpha before implementation is an NVIDIA GPU with H.264, the captured monitor directly
owned by that NVIDIA adapter, and an operator-confirmed expected primary monitor. This
is not general Windows support. No claim is made that FTHR outperforms Medal.

## Implementation addendum — 2026-08-16

This addendum records the approved Stage 1 and Stage 2 implementation. It does not
replace the investigation and decisions above. Stage 1 is commit `9bd16a0`; Stage 2 is
commit `82207f9`.

### AUDIT-048 implementation status — RESOLVED

The UI now enumerates Windows display devices with `EnumDisplayDevicesW` and stores a
normalized monitor device-interface path. The path is the persistent selection; raw
`HMONITOR` values, adapter/output objects, enumeration positions and output indexes are
never persisted. An old GDI/QScreen-name selection is migrated when it resolves
unambiguously, and primary is selected and persisted only when no valid prior selection
exists.

The engine receives the device path through the existing process argument/config path,
so shared-memory v4 is unchanged. `WindowsMonitorTopologySource` uses
`QueryDisplayConfig` plus display-config device information to resolve the selected path
to the current adapter LUID, source ID, target ID, GDI name and `HMONITOR`.
`MonitorResolver` and the DXGI-output matcher contain the injectable/pure selection
logic used by the native tests.

WGC creates its capture item from exactly the resolved `HMONITOR`. DXGI opens the
matching adapter LUID and output instead of adapter 0/output 0. A missing or stale
selection does not fall back to primary or first output. Resolution and capture failures
use the existing diagnostic channel with typed text including `MONITOR_NOT_FOUND`,
`MONITOR_DISCONNECTED`, `MONITOR_TOPOLOGY_CHANGED`,
`OUTPUT_RESOLUTION_FAILED`, and `CAPTURE_ITEM_CREATION_FAILED`.

WGC item closure/size changes and DXGI access loss invalidate the capture generation,
clear replay state, and enter the existing failed/recovery lifecycle. Recovery starts a
fresh engine generation and resolves the same persistent monitor path again; it cannot
silently capture another monitor. This preserves the existing recovery policy and avoids
replacing a live D3D/NVENC device underneath native encoder textures.

Deterministic native coverage comprises 13 monitor scenarios (14 checks), including
same- and multi-adapter layouts, secondary selection, reordered enumeration, primary
changes, normalization, missing/removed outputs, stale topology rejection and both
no-fallback requirements. Three Python tests cover enumeration/selection migration.

A real two-monitor test on the development machine captured an AOC display at
1920x1080 and an LG UltraWide at 2560x1080 through WGC/native NVENC H.264. Independent
solid red/blue test windows produced the expected distinct decoded pixel statistics in
the corresponding clips. Switching the saved selection, restarting capture and saving
again produced the selected monitor's resolution/content. A fabricated monitor path
failed with `MONITOR_NOT_FOUND` in both WGC and DXGI instead of falling back.

### AUDIT-049 Stage 2 foundation status — COMPLETE; overall finding remains OPEN

`VideoCodec` explicitly represents H.264, HEVC and AV1. `EncodedVideoConfig` carries
codec, dimensions, frame rate, stream time base, bitrate, maximum keyframe interval,
maximum B-frame count, encoded packet format and codec extradata. Validation and MP4
codec-ID mapping are codec-neutral, while a separate production gate currently accepts
only H.264.

`IReplayEncoder` is the small Windows replay seam. The existing native
`HardwareEncoder` now implements it; the NVENC/D3D11 implementation itself was not
replaced. It retains the same persistent encoder textures, `CopyResource` GPU path,
native NVENC calls, H.264 AVCC output, timestamps, packet publication, no-B-frame
contract and four-media-second forced-IDR maximum.

The encoded ring now stores an `EncodedVideoConfig` with packets and snapshots instead
of an H.264-specific extradata member. Save-time stream creation takes codec ID,
dimensions, frame rate, time base and extradata from the snapshot config. The existing
timestamp-driven AUDIT-042 interval selection and AUDIT-028 `.partial`/trailer/close/
atomic-rename transaction remain in place. Structural H.264/HEVC/AV1 MP4 mappings exist,
but no missing encoder is exposed as supported.

Native coverage now totals 20 scenarios (27 checks): the 13 monitor scenarios plus
codec mapping/config, production rejection, generic ring snapshot/extradata,
PTS/keyframe and interval-selection contracts. The real RTX 4060 Ti regression used
WGC on the 2560x1080 secondary display, native same-adapter NVENC H.264 at 60 target FPS
and 16 Mbit/s. Both 30-second saves (including an immediate rapid save) and the
60-second save reported `h264`, start time zero and exact 30.000/60.000-second container
durations; full FFmpeg decode completed without errors. Save acknowledgement latency
was approximately 82 ms.

Matched 20-second samples against a build of the pre-refactor Stage-1 commit measured
about 4.45% of one CPU core and 210.0 MiB working set before, versus 4.76–4.92% and
206.7–210.8 MiB after. This small run-to-run delta is not evidence of a meaningful
regression. More importantly, inspection confirms no new GPU-to-CPU readback,
full-frame CPU copy, format conversion, CPU-to-GPU upload, per-frame heap allocation or
packet copy. The seam adds lightweight virtual dispatch only. Shared-memory v4 exposes
captured-frame progress but not encoder/capture-drop counters; 4,189 frames were
reported after the final run and no failure/drop diagnostic appeared, so an exact drop
count is not claimed.

Production support after Stage 2 is deliberately unchanged:

| Vendor | H.264 | HEVC | AV1 |
|---|---|---|---|
| NVIDIA | Integrated — native NVENC | Not yet integrated | Not yet integrated |
| AMD | Not yet integrated | Not yet integrated | Not yet integrated |
| Intel | Not yet integrated | Not yet integrated | Not yet integrated |

AUDIT-049 therefore remains open. The codec-neutral foundation is complete, but the
eight missing vendor/codec combinations, hybrid-adapter policy and their physical
qualification remain release work. The next bounded implementation task is NVIDIA HEVC
plus NVIDIA AV1.

### Future shared-memory v5 proposal — not implemented

Any future ABI revision should use fixed-width, versioned fields for the active codec,
encoder vendor/backend, hardware/software classification, capture- and encoder-adapter
LUIDs, actual replay capacity, normalized selected-monitor identity/status, and capture/
encoder drop counters. Capability data should distinguish available, selected and
active state so the UI cannot claim an encoder that initialization rejected. This work
made no shared-memory layout, enum or version change.

## Implementation addendum — 2026-08-21 — NVIDIA codec stage

This addendum records the bounded NVIDIA-only implementation after the Stage 2
foundation. It does not widen support to AMD, Intel or hybrid-adapter configurations,
and AUDIT-049 remains **OPEN, P0**.

### Integrated NVIDIA matrix

| Vendor | H.264 | HEVC | AV1 |
|---|---|---|---|
| NVIDIA | Integrated and physically verified | Integrated and physically verified | Integrated and physically verified |
| AMD | Not integrated | Not integrated | Not integrated |
| Intel | Not integrated | Not integrated | Not integrated |

The native NVENC backend now selects and verifies the requested codec GUID and ARGB
input format against the live encode session before initialization. The pinned NVENC
headers expose API version 13.0 and the H.264, HEVC and AV1 codec/profile GUIDs used by
this implementation. All three codecs use preset P2 with low-latency tuning, the
existing VBR target/ceiling policy, no B-frames and a four-media-second maximum GOP.
Explicit HEVC or AV1 selection fails closed when that codec cannot initialize; it
cannot silently report or run H.264. Auto/H.264 retains the existing software fallback
policy.

The encoded ring's video configuration is immutable within one capture generation.
Recovery therefore creates a fresh generation/config rather than mixing codecs or
extradata. The existing AUDIT-022/023/035 health signals, AUDIT-028 transactional save,
AUDIT-042 timestamp-driven interval selection and recovery policy are preserved.

H.264 remains length-prefixed AVC with `avcC`. HEVC uses Annex-B packets and sequence
headers that FFmpeg converts to MP4 samples/`hvcC`. AV1 uses low-overhead OBU packets
and sequence header data for `av1C`. An initial AV1 configuration that suppressed
sequence headers produced a recognizable but undecodable MP4 and was rejected. The
final configuration matches the pinned FFmpeg NVENC integration: non-Annex-B AV1,
sequence headers enabled and repeated on keyframes. Full decode then passed.

No raw-frame IPC, CPU readback, CPU pixel-format conversion, CPU-to-GPU upload or new
full-frame copy was introduced. WGC and NVENC retain persistent D3D11 textures and one
`CopyResource`; only H.264 performs the pre-existing compressed-packet Annex-B-to-AVCC
conversion. HEVC and AV1 packets are published directly into the existing compressed
ring path. No new SDK binary or third-party runtime is packaged.

### Physical runtime evidence

Hardware: NVIDIA GeForce RTX 4060 Ti, driver 610.88, 1920x1080 AOC display, WGC,
same-adapter D3D11/NVENC, 60 FPS target and 16 Mbit/s. The runtime used the repository's
pinned FFmpeg `n8.1.2-21-gce3c09c101` build. Audio was disabled to isolate video codec
correctness.

| Codec | Active backend | 30-second save | 60-second save | Rapid save | Full decode | Max keyframe gap |
|---|---|---:|---:|---:|---|---:|
| H.264 | `h264_nvenc` | 30.000 s | 60.000 s | 30.000 s | Pass | 4.017 s |
| HEVC | `hevc_nvenc` | 30.000 s | 60.000 s | 30.000 s | Pass | 4.017 s |
| AV1 | `av1_nvenc` | 30.000 s | 60.000 s | 30.000 s | Pass | 4.017 s |

Each MP4 reports start time zero and stream time base `1/90000`. Warm-up saves requested
at 30 seconds after roughly five seconds of connected capture produced valid partial-
history clips of 6.283 s (H.264), 6.450 s (HEVC) and 6.217 s (AV1), rather than padded
30-second files. All three warm-up files also passed full decode. The runtime does not
expose an exact encode-drop counter, so no zero-drop claim is made.

Observed process samples were 3.52–4.05% of one CPU core, 267–271 MiB working set and
roughly 4–7% NVIDIA encoder utilization during these desktop workloads. Save completion
was observed in under one second, but was not instrumented as an internal latency
benchmark. These samples confirm no obvious stage-specific regression; they are not a
broad performance qualification.

### Verification and remaining scope

Native deterministic coverage now comprises 25 scenarios / 57 checks for codec
configuration, production backend gating, per-generation ring immutability, packet
format/extradata contracts and the existing monitor/replay behavior. Python coverage
also verifies that all three explicit codec preferences survive a settings restart.
Product build, focused tests, full regression and release/contract gates are recorded
in the implementation handoff.

This stage makes the three NVIDIA codec rows implementation-ready on the physically
tested same-adapter Windows configuration. It does **not** make the general Windows
alpha ready: AMD H.264/HEVC/AV1, Intel H.264/HEVC/AV1, hybrid capture/encode adapter
policy and their physical qualification remain AUDIT-049 P0 work. No new audit finding
is required because these are the already-defined remaining parts of AUDIT-049. The
next bounded task is exactly AMD H.264, HEVC and AV1 through FFmpeg AMF plus D3D11.

## Implementation addendum — 2026-08-21 — AMD codec stage

This addendum records the AMD H.264, HEVC and AV1 source implementation. No AMD GPU is
present on the development host, so it deliberately distinguishes code/build evidence
from real AMF runtime, media and performance evidence. AUDIT-049 remains **OPEN, P0**.

### Pinned FFmpeg and runtime evidence

The shipped Windows build is `n8.1.2-21-gce3c09c101-20260630`. Its live encoder list
contains `h264_amf`, `hevc_amf` and `av1_amf`; all three advertise D3D11 hardware input
and accept BGRA through the common AMF wrapper. The pinned `amfenc.c` wraps
`AV_PIX_FMT_D3D11` frames through `CreateSurfaceFromDX11Native`, retains the submitted
`AVFrame` until AMF releases the surface and copies only the resulting compressed
buffer into an `AVPacket`. The host-copy branch is not used.

The D3D11 frames context uses `DXGI_FORMAT_B8G8R8A8_UNORM`/`AV_PIX_FMT_BGRA` and a
fixed eight-slice GPU texture-array pool. A non-zero pool is required by this pinned
`hwcontext_d3d11va.c`; the capture loop therefore copies subresource zero of the WGC or
DXGI texture into the current encoder-owned array slice with
`CopySubresourceRegion`. FFmpeg passes the slice index to AMF. No texture is mapped.

FFmpeg derives its AMF device from the supplied D3D11 device and loads the AMD AMF
runtime dynamically (`amfrt64.dll` on 64-bit Windows). FTHR does not package an AMF
SDK/runtime DLL. Missing driver runtime, unsupported hardware or a codec-specific open
failure is returned as an initialization error. The test host has no AMD display
adapter and no system `amfrt64.dll`, which is expected and means real AMF behavior is
not verified here.

### Architecture and ownership

`FfmpegAmfReplayEncoder` implements `IReplayEncoder`. A narrow codec-session object
owns the `AVCodecContext`, D3D11 `AVHWDeviceContext`, `AVHWFramesContext`, reusable
`AVFrame`, reusable `AVPacket` and the COM reference held by FFmpeg. Destruction and
every failed initialization unwind through one reset path. The outer wrapper owns QPC
timestamping, forced-keyframe policy, packet callbacks, flush and generation-local
timing lookup. It adds no encoder thread or queue; FFmpeg/AMF owns any internal async
work.

The centralized factory selects exactly:

- selected NVIDIA adapter → native NVENC;
- selected AMD adapter → FFmpeg AMF;
- Intel/unknown adapter → current fallback;
- the pre-existing Intel-display/NVIDIA Optimus case → native NVENC CPU-input.

An AMD-owned monitor is never redirected to an unrelated NVIDIA or AMD adapter. The
AMF session independently queries the supplied D3D11 device, requires AMD vendor
`0x1002`, and verifies that the immediate context belongs to the same COM device.
Requested and active codec/config must match. H.264 may use the existing truthful
OpenH264/raw fallback after AMF failure; explicit HEVC or AV1 fails closed rather than
silently becoming H.264.

The primary supported AMD path is native-resolution, same-adapter capture. A configured
source/output resolution mismatch is rejected because adding an unmeasured D3D11
scaler was outside this bounded stage. This restriction is explicit; it cannot silently
produce the wrong resolution or fall into a continuous CPU conversion path.

### Frame, encode and packet contracts

The normal path for all three codecs is:

`WGC/DXGI BGRA texture → GPU CopySubresourceRegion → FFmpeg D3D11 BGRA frame → AMF → compressed packet → EncodedRingBuffer → MP4 transaction`.

Full-frame CPU copies, GPU-to-CPU readbacks and CPU-to-GPU uploads are all zero on that
path. There is one full-frame GPU copy and AMF performs any internal hardware color
conversion required by the codec. The raw replay `FramePool` and readback staging
texture are deferred and remain unallocated after successful AMF initialization.

All codecs use Main profile, CBR, ultra-low-latency usage, speed quality, four-frame
AMF async depth, zero B-frames, low-delay codec flags and a nominal four-second GOP.
HEVC and AV1 explicitly use eight-bit input and GOP header insertion; HEVC uses one GOP
per IDR, and AV1 requests lowest latency. Independently of frame arrival rate, the
wrapper forces an I/IDR/key frame whenever QPC-derived media PTS advances by four
seconds. No `frame_index / configured_fps` wall-time assumption was introduced.

Encoder and replay time base remain `1/fps`. Each `AVFrame::pts` comes from the existing
QPC epoch and monotonic media-time calculation. AMF packet PTS and keyframe flags are
required and passed unchanged into the ring; no-B-frame MP4 output continues to use
`DTS = PTS`, rescaled from encoder time base to stream time base `1/90000`. This
preserves the AUDIT-042 interval model.

H.264 AMF Annex-B output is normalized to length-prefixed samples and its SPS/PPS are
converted to `avcC`. HEVC retains Annex-B access units/extradata for the pinned MP4
muxer to form `hvcC`. AV1 retains low-overhead OBUs and sequence-header data for
`av1C`. No packet is admitted until a valid immutable `EncodedVideoConfig` is available.
Recovery destroys the codec/hardware contexts, clears the replay generation and creates
a fresh config; old callbacks cannot arrive because the wrapper has no background
thread.

### Memory/performance model

The removed normal AMD CPU traffic was approximately 498 MB/s at 1080p60, 885 MB/s at
1440p60 and 1.99 GB/s at 4K60, before later save-time encoding. At 16 Mbit/s the
compressed video payload is approximately 2 MB/s, 60 MB for 30 seconds or 120 MB for
60 seconds (about 57.2/114.4 MiB), plus packet overhead. The eight-slice BGRA AMF input
pool is approximately 63.3 MiB at 1080p, 112.5 MiB at 1440p or 253.1 MiB at 4K. GPU
copy bandwidth still exists, but no equivalent full-resolution bandwidth crosses the
CPU memory boundary. Gaming-FPS or quality improvements are not claimed without AMD
hardware measurements.

### Deterministic verification and limitations

The native executable now reports 49 scenarios / 83 checks. Its 24 AMD scenarios cover
adapter policy, exact H.264/HEVC/AV1 encoder names, all missing-encoder cases, encoder
open and hardware-context failures, real non-AMD D3D11 rejection, no silent codec
fallback, three `EncodedVideoConfig` forms, ring/mux config, timestamp/keyframe and
subresource propagation, generation immutability, flush/error behavior, raw-pool
avoidance and transactional cleanup after a simulated mux-writer failure.

The Windows Release x64 solution builds with zero compiler/linker warnings and errors.
The full Python result is 391 passed / 30 platform skips. Ruff, compileall, version,
response, shared-memory v4 (Windows 2736 bytes / Linux 4272 bytes), exception and
hygiene gates pass. The source licence gate passes 76 checks with only the known absent
vendored-Linux-FFmpeg warning; the existing Windows bundle passes 84/84 licence, Qt and
asset checks. No package file or proprietary runtime was added. Linux source was not
modified and Linux runtime regression was not executed on this Windows host.

The available RTX 4060 Ti still opened `h264_nvenc`, `hevc_nvenc` and `av1_nvenc` in
the pinned FFmpeg build; short smoke files for all three fully decoded. Prior product
30/60-second NVIDIA evidence remains authoritative and the deterministic factory tests
prove NVIDIA never routes to AMF.

Real AMD fields remain **NOT VERIFIED — NO AMD HARDWARE**: GPU/driver capability,
H.264/HEVC/AV1 open and encode, 30/60-second saves, warm-up/recovery saves, rapid save,
ffprobe, full decode, observed keyframe gaps, save latency, process CPU, GPU video
encode utilization and memory. Local qualification must run that complete matrix on an
AMD-owned monitor, including an older GPU that truthfully rejects AV1 if unsupported.

### Status after the AMD stage

| Vendor | H.264 | HEVC | AV1 |
|---|---|---|---|
| NVIDIA | Integrated + verified | Integrated + verified | Integrated + verified |
| AMD | Code integrated; hardware unverified | Code integrated; hardware unverified | Code integrated; hardware unverified |
| Intel | Not integrated | Not integrated | Not integrated |

AMD codec stage verdict: **CODE READY / HARDWARE UNVERIFIED** for the bounded
same-adapter native-resolution architecture. AUDIT-049 remains **OPEN, P0** because
Intel H.264/HEVC/AV1 and hybrid-GPU qualification remain. No new audit finding is
created: the architecture is the already-approved AUDIT-049 AMD stage and introduces
no new binary, IPC contract or security boundary. General Windows alpha remains
**NO**. The next task is exactly Intel H.264, HEVC and AV1 through QSV/oneVPL plus
D3D11; Intel work has not begun here.

## Implementation addendum — 2026-08-21 — Intel codec stage

This addendum records the same-adapter Intel H.264, HEVC and AV1 source integration.
The development host has only an NVIDIA GeForce RTX 4060 Ti and no Intel display
adapter, so code/build evidence is kept separate from QSV runtime, media and
performance evidence. AUDIT-049 remains **OPEN, P0**.

### Exact pinned FFmpeg/QSV evidence

The shipped Windows build is `n8.1.2-21-gce3c09c101-20260630`, revision
`ce3c09c101c83add623774d414a9f9498caf5c25`. Its live encoder list contains
`h264_qsv`, `hevc_qsv` and `av1_qsv`; the build configuration enables `libvpl`, and
the hardware-device list contains both `qsv` and `d3d11va`. The three encoders accept
`AV_PIX_FMT_QSV`; their eight-bit common software format is NV12.

The implementation was checked against the exact revision's
`libavutil/hwcontext_qsv.c:qsv_device_derive_from_child`,
`qsv_frames_derive_from`, `qsv_map_to` and `qsv_map_from`, plus
`libavutil/hwcontext_d3d11va.c`. Device derivation passes the selected
`ID3D11Device` into oneVPL and constrains selection with the Intel device identity and
adapter LUID. Fixed D3D11 pools preserve the array-slice handle in the QSV
`mfxHDLPair`; a pool carrying `D3D11_BIND_RENDER_TARGET` instead uses
`MFX_INFINITE` for the second handle. FTHR therefore deliberately separates the
single VideoProcessor render target from the eight-slice encoder pool.

The exact `libavcodec/qsvenc.c` uses the caller's `AV_PIX_FMT_QSV` hardware frames,
an async FIFO and forced-IDR behavior. `qsvenc_h264.c` and `qsvenc_hevc.c` expose
decoder configuration after open. With global headers, `qsvenc_av1.c` applies the
`extract_extradata` bitstream filter, so AV1 decoder configuration arrives as
`AV_PKT_DATA_NEW_EXTRADATA` with the first encoded packet. AV1 also requires a
sufficient oneVPL implementation version; unsupported Intel generations fail the
codec open rather than changing codec.

`avcodec-62.dll` has no direct dependency on a separate `vpl` or `mfx` DLL. The
pinned FFmpeg build was linked with the oneVPL dispatcher; the installed Intel
graphics driver must provide the actual QSV/oneVPL implementation. FTHR adds and
packages no DLL, SDK binary or proprietary runtime. Missing runtime, driver support
or codec support is a truthful initialization error. The existing FFmpeg LGPL
provenance and release-license gate remain unchanged.

### Architecture, conversion and ownership

`FfmpegQsvReplayEncoder` implements `IReplayEncoder`. Its production codec session
owns the FFmpeg D3D11 device context, QSV device derived from that exact context,
D3D11 NV12 frames context, derived QSV frames context, reusable frames/packet and
all D3D11 VideoProcessor objects. The selected monitor's AUDIT-048 adapter remains
authoritative: vendor `0x8086` is required, and the supplied immediate context must
belong to the same COM device. No default or unrelated Intel adapter is opened.

The persistent pipeline is:

`WGC/DXGI BGRA texture → D3D11 VideoProcessor scale/color conversion → persistent NV12 render target → CopySubresourceRegion into current D3D11/QSV pool slice → h264_qsv/hevc_qsv/av1_qsv → compressed ring → existing MP4 transaction`.

Microsoft's D3D11 contract permits eligible default textures (including bind flags
zero or supported video/render combinations) as VideoProcessor inputs and requires
`D3D11_BIND_RENDER_TARGET` for the output. The source/output rectangles make scaling
and BGRA-to-NV12 conversion one VideoProcessor operation. The converter texture,
output view, enumerator, processor and eight encoder surfaces are persistent. Only
the input view follows the acquired capture resource per frame; no full-resolution
heap buffer or application encoder thread was added.

All three codecs use eight-bit NV12. The encoder pool is a fixed eight-slice D3D11
texture array with `D3D11_BIND_DECODER | D3D11_BIND_VIDEO_ENCODER`, enough for QSV
`async_depth=4`, the prepared frame and transient retained references. It intentionally
does not carry the render-target flag, so the exact FFmpeg mapping retains each array
slice. There are zero CPU full-frame copies, zero GPU-to-CPU readbacks and zero
CPU-to-GPU uploads. Each frame performs one GPU VideoProcessor conversion/scale and
one NV12 GPU copy.

At 60 FPS, avoiding the old BGRA CPU path removes approximately 498 MB/s at 1080p,
885 MB/s at 1440p and 1.99 GB/s at 4K. The remaining NV12 GPU copy is approximately
187, 332 and 746 MB/s respectively. The eight-slice NV12 pool plus one converter
surface uses about 26.7 MiB at 1080p, 47.5 MiB at 1440p or 106.8 MiB at 4K. At
16 Mbit/s, compressed replay payload remains about 60 MB for 30 seconds or 120 MB for
60 seconds, before small container/packet overhead. No gaming-FPS improvement is
claimed without Intel hardware measurement.

### Codec, timing, config and failure contracts

H.264 maps only to `h264_qsv`, HEVC only to `hevc_qsv`, and AV1 only to `av1_qsv`.
All use Main profile, target/minimum/maximum rate at the configured bitrate, very-fast
preset, low-delay/global-header/closed-GOP flags, no B-frames, look-ahead depth zero,
QSV async depth four and forced IDR. The wrapper independently forces a keyframe at
most every four media seconds. Requested and active codec/packet format must match;
there is no QSV codec fallback and no software encoder reported as Intel hardware.

Time base remains `1/fps`. QPC-derived monotonic media PTS is submitted to FFmpeg;
packet PTS and keyframe status pass to the replay ring, while absent packet DTS uses
PTS under the zero-B-frame policy. This keeps AUDIT-042's timestamp-selected 30/60
second interval model unchanged. Send/receive handles EAGAIN, drains every available
packet and flushes to EOF on shutdown.

H.264 Annex-B packets are normalized to length-prefixed samples and SPS/PPS become
`avcC`. HEVC remains Annex B with encoder extradata for the pinned MP4 muxer to form
`hvcC`. AV1 remains low-overhead OBU. Because pinned QSV may produce AV1 sequence
configuration with the first packet, the ring publishes its immutable
`EncodedVideoConfig` immediately before admitting that packet. Missing or changing
configuration fails the generation. Device removal, conversion, QSV session,
send/receive or config-publication failure enters the existing recovery/failure path;
no stale packet is accepted.

Successful Intel initialization skips both the raw replay pool and readback staging
texture. Save remains compressed snapshot to codec-neutral MP4 mux to AUDIT-028
temporary file/atomic rename, with no video re-encode. The shared-memory v4 layout,
UI/engine IPC, Linux implementation, NVIDIA native path and AMD AMF path are unchanged.

### Verification and remaining scope

The native executable reports 76 scenarios and 115 checks after adding the real WARP
wrong-device rejection. Its 27 Intel requirements cover vendor routing, all three
exact QSV names, missing runtime/codec failures, device/context/derivation/conversion
failures, no silent codec fallback, three config forms, ring/mux/transaction behavior,
PTS/keyframe propagation, deferred AV1 config, generation isolation, flush/errors,
raw-pool avoidance and NVIDIA/AMD policy regressions.

Release x64 builds successfully. Python regression is 391 passed with 30 Windows
platform skips. Ruff, compileall, version, engine-response, shared-memory v4, exception
and hygiene gates pass. The combined source/existing-Windows-artifact release gate
passes 160 checks with only the known warning that a vendored Linux FFmpeg is absent
on this Windows checkout. PySide6/Qt and all 25 source assets plus 20 packaged Windows
assets remain approved. Linux source was not modified.

Real Intel fields remain **NOT VERIFIED — NO INTEL HARDWARE**: GPU/driver, H.264/HEVC/
AV1 codec opens, 30/60-second and rapid saves, ffprobe, full decode, observed keyframe
gaps, save latency, process CPU, GPU conversion/encode utilization and memory. Local
qualification must run that matrix on an Intel-owned monitor; an older Intel device
that lacks AV1 must report AV1 unsupported without invalidating working H.264/HEVC.

| Vendor | H.264 | HEVC | AV1 |
|---|---|---|---|
| NVIDIA | Integrated + verified | Integrated + verified | Integrated + verified |
| AMD | Code integrated; hardware unverified | Code integrated; hardware unverified | Code integrated; hardware unverified |
| Intel | Code integrated; hardware unverified | Code integrated; hardware unverified | Code integrated; hardware unverified |

Intel codec stage verdict: **CODE READY / HARDWARE UNVERIFIED**. AUDIT-049 remains
**OPEN, P0** because AMD/Intel physical qualification, hybrid-GPU policy and final
capability truth remain. No new audit ID is needed: this is the planned Intel portion
of AUDIT-049 and adds no binary, IPC version or separate security boundary. General
Windows alpha remains **NO**.

## AUDIT-049 — Final qualification — 2026-08-21

### Decision and status

AUDIT-049 remains **OPEN, P0**. The code/policy portion is complete, and the physically
tested NVIDIA same-adapter cohort is ready for a controlled alpha. AMD and Intel remain
**CODE READY / HW UNVERIFIED** because this machine has no AMD or Intel adapter. Hybrid
GPU capture/encoding remains **NOT READY**. These missing physical results are existing
AUDIT-049 scope, not a new independent finding.

The final public-alpha policy is:

1. The selected monitor/capture D3D11 adapter is authoritative.
2. NVIDIA uses native NVENC, AMD uses FFmpeg AMF, and Intel uses FFmpeg QSV on that
   exact adapter/device.
3. The requested codec must initialize and must equal the active codec.
4. An unrelated vendor elsewhere in the machine cannot steal encoder selection.
5. Automatic cross-adapter encoding and the legacy raw replay fallback are disabled.
6. Failure stops capture startup with a structured, visible error; it never claims a
   30/60-second replay that is not physically available.

`SelectWindowsReplayPolicy` is the sole production selection policy. Hardware probing
is generation-local: the backend is opened once during engine startup on the current
capture device. A monitor/topology/device/codec change requires a fresh engine
generation, so there is no persistent adapter/driver capability cache to become stale.

### Hybrid policy evaluation

| Policy | Transfer/CPU behavior | Reliability and maintenance | Alpha decision |
|---|---|---|---|
| A — capture-adapter encoder | Existing same-device GPU conversion/copy only; no cross-adapter full frame | Smallest surface, already represented by all three backends | **Selected** |
| B — NVIDIA whenever present | May require Intel/AMD GPU → CPU → NVIDIA upload or unqualified shared-resource behavior | Reintroduces the removed vendor-presence heuristic | Rejected |
| C — strongest codec encoder | Same transfer uncertainty as B and selection changes by codec | Requires per-pair capability/performance qualification | Rejected |
| D — automatic cost-aware | Needs trustworthy runtime cost measurements, topology cache and recovery for every adapter pair | Highest complexity and alpha-blocker risk | Deferred |
| E — same-adapter + user override | Same safe default; override still needs an approved transfer path and explicit cost | Reasonable future product direction | Default half selected; override deferred |

The former Intel-display/NVIDIA CPU-input path was removed from production selection.
It performed a full BGRA readback and upload and was created by enumerating NVIDIA
presence rather than by a qualified adapter-pair policy. No replacement cross-adapter
path is advertised.

Direct3D 11.1 can share eligible 2D textures using NT handles and keyed-mutex
synchronization, but this alone does not prove a zero-copy cross-*adapter* encode path.
Windows hybrid cross-adapter resources require driver support, one shared linear
allocation in the aperture segment, pitch/height alignment and explicit synchronization.
D3D11.3 row-major cross-adapter layouts may avoid application CPU staging, but still
need per adapter-pair compatibility, encoder acceptance, synchronization and performance
qualification. Therefore shared-handle, cross-adapter texture and D3D12 bridge designs
remain future measured work, not an alpha fallback. Primary references:

- Microsoft [`D3D11_RESOURCE_MISC_FLAG`](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ne-d3d11-d3d11_resource_misc_flag)
  and [`OpenSharedResource1`](https://learn.microsoft.com/en-us/windows/win32/api/d3d11_1/nf-d3d11_1-id3d11device1-opensharedresource1)
  documentation;
- Microsoft [*Using Cross-Adapter Resources in a Hybrid System*](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/using-cross-adapter-resources-in-a-hybrid-system);
- Microsoft [*Default Texture Mapping*](https://learn.microsoft.com/en-us/windows/win32/direct3d11/default-texture-mapping)
  (row-major cross-adapter layout).

The actual capture format is BGRA, four bytes/pixel. One full-frame transfer at 60 FPS
is approximately:

| Resolution | One direction | CPU readback + upload |
|---|---:|---:|
| 1920×1080 | 497.7 MB/s (474.6 MiB/s) | 995.3 MB/s (949.2 MiB/s) |
| 2560×1440 | 884.7 MB/s (843.8 MiB/s) | 1.77 GB/s (1.65 GiB/s) |
| 3840×2160 | 1.99 GB/s (1.85 GiB/s) | 3.98 GB/s (3.71 GiB/s) |

Those figures exclude row-pitch padding, additional copies, synchronization and PCIe/
shared-memory contention. The CPU-staged route is therefore forbidden as an automatic
gaming-capture default.

### Capability truth and failure behavior

The internal generation capability records requested/active codec, active backend,
hardware state, capture/encoder vendor, same-adapter state, initialization result and
generation. Startup logs emit the corresponding `ReplayCapability` record. On success,
the frozen v4 fields `nvenc_active` (legacy name meaning any Windows hardware encoder)
and `active_codec` publish actual backend truth such as `h264_nvenc`, `hevc_amf` or
`av1_qsv`; the selected monitor path and configured history already remain available
to the two processes. Because all successful alpha paths are hardware, same-adapter and
compressed, replay capacity equals configured compressed history. No extra ABI fields
are required to make an alpha go/no-go decision.

On failure the engine emits a bounded `FTHR_STARTUP_ERROR` record. The Windows UI
captures native startup output and displays the code/detail instead of the former
generic “engine not responding” message. Supported error classes include
`REQUESTED_CODEC_UNSUPPORTED`, `HARDWARE_ENCODER_UNAVAILABLE`,
`CAPTURE_ADAPTER_UNSUPPORTED`, `CROSS_ADAPTER_PATH_UNAVAILABLE`,
`REPLAY_CAPACITY_LIMITED` and `ENCODER_INIT_FAILED`. FFmpeg AMF/QSV and native NVENC
preserve their backend detail. There is no silent codec substitution.

Shared memory therefore remains **v4**, exactly 2736 bytes on Windows and 4272 bytes on
Linux. A v5 would be useful for richer live diagnostics or future user-selectable
cross-adapter routing, but is not required for this fail-closed controlled cohort and
was not implemented.

The raw-capacity calculator remains deterministic for diagnostics. For example,
512 MiB holds only 64 whole 1920×1080 BGRA frames, or 1.066 seconds at 60 FPS. The
production policy refuses capture rather than representing that as a 60-second replay.

### Repeatable physical qualification

`tools/qualify_windows_hardware.py` provides the local qualification sequence. It
starts one fresh engine generation per requested codec, reads actual `active_codec`
from shared memory, captures a partial warm-up, full 30-second and 60-second histories,
performs two rapid sequential saves, probes every file, fully decodes video, extracts
keyframe timestamps, checks audio presence and confirms no final `.partial` remains.
It writes a JSON report and one engine log per codec. Example:

```powershell
python tools\qualify_windows_hardware.py --codec all --output build\audit-049-machine
```

The operator must close the normal FTHR application first. Optional `--monitor`,
`--width`, `--height`, `--fps`, `--bitrate` and executable-path arguments support
another machine or packaged build. GPU 3D/copy/video-encode counters and game FPS lows
remain external measurements; the report labels them rather than inventing values.

### NVIDIA physical regression — pass

Machine: Windows 10 Pro 22H2, build 19045; NVIDIA GeForce RTX 4060 Ti; driver WMI
`32.0.16.1088` (NVIDIA 610.88); AOC 1920×1080 monitor; exact monitor device path and
adapter LUID `0:124769`; native resolution, 60 FPS, 16 Mbit/s, audio enabled. Every
generation logged capture vendor NVIDIA, encoder vendor NVIDIA, native-NVENC backend,
same-adapter policy, compressed ring and skipped raw pool.

| Codec | Active codec | Warm-up | 30-second | 60-second | Rapid pair | Full decode | Audio | Max keyframe gap |
|---|---|---:|---:|---:|---|---|---|---:|
| H.264 | `h264_nvenc` | 5.802 s | 30.016 s | 60.010 s | 5.013/5.013 s | Pass (all) | Present | 4.017 s |
| HEVC | `hevc_nvenc` | 5.933 s | 30.016 s | 60.010 s | 5.013/5.013 s | Pass (all) | Present | 4.017 s |
| AV1 | `av1_nvenc` | 5.917 s | 30.016 s | 60.010 s | 5.013/5.013 s | Pass (all) | Present | 4.017 s |

Every output had the requested codec/resolution, every final `.partial` was absent and
capture health remained active. Save latency ranges were 0.252–2.171 s (H.264),
0.252–2.170 s (HEVC) and 0.302–2.168 s (AV1). End-of-run working set/private bytes were
approximately 259/618 MB, 380/732 MB and 341/706 MB respectively. Average engine-process
CPU was 11.25%, 11.34% and 11.20% of one logical-core equivalent over the three runs.
These are desktop integration observations, not gaming-overhead qualification or a
Medal comparison; GPU counters still require an external measurement pass.

### AMD, Intel, scaling, OS and packaging truth

No AMD/Intel physical result is claimed. For each codec, runtime open, 30/60-second
duration, rapid save, probe/decode, keyframes, latency, CPU, GPU encode, memory and
device-loss recovery remain **NOT VERIFIED — HARDWARE ABSENT**. AMD downscale behavior
is not advertised; its current same-adapter cohort remains native-resolution only.
Intel GPU VideoProcessor scaling is code-integrated but remains hardware-unverified.

Windows 10 22H2 is physically verified only for the NVIDIA cohort above. Windows 11,
AMD and Intel OS/driver combinations remain unverified. The package keeps the pinned
FFmpeg encoders `h264_amf`/`hevc_amf`/`av1_amf` and
`h264_qsv`/`hevc_qsv`/`av1_qsv`; no AMF DLL, oneVPL/QSV implementation DLL or new
vendor binary was added. AMF uses the driver runtime; the Intel driver supplies the
hardware implementation. Missing runtimes fail at initialization and reach the UI.

### Final matrix and alpha cohort

| Vendor | H.264 | HEVC | AV1 |
|---|---|---|---|
| NVIDIA | **VERIFIED** | **VERIFIED** | **VERIFIED** |
| AMD | **CODE READY / HW UNVERIFIED** | **CODE READY / HW UNVERIFIED** | **CODE READY / HW UNVERIFIED** |
| Intel | **CODE READY / HW UNVERIFIED** | **CODE READY / HW UNVERIFIED** | **CODE READY / HW UNVERIFIED** |

- NVIDIA same-adapter: **READY** on the tested RTX 4060 Ti/driver/Windows 10 cohort.
- AMD same-adapter: **CODE READY / HW UNVERIFIED**.
- Intel same-adapter: **CODE READY / HW UNVERIFIED**.
- Intel-display + NVIDIA and AMD-display + NVIDIA: **NOT READY**.
- Multi-monitor: **READY** under the resolved AUDIT-048 exact-monitor contract.
- Windows alpha: **CONTROLLED COHORT ONLY**; not general Windows support.

AUDIT-028 transactional saves, AUDIT-042 timestamp duration, AUDIT-048 monitor mapping,
capture health/recovery and the NVIDIA 3×3 path remain intact. The only next AUDIT-049
task is representative AMD and Intel physical qualification with the reusable harness;
the audit cannot close until that evidence exists or those cohorts are explicitly
removed from public support.
