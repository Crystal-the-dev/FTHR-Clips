# version.py — the single source of truth for the FTHR Clips product version.
#
# AUDIT-008: the version used to be hardcoded in five unrelated places
# (window title, About label, installer script, AppImage filename, docs) and
# they had already drifted apart once. Everything that needs to state a
# product version now derives it from here.
#
# Consumers:
#   FTHR_UI/main.py            -> QApplication.setApplicationVersion + title
#   FTHR.spec / FTHR_linux.spec-> PyInstaller bundle + Windows VERSIONINFO
#   installer_windows.iss      -> MyAppVersion (checked by the verifier)
#   build_linux.sh             -> AppImage filename
#   tools/verify_version_consistency.py -> the gate that keeps them aligned
#
# What this file is NOT:
#   It is not the shared-memory contract version. The IPC layout is versioned
#   independently through CaptureBridge.SHARED_MEM_NAME ('FTHR_SharedMemory_v3')
#   and must only change when the struct layout itself changes. Do not bump it
#   because the product version moved.

from __future__ import annotations

# PEP 440 / SemVer pre-release string. Keep the two in sync when releasing.
__version__ = '1.0.0-alpha'

# Numeric (major, minor, patch) triple. Windows VERSIONINFO resources cannot
# express a pre-release suffix, so the packaging code uses this and records the
# full string in the FileVersion / ProductVersion text fields.
VERSION_INFO = (1, 0, 0)

PRERELEASE = 'alpha'

# Date the current source state was cut for release, ISO 8601. Shown in the
# About page. Bump it together with __version__ — it used to be a separate
# hardcoded literal in main.py and had drifted four months out of date.
BUILD_DATE = '2026-08-06'

# Effective licence of a *distributed build*, as opposed to the licence of
# FTHR's own source (MIT, see LICENSE). PyQt6 is GPL-3.0-only, so any bundle
# containing it is GPLv3 as a whole — AUDIT-013. The UI and the release
# paperwork must say this; advertising a download as "MIT" would be false.
SOURCE_LICENSE = 'MIT'
DISTRIBUTION_LICENSE = 'GPL-3.0-only'

APP_NAME = 'FTHR Clips'
APP_ID = 'FTHRClips'
PUBLISHER = 'FTHR Community'


def version_string() -> str:
    """The canonical human-facing version, e.g. '1.0.0-alpha'."""
    return __version__


def windows_file_version() -> tuple[int, int, int, int]:
    """4-tuple for the Windows VS_FIXEDFILEINFO block.

    The fourth field is the build number; alpha builds pin it to 0 so two
    builds of the same source produce byte-identical version resources.
    """
    return (*VERSION_INFO, 0)
