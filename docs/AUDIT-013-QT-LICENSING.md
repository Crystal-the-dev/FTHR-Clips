# AUDIT-013 — Qt / Python UI licensing

Date: **2026-08-14**

Status: **DECISION REQUIRED**

Technical Qt closure: **complete**. Public-alpha legal approval: **blocked on
project-asset provenance/permission evidence from the owner**.

This is an engineering compliance review, not legal advice. Release-readiness
claims below are limited to facts reproducible from source, official package
metadata, and inspected artifacts.

## 1. Current project licence and ownership evidence

`LICENSE` is the canonical project licence. It applies the OSI-approved MIT
text to FTHR Clips source attributed to “FTHR-Community”; `README.md` and
`THIRD_PARTY_NOTICES.md` now consistently distinguish MIT application source
from separately licensed runtime components. Primary source: [MIT
License](https://opensource.org/license/mit), Open Source Initiative, accessed
2026-08-14.

Git history contains one author identity, but no CLA, copyright assignment,
contributor certificate, or explicit relicensing authority. That is not proof
that the author owns every contribution. This change therefore preserves MIT
for FTHR code and removes the GPL-only binding; it does not relicense the
project. The tree contains copied FFmpeg/NVIDIA/Wayland material with upstream
notices. It also ships logos/icons, four MP3 sounds, and `Oswald-Bold.ttf`
without repository provenance or licence evidence; section 14 records the
required owner decision.

## 2. Previous binding and reproducible baseline

The prior release lock selected `PyQt6==6.11.0`, `PyQt6-Qt6==6.11.1`, and
`PyQt6_sip==13.11.1`. Before migration, 16 production files contained 60
PyQt6 import sites; 85 `pyqtSignal`/`pyqtSlot`/`pyqtProperty` sites occurred
across 12 files, and five tests imported PyQt6. There were no `.ui` files or
generated UI Python files.

The prior 514.7 MiB Windows artifact contained PyQt extension modules for Core,
GUI, Widgets, Network, Multimedia, and Multimedia Widgets, plus SVG/PDF Qt
runtime libraries. It contained PyQt6 GPL paperwork and was not an approved
MIT-plus-LGPL release artifact.

## 3. Exact PyQt6 licence

- Riverbank states that PyQt is dual-licensed under GPLv3 and a commercial
  licence and is not offered under LGPL: [PyQt licensing](https://riverbankcomputing.com/software/pyqt),
  Riverbank Computing, accessed 2026-08-14.
- Riverbank states that a commercial PyQt licence is required for a
  GPL-incompatible distribution, is per-developer, and does not establish a Qt
  commercial entitlement: [Commercial License FAQ](https://riverbankcomputing.com/commercial/license-faq),
  Riverbank Computing, accessed 2026-08-14.
- The exact prior wheel records `GPL-3.0-only` and describes the PyPI download
  as the GPL version: [PyQt6 6.11.0](https://pypi.org/project/PyQt6/6.11.0/),
  Python Package Index/Riverbank metadata, accessed 2026-08-14.

No PyQt or Qt commercial entitlement is evidenced in the repository.
PyInstaller bundling redistributes rather than changes the binding licence.

## 4. Selected binding and Qt licences

The selected replacement is **PySide6 6.11.1**. A clean CPython 3.14.3
environment resolved only `PySide6`, `PySide6-Essentials`, `PySide6-Addons`,
and `shiboken6`, all 6.11.1, and imported every FTHR-required Qt module.

- Qt identifies PySide6 as its official Python binding and offers it under
  LGPLv3/GPLv3/commercial terms: [Qt for Python](https://doc.qt.io/qtforpython-6/),
  The Qt Company, accessed 2026-08-14.
- Exact wheel metadata offers `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`:
  [PySide6 6.11.1](https://pypi.org/project/PySide6/6.11.1/), Qt Project/Python
  Package Index, accessed 2026-08-14.
- Qt documents dynamic linking/replacement, notices, reverse-engineering
  permission, and corresponding-source delivery/offer considerations for LGPL
  distribution: [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations),
  The Qt Company, accessed 2026-08-14.

FTHR selects LGPLv3 for unmodified, separately loaded PySide/Shiboken modules
and Qt shared libraries. Exact Qt 6.11.1 and Qt for Python 6.11.1 source archive
URLs are shipped in `licenses/Qt6-SOURCE.txt`; replacement or reverse
engineering for debugging modified LGPL components is not prohibited.

## 5. Actual Qt runtime module inventory

Production source directly imports Core, GUI, Widgets, Multimedia, and
Multimedia Widgets. The final artifact allowlists are based on the concrete
PyInstaller outputs:

| Platform | Bundled Qt libraries |
|---|---|
| Windows | Core, GUI, Multimedia, Multimedia Widgets, Network, OpenGL, PDF, SVG, Widgets |
| Linux | Concurrent, Core, DBus, EGLFS Device Integration, EGLFS KMS Support, GUI, Multimedia, Multimedia Widgets, Network, OpenGL, PDF, SVG, Wayland Client, Widgets, WlShell Integration, XcbQpa |

Official module licensing evidence: [Qt licensing](https://doc.qt.io/qt-6/licensing.html),
[Core](https://doc.qt.io/qt-6/qtcore-index.html), [GUI](https://doc.qt.io/qt-6/qtgui-index.html),
[Widgets](https://doc.qt.io/qt-6/qtwidgets-index.html), [Multimedia](https://doc.qt.io/qt-6/qtmultimedia-index.html),
[Multimedia Widgets](https://doc.qt.io/qt-6/qtmultimediawidgets-index.html),
[SVG](https://doc.qt.io/qt-6/qtsvg-index.html), [PDF](https://doc.qt.io/qt-6/qtpdf-index.html),
[Concurrent](https://doc.qt.io/qt-6/qtconcurrent-index.html), [Wayland
Client](https://doc.qt.io/qt-6/qtwaylandclient-index.html), and [EGLFS/Qt
GUI platform integration](https://doc.qt.io/qt-6/embedded-linux.html), The Qt
Company, Qt 6.11, accessed 2026-08-14. Qt's component-level third-party list is
[Licenses Used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html), The Qt
Company, accessed 2026-08-14.

The inspected artifacts contain no reviewed GPL-only Qt module. QML, Quick,
VirtualKeyboard, Graphs, HTTP Server, Lottie, Network Authorization, QML
Compiler, Quick 3D, and Wayland Compositor are unnecessary and denied by the
machine-readable runtime manifest. PyInstaller initially collected unused
QML/Quick/VirtualKeyboard libraries through its QtGui hook; the final TOC
filter removes them and the artifact gate proves their absence.

## 6. Options evaluated and decision

| Option | Evidence and consequence | Decision |
|---|---|---|
| A — PyQt6/GPLv3 | Requires deliberate GPL-compatible distribution and sufficient ownership authority; repository evidence does not establish that authority and the documented goal is MIT application source. | Rejected |
| B — commercial PyQt6 | Riverbank offers per-developer licences, but no entitlement exists in the repository; private licence/package governance would enter CI and packaging. | Unavailable without owner purchase/evidence |
| C — PySide6/LGPLv3 | Exact binding and required Qt modules offer an LGPL option; separate shared libraries allow FTHR application source to remain MIT when concrete LGPL obligations are met. | **Selected and implemented** |

This is the smallest evidenced strategy preserving the project's current MIT
grant without relying on an unowned commercial entitlement or project-wide GPL
decision.

## 7. Source migration

Sixteen production files and the five pre-existing Qt-coupled tests now import
PySide6; PyQt declarations were individually converted to `Signal`. Layouts,
styling, controls, dialogs, timers, threads, multimedia use, clip library, and
upload behavior were not redesigned. PySide6 is the sole binding; no broad
compatibility layer was added. One binding-specific behavior was made explicit:
the microphone combo signal is connected once rather than relying on PyQt's
silent disconnect behavior.

New smoke coverage constructs the real main-window widget tree with external
capture/audio/subprocess effects stubbed, verifies a dialog signal, and proves
queued signal delivery across a real `QThread`.

## 8. Dependency and packaging changes

`requirements-alpha.txt` pins all four Qt for Python packages exactly to 6.11.1
and contains no PyQt package. Both PyInstaller specs use PySide hooks, exclude
the old binding/reviewed GPL-only modules, and apply platform-specific Qt
runtime allowlists. Linux resolves plugins from the PySide wheel rather than a
host Qt installation and contains both XCB (`libqxcb.so`) and Wayland
(`libqwayland.so`) platform plugins.

No C++ capture source, shared-memory layout, command/response contract, replay
timing, recovery policy, or transactional-save implementation changed.

## 9. Licence files and notices

Each final artifact contains 15 files under `licenses/`, covering Qt/PySide6
LGPL and exact sources, Qt third-party code, FFmpeg, OpenH264, OpenCV, NumPy,
sounddevice, keyboard, cffi, pycparser, NVIDIA's source header, and generated
Wayland protocol material. `LICENSE` and `THIRD_PARTY_NOTICES.md` are also
included. The official OpenH264 licence source is [Cisco OpenH264
LICENSE](https://raw.githubusercontent.com/cisco/openh264/master/LICENSE),
accessed 2026-08-14.

## 10. Automated gates

`tools/verify_release_licenses.py` now rejects production PyQt6 imports,
inexact/unapproved release locks, missing PySide6, both bindings in an artifact,
reviewed GPL-only or unexpected Qt libraries, missing directly used modules or
platform plugins, obsolete PyQt notices, and missing licence files. Tests cover
positive Windows/Linux layouts and deliberate negative fixtures.

The final Windows onedir passed **60/60** artifact checks. The final Linux
AppDir and independently extracted AppImage each passed **109/109** checks.
The source-tree gate also detects a pre-existing ignored stale Linux engine in
this worktree whose linkage is invalid; the clean Linux engine and artifacts
pass the RPATH/DT_NEEDED checks. Release automation must build from a clean
checkout and gate the produced artifact.

## 11. Windows validation

Fresh CPython **3.14.3**, binding-only environment:

- `pip check`: pass; Ruff: pass.
- pytest: **378 passed, 30 platform skips, 0 failed**.
- Real `QApplication`/main-window/thread/dialog PySide6 smoke tests: pass.
- Clean onedir: **546,312,457 bytes**; 60/60 licence/runtime gates pass.
- Packaged `--card-process` startup: exit code 0.

No physical Windows capture/save or interactive desktop/tray test was run for
this licensing task, so those are not claimed.

## 12. Linux validation

Ubuntu 24.04/WSL2, Python **3.12.3**, binding-only environment:

- CTest: **10/10 passed**, including replay/duration, recovery,
  transactional-save, bounded Wayland dispatch, timestamps, and real short-MP4
  integration.
- Python: **406 passed, 1 skipped, 1 failed**. The sole failure is the existing
  no-display lifecycle test: after reporting no backend and exhausting three
  recovery attempts, the unchanged C++ engine does not exit within eight
  seconds. AUDIT-013 changes no C++/capture/recovery/IPC code.
- Final AppImage: **215,524,544 bytes**; SHA-256
  `e16966c6429ec2ef612d7cf54a917300fd0bf582da07931412e94c36c4a1a6e8`.
- AppDir and independently extracted AppImage: 109/109 gates pass.
- Packaged offscreen `--card-process`: exit code 0; WSL's missing PipeWire
  library produced a non-fatal warning.

This headless WSL evidence validates packaging, not real X11/Wayland capture.

## 13. Performance validation

Five same-host Windows runs used a minimal real `QWidget`, cold imports through
FTHR application/widget modules, a two-second idle period, and a 10 ms timer:

| Metric (median) | PyQt6 | PySide6 | Delta |
|---|---:|---:|---:|
| Startup | 67.1 ms | 172.3 ms | +105.2 ms |
| RSS | 31.4 MiB | 38.0 MiB | +6.6 MiB |
| Idle CPU over 2 s | 0 ms | 0 ms | no measured change |
| 10 ms timer firings | 199 | 200 | no material change |

This scoped microbenchmark is not a full packaged-UI or capture-throughput
benchmark. Native capture/save timing tests remain green; no capture-FPS claim
is inferred.

## 14. Unresolved legal questions

Repository evidence does not prove authorship, redistribution permission, or
licence terms for the bundled FTHR logos/icons, four MP3 notification sounds,
or `Oswald-Bold.ttf`. It also does not provide general relicensing authority
beyond the current MIT file. The owner must supply auditable provenance and
licence/redistribution evidence for these assets, or replace/remove them.

## 15. Final release recommendation

Keep the completed PySide6 6.11.1 migration and dynamic LGPL packaging. The Qt
binding blocker is technically closed, but **do not publish a public alpha
yet**. AUDIT-013 remains **DECISION REQUIRED**, not RESOLVED, until the owner
closes the asset-provenance decision in section 14. AUDIT-044/AUDIT-045 X11
worker architecture remains separate and was not implemented here.
