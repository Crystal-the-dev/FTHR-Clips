# Canonical audit-ID map

Last reconciled: **2026-08-21**

This register is the canonical allocation map for audit IDs appearing in the
committed repository. It prevents a later finding from silently replacing an
existing audit. Historical IDs are not renumbered merely to close gaps.

| ID | Canonical finding / reservation | State in committed documentation |
|---|---|---|
| AUDIT-001 | Multiple instances corrupt shared single-writer state | Fixed |
| AUDIT-002 | Failed clip writes were reported as successful | Fixed |
| AUDIT-003 | Linux hotkey socket permissions exposed capture commands | Fixed |
| AUDIT-003b | Fixed Linux hotkey socket path was unsafe in `/tmp` | Fixed |
| AUDIT-004 | Windows deep-path failures had the wrong diagnostic | Fixed |
| AUDIT-005 | GPL FFmpeg was bundled under incomplete licence notices | Resolved |
| AUDIT-006 | Linux external tools were resolved through untrusted `PATH` entries | Fixed |
| AUDIT-007 | Swallowed exceptions prevent useful diagnostics | Partially fixed |
| AUDIT-008 | No authoritative version-controlled source tree | Resolved |
| AUDIT-009 | Runtime and build dependencies were unpinned | Resolved |
| AUDIT-010 | About screen version drift | Fixed |
| AUDIT-011 | Clip-save handshake blocked the Qt event loop | Fixed |
| AUDIT-012 | Upload credential storage / URL privacy hardening | Open |
| AUDIT-013 | Qt Python binding licensing / bundled-asset provenance | Resolved; PySide6/LGPL packaging and per-asset provenance gates verified |
| AUDIT-014 | Linux FFmpeg/AppImage packaging and licensing | Conditionally resolved |
| AUDIT-015 | Linux successful saves could miss the acknowledgement window | Fixed |
| AUDIT-016 | Debian-family AppImage portability failures | Fixed |
| AUDIT-017 | A new save could erase an unconsumed prior verdict | Fixed |
| AUDIT-018 | Engine response was published before its payload | Resolved |
| AUDIT-019 | Windows engine errors lacked diagnostic payloads | Fixed |
| AUDIT-020 | Engine connection retries block the Qt event loop | Open |
| AUDIT-021 | Windows failed saves were reported as successful | Fixed |
| AUDIT-022 | Capture progress / health admission | Conditionally resolved |
| AUDIT-023 | Linux capture failure, recovery, and generation handling | Conditionally resolved |
| AUDIT-024 | Wayland bounded dispatch and recovery | Code fixed; real desktop runtime unverified |
| AUDIT-025–026 | No definition found in committed documentation | Unallocated; investigate history before reuse |
| AUDIT-027 | Broader post-processing “fully ready” semantics | Referenced by AUDIT-028; definition absent from committed tree, so reserved |
| AUDIT-028 | Transactional / crash-safe clip saves | Code fixed; automated tested |
| AUDIT-029–033 | No definition found in committed documentation | Unallocated; investigate history before reuse |
| AUDIT-034 | Audio-device / synchronization scenarios | Referenced by AUDIT-042; definition absent from committed tree, so reserved |
| AUDIT-035 | Conservative black/uniform-frame content health | Conditionally resolved |
| AUDIT-036–039 | No definition found in committed documentation | Unallocated; investigate history before reuse |
| AUDIT-040 | Linux Release could mix system FFmpeg generations | Resolved |
| AUDIT-041 | Credential-shaped test fixtures failed the hygiene gate | Resolved |
| AUDIT-042 | Short-save / clip-duration correctness | Resolved for full-history replay saves |
| AUDIT-043 | Linux FFmpeg capability probes could false-pass loader failures | Resolved |
| AUDIT-044 | X11 reads are not cancellation/deadline bounded | Open |
| AUDIT-045 | X11 worker IPC, lifecycle, performance, and packaging validation | Reserved; separate from AUDIT-044 and not implemented here |
| AUDIT-046 | Linux pinned FFmpeg header/library ABI mismatch | Resolved |
| AUDIT-047 | Linux encoded snapshot copies hold the ring mutex | Open, P2 |
| AUDIT-048 | Windows monitor identity and adapter/output mapping | Resolved; stable monitor-device-path mapping and real two-monitor capture verified |
| AUDIT-049 | Windows vendor/codec hardware replay and hybrid-GPU policy | Open, P0; NVIDIA H.264/HEVC/AV1 integrated and physically verified, six AMD/Intel vendor/codec combinations plus hybrid qualification remain |
| AUDIT-050 | Multi-Audio Architecture Validation | Implementation in progress; source/manifest/session/AAC-default-mix and paired multi-track mux foundations landed, but Windows 11 runtime, native mic, actual multi-stem providers/playback and qualification remain open |

When allocating a new ID, search the full history and this map first. A missing
standalone report does not make a referenced or reserved ID reusable.
