# AUDIT-013 — Release asset provenance

> **Project-licence update (2026-08-24):** FTHR application source now uses
> `GPL-3.0-only`. The generated assets inventoried here retain their explicit
> MIT grant under `licenses/FTHR-GENERATED-ASSETS.txt` and
> `licenses/MIT.txt`; Oswald remains OFL-1.1.

Date: **2026-08-14**

Status: **RESOLVED IN SOURCE AND ENFORCED BY RELEASE GATES**

This is an engineering evidence record, not legal advice. The canonical,
machine-readable inventory is `tools/release_asset_manifest.json`; its SHA-256
values are enforced by `tools/verify_release_licenses.py`.

## Evidence keys

- **G1 — FTHR generated:** the bytes are reproducibly produced by
  `tools/generate_release_assets.py` version 1 using only Python standard-library
  drawing and PCM synthesis. It consumes no external images, fonts, samples, or
  icon library. The generator, inputs, copyright statement, and MIT grant are
  shipped under `licenses/FTHR-GENERATED-ASSETS.txt`.
- **O1 — Oswald upstream:** the repository font is byte-for-byte identical to
  `fonts/ttf/Oswald-Bold.ttf` at Google Fonts' Oswald repository revision
  `89795261ac9eeb9aa8cd99f43982c4e4b0e53261`. Both files have SHA-256
  `eb7d46f856dd57f18a8c03d033c57802692bf127f01dbd95ba8984338e6b5135`.
  Embedded name records identify Oswald Bold version 4.103 and state
  `Copyright 2016 The Oswald Project Authors`; `fsType` is `0x0000`. The
  upstream OFL text is preserved verbatim as `licenses/Oswald-OFL-1.1.txt`.

## Complete current inventory

`W` means the asset is shipped by the Windows packaging path, `L` means it is
shipped by the Linux packaging path, and `R` means repository/documentation
only. `Embedded` means the source asset is compiled into the executable or
installer rather than copied as a standalone artifact file.

| Path / filename | Type | Source/build use | Delivery | Notice / origin | Status |
|---|---|---|---|---|---|
| `.github/social_preview.png` | PNG | GitHub repository social preview | R | MIT; G1 | APPROVED |
| `AppDir/fthr-clips.png` | PNG | AppImage root icon; `build_linux.sh:230` | L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/fonts/Oswald-Bold.ttf` | TTF | Qt font registration in `FTHR_UI/main.py::_load_fonts`; both specs | W, L | OFL-1.1; O1 | APPROVED |
| `FTHR_UI/assets/fthr_logo.ico` | ICO | `FTHR.spec` executable icon; `installer_windows.iss` setup icon | W Embedded | MIT; G1 | APPROVED |
| `FTHR_UI/assets/fthr_logo.png` | PNG | main window, splash and theme/customization fallback; both specs | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/clip.png` | PNG | clip navigation tab; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/close.png` | PNG | window close button; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/dropdown.png` | PNG | combo-box arrow in main UI and clip grid | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/home.png` | PNG | settings home/back button; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/maximize.png` | PNG | window maximize button; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/minimize.png` | PNG | window minimize button; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/pause.png` | PNG | clip viewer pause control | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/personalize.png` | PNG | customization navigation tab; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/play.png` | PNG | clip viewer play control | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/refresh.png` | PNG | clip/settings refresh controls; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/settings(general).png` | PNG | general settings navigation/gear | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/sound.png` | PNG | audio navigation tab; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/updates.png` | PNG | updates/performance navigation icon | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/icons/visuals.png` | PNG | visuals navigation tab; theme manager | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/sounds/clip_captured.wav` | PCM WAV | default clip notification | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/sounds/error.wav` | PCM WAV | default error notification | W, L | MIT; G1 | APPROVED |
| `FTHR_UI/assets/sounds/screenshot_saved.wav` | PCM WAV | default screenshot notification | W, L | MIT; G1 | APPROVED |
| `installer_assets/wizard_banner.bmp` | BMP | Inno Setup wizard image | W Embedded | MIT; G1 | APPROVED |
| `installer_assets/wizard_small.bmp` | BMP | Inno Setup small wizard image | W Embedded | MIT; G1 | APPROVED |

The two PyInstaller specs copy the approved `assets/` subtree. Linux also
copies the approved AppImage root icon. The Windows executable and installer
embed the approved ICO; the installer embeds the two approved BMP files.

## Repository and metadata investigation

All 25 predecessor media files first appear together in initial import commit
`96d2d0f87b2aae45ef43710516cd925912cb8701` on 2026-08-06. No earlier
repository commit, source URL, author statement, licence sidecar, design source,
or rights assignment was found. The image files contained no author, source,
copyright, or licence metadata. The commit author and age of the files were not
treated as evidence of authorship or redistribution rights.

The four predecessor MP3 files were assessed individually:

| Removed file | SHA-256 | Duration / encoding metadata | Provenance result |
|---|---|---|---|
| `clip_captured.mp3` | `b22a89ced3029bf26224531b8fd2e8a6618e0fb0c59ae6687c3956494238aeaf` | 1.097143 s; FFmpeg `Lavc58.18` / `Lavf58.12.100`; Android 15 tag | UNRESOLVED — no author/source/licence fields |
| `error.mp3` | `b0abd47b30cb761747740a63052ff3788022cf66233579c2d474df0dd56447e6` | 0.992653 s; same encoder family and Android tag | UNRESOLVED — no author/source/licence fields |
| `screenshot_saved.mp3` | `cfa621f3625bca42dd3b1948c45f893ca52f38c476ae9865475753441be963b1` | 0.966531 s; same encoder family and Android tag | UNRESOLVED — no author/source/licence fields |
| `startup.mp3` | `b22a89ced3029bf26224531b8fd2e8a6618e0fb0c59ae6687c3956494238aeaf` | byte-identical to `clip_captured.mp3` | UNRESOLVED — duplication is not rights evidence |

The predecessor image files were also reviewed individually. Each had the same
evidence result: no embedded author/copyright/licence/source metadata, no
licence sidecar or design source, and no history before the initial import.

| Predecessor image path | Visual role checked | Result / action |
|---|---|---|
| `.github/social_preview.png` | repository marketing image | UNRESOLVED; bytes replaced by G1 |
| `AppDir/fthr-clips.png` | Linux product icon / logo | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/fthr_logo.ico` | Windows product/installer icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/fthr_logo.png` | application logo / possible brand element | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/clip.png` | clip icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/close.png` | close icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/dropdown.png` | dropdown icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/home.png` | home icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/maximize.png` | maximize icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/minimize.png` | minimize icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/pause.png` | pause icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/personalize.png` | customization icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/play.png` | play icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/refresh.png` | refresh icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/settings(general).png` | settings icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/sound.png` | audio icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/updates.png` | update/performance icon | UNRESOLVED; bytes replaced by G1 |
| `FTHR_UI/assets/icons/visuals.png` | visuals icon | UNRESOLVED; bytes replaced by G1 |
| `installer_assets/wizard_banner.bmp` | installer artwork / logo | UNRESOLVED; bytes replaced by G1 |
| `installer_assets/wizard_small.bmp` | installer artwork / logo | UNRESOLVED; bytes replaced by G1 |

The predecessor logos, icons, social image, AppImage icon, and installer BMPs
were likewise **UNRESOLVED**: visual simplicity and repository history do not
prove creation, modification permission, trademark clearance, or redistribution
permission. They have all been replaced at their existing paths by G1 outputs,
so none of the unresolved bytes remains in the source inventory or release
allowlist. The four MP3 paths were deleted and application defaults now use the
four generated PCM WAV files.

The predecessor bytes remain reachable in the local Git history. They are not
present in either binary artifact or a source snapshot made with
`git archive HEAD`; a public full-history repository mirror must therefore not
be used as the alpha source deliverable unless the history is scrubbed or the
missing rights evidence is supplied. This audit deliberately did not rewrite
shared history without owner approval.

## Oswald verification and obligations

The exact upstream evidence is pinned in the manifest rather than relying on a
font family name:

- Source repository: <https://github.com/googlefonts/OswaldFont>
- Source revision: `89795261ac9eeb9aa8cd99f43982c4e4b0e53261`
- Exact font: <https://raw.githubusercontent.com/googlefonts/OswaldFont/89795261ac9eeb9aa8cd99f43982c4e4b0e53261/fonts/ttf/Oswald-Bold.ttf>
- Exact licence: <https://raw.githubusercontent.com/googlefonts/OswaldFont/89795261ac9eeb9aa8cd99f43982c4e4b0e53261/OFL.txt>

OFL 1.1 permits use, modification, bundling, and redistribution subject to its
conditions. The font is redistributed unmodified, not sold by itself, and the
copyright and full OFL text accompany both artifacts. No reserved font name is
declared in the pinned OFL file. FTHR does not claim the font under MIT.

## Enforcement

The release gate fails when an inventoried file changes hash, an asset lacks an
approved redistribution status/licence/copyright/origin/notice, an undocumented
media file enters a scanned source directory, an approved artifact asset is
missing, or any additional media file (including a legacy MP3) appears in a
Windows or Linux artifact. `tools/generate_release_assets.py --check` also
proves that every G1 byte still matches the reviewed generator.

The manifest intentionally does not change the UI/engine shared-memory IPC
contract, capture engine, or recovery policy.
