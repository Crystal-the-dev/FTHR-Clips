# Windows capture privacy indicator

## Product rule

The yellow/coloured border shown during Windows Graphics Capture (WGC) is a
Windows privacy indicator, not an FTHR overlay defect. FTHR leaves it in place
unless Windows explicitly permits a borderless WGC session. It does not hide,
cover, inject into, patch, or otherwise bypass the indicator.

## Source and current backend behaviour

`CaptureEngine::InitializeWGC` creates a monitor item with WGC
`CreateForMonitor`; `InitializeWindowCapture` creates a window item with WGC
`CreateForWindow`. Both create a `GraphicsCaptureSession`, so either WGC path
can display the system indicator. The capture-border policy is applied once to
each new WGC session after creation and before `StartCapture`.

The DXGI Desktop Duplication fallback owns an `IDXGIOutputDuplication` and never
creates a WGC session. It therefore does not use the WGC coloured-border
mechanism. That is source/architecture evidence, not a reason to switch
backends: WGC remains preferred because it preserves the selected-monitor and
anti-cheat/independent-flip behaviour already qualified for FTHR.

## Official API and access contract

Microsoft documents `GraphicsCaptureSession.IsBorderRequired` as the supported
control for a window or display capture border. It was added in Windows build
20348. Before setting it to `false`, the app must request
`GraphicsCaptureAccessKind::Borderless`; the request requires a package manifest
that declares `graphicsCaptureWithoutBorder`. A denied request may leave the
system border visible even when a caller tries to set the property, so FTHR
never reports success from a setter call alone.

Primary references:

- [GraphicsCaptureSession.IsBorderRequired](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession.isborderrequired?view=winrt-28000)
- [GraphicsCaptureAccess.RequestAccessAsync](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscaptureaccess.requestaccessasync?view=winrt-28000)
- [GraphicsCaptureAccessKind](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscaptureaccesskind?view=winrt-28000)
- [graphicsCaptureWithoutBorder capability](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/app-capability-declarations)

## Support matrix

| OS | WGC monitor/window capture | Official border opt-out | Current FTHR Inno artifact |
|---|---|---|---|
| Windows 10 1903 (18362) | Yes | No | Normal Windows indicator remains |
| Windows 10 22H2 (19045) | Yes | No | Normal Windows indicator remains |
| Windows 11 build 22000 / 22621 / 22631 and later | Yes | Yes, from build 20348 | Not entitled yet: the current installer is unpackaged and has no manifest capability |

The active Windows installer is Inno Setup and has no MSIX/AppX identity or
package manifest. The checked-in release build therefore leaves
`FTHR_BORDERLESS_CAPTURE_CAPABILITY_DECLARED` undefined and cannot issue a
borderless access request. A later identity-capable installer may set that build
flag **only** when its manifest declares `graphicsCaptureWithoutBorder`; the
runtime still checks package identity, OS build, consent result and the optional
`IGraphicsCaptureSession3` interface before changing the session property.
The current Inno lifecycle and the deferred sparse-package decision are recorded
in [`WINDOWS-INSTALLER-LIFECYCLE.md`](WINDOWS-INSTALLER-LIFECYCLE.md).

## Runtime policy and recovery

`windows_capture_border_policy` produces one explicit decision from these facts:

1. WGC versus non-WGC backend;
2. actual Windows build;
3. package identity;
4. declared package capability;
5. Windows access result; and
6. session-interface/property result.

On an eligible packaged build FTHR requests only `Borderless` access once per
engine lifetime. Recovery, monitor switching and background/UI launches reuse a
previously granted or denied result; they do not create a permission-prompt loop.
Every new WGC session still re-applies a granted policy before capture starts.
Denial, an unavailable interface or a setter/readback failure keeps capture
running normally with the Windows indicator.

The concise per-session diagnostic includes the target, build, identity,
capability, access state, whether this session sent a request, whether it tried
the property, the effective result and a reason. A Windows 10 19045 session is
expected to report `api_supported=false`, `effective=false` and
`reason=build_unsupported`; it must never claim that the border was disabled.

## Compatibility, performance and qualification

The new build gate avoids asking a pre-20348 system for the newer access API.
On a supported build it uses `try_as<IGraphicsCaptureSession3>` before accessing
the newer session interface, so a missing interface is a normal fallback rather
than a startup failure. The only new Win32 identity probe,
`GetCurrentPackageFullName`, is available on all supported Windows 10 versions.

There is no per-frame work, frame copy, memory allocation, capture-generation
change, shared-memory change or encoder/audio/screenshot-path change. The work
is one bounded policy evaluation per WGC session creation.

The current physical machine is Windows 10 Pro build 19045 with an RTX 4060 Ti.
That build cannot use the official borderless API, so physical border removal is
**unsupported on this host** and no before/after disappearance is claimed. A
future Windows 11 build >=20348 test must use an identity-capable package with
the declared capability, approve or deny the system request once, and verify
monitor selection, capture recovery and a decoded replay save visually.
