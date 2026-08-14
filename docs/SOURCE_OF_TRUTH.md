# Source of Truth

Read this before editing anything. Several stale copies of FTHR Clips exist on
the development machine and at least one of them describes a directory layout
the project no longer uses. Editing the wrong one wastes an afternoon and, if
it gets built, ships the wrong code.

## The authoritative tree

```
C:\Users\Tom\Desktop\FTHR_Clips_source\FTHR_Clips
```

This is the git repository. **If it is not in git, it is not the project.**

Everything about a release — what shipped, when, and from which source — is
answered by `git log` and the tags in this repository. Nothing else counts as
evidence.

## Copies that are NOT authoritative

None of these are touched by tooling in this repo, and none of them should be
edited, built from, or copied back over the tree above.

| Path | What it is | What to do with it |
|---|---|---|
| `Desktop\clipping\` | Abandoned C++ stub from 05/2026 | Archive or delete |
| `Desktop\FTHR_CLIPS_BACKUP(1)\` | Ad-hoc folder backup | Archive or delete |
| `Desktop\FTHR_Clips\` | Empty directory | Delete |
| `Desktop\CLAUDE.md` | Notes describing an `engine/` + `ui/` layout that no longer exists, and shared memory `_v1` (current is `_v4`) | **Delete or replace** — see below |

### Recommended manual cleanup

These commands are **not** run by any script here. Review, then run them
yourself. Nothing in this repository depends on any of these paths.

```powershell
# 1. Park the stale copies somewhere unambiguous, dated, and out of Desktop.
$archive = "$HOME\Archive\FTHR_stale_2026-08-06"
New-Item -ItemType Directory -Force $archive

Move-Item "$HOME\Desktop\clipping"              $archive
Move-Item "$HOME\Desktop\FTHR_CLIPS_BACKUP(1)"  $archive
Move-Item "$HOME\Desktop\CLAUDE.md"             $archive

# 2. The empty one can just go.
Remove-Item "$HOME\Desktop\FTHR_Clips" -Recurse -Force

# 3. Optional: once you trust git, the pre-AUDIT-005 GPL build backup inside
#    the repo can be moved out too. It is git-ignored, so it is invisible to
#    the repo either way — but it contains GPL FFmpeg DLLs and must never be
#    shipped or confused with dist/.
Move-Item "$HOME\Desktop\FTHR_Clips_source\FTHR_Clips\dist_old_gpl_2026-08-05" $archive
```

`Desktop\CLAUDE.md` specifically: do not try to update it in place. It documents
a structure two refactors old. Replace it with a pointer:

```markdown
# FTHR Clips

Moved. The project lives at
`C:\Users\Tom\Desktop\FTHR_Clips_source\FTHR_Clips` and is a git repository.
Read `CONTRIBUTING.md` and `SOURCE_OF_TRUTH.md` there. This file is stale and
describes an `engine/` + `ui/` layout that no longer exists.
```

## How backups work from now on

The old model was "copy the folder to the Desktop and rename it". That is what
created the ambiguity this file exists to resolve. It is no longer how backups
are made.

**A backup is a git clone or a git bundle. Never a working directory.**

```bash
# Full mirror, including all branches and tags — the good option.
git clone --mirror . D:\Backups\FTHR-Clips-mirror.git

# Refresh it later
cd D:\Backups\FTHR-Clips-mirror.git && git remote update

# Single-file snapshot, easy to copy to an external disk or the server.
git bundle create D:\Backups\FTHR-Clips-2026-08-06.bundle --all
```

Rules:

1. **A backup is never opened as a working directory.** If you need to inspect
   one, clone *out* of it into a temporary path, look, then delete the clone.
2. **A backup is never edited.** Changes go into the authoritative tree and are
   committed there.
3. **A backup is never the thing you build from.** Releases are built from a
   tagged commit in the authoritative repository.
4. Bare mirrors (`.git` suffix) are preferred precisely because you *cannot*
   accidentally start editing them.

Once this repository has a remote, the remote is the primary backup and the
local mirror becomes a secondary.

## How releases are identified

A release is a **git tag**, not a file on a Desktop.

- Tag format: `v<version>` — the alpha is `v1.0.0-alpha`.
- The tag points at the exact commit the artefacts were built from.
- The product version comes from `FTHR_UI/version.py` and nowhere else;
  `tools/verify_version_consistency.py` fails the build if any other file
  disagrees.
- A tag is only created once every gate in `RELEASE_CHECKLIST.md` passes.
  Tags are cheap but they are also a claim; an untested tag is a lie that
  outlives the person who made it.

To find out what a downloaded build actually is:

- Windows: right-click `FTHRClips.exe` → Properties → Details → Product version
- Linux: the AppImage filename carries the version
- Either: Settings → Version & Updates shows version and build date
- Either: the first line of `~/.fthr/logs/fthr.log` carries the version

## Current status

| Item | State |
|---|---|
| Git repository | Yes — initialised 2026-08-06, honest import of the existing alpha source |
| Tag `v1.0.0-alpha` | **Not created.** Release gates are not met — see `RELEASE_CHECKLIST.md` |
| Outstanding release blocker | AUDIT-013: PySide6/LGPL artifact path is gated; owner must prove or replace the bundled logos/icons/sounds/font |
