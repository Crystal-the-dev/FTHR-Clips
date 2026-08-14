"""Release-gate tests for the AUDIT-013 asset provenance allowlist."""

import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

import verify_release_licenses as vrl  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path: Path, *, status: str = 'approved') -> tuple[Path, bytes]:
    content = b'approved image bytes'
    asset = tmp_path / 'assets' / 'logo.png'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(content)
    notice = tmp_path / 'licenses' / 'asset-notice.txt'
    notice.parent.mkdir(parents=True)
    notice.write_text('fixture notice\n', encoding='utf-8')
    manifest = {
        'schema_version': 1,
        'allowed_redistribution_status': 'approved',
        'scanned_source_roots': ['assets'],
        'asset_extensions': ['.mp3', '.png'],
        'origins': {
            'fixture': {
                'type': 'project-generated',
                'notice': 'licenses/asset-notice.txt',
            },
        },
        'assets': [{
            'path': 'assets/logo.png',
            'sha256': _sha(content),
            'media_type': 'image/png',
            'usage': ['fixture'],
            'distributions': ['windows'],
            'artifact_paths': {
                'windows': ['_internal/assets/logo.png'],
            },
            'license': 'MIT',
            'copyright': 'Copyright fixture',
            'origin': 'fixture',
            'redistribution_status': status,
        }],
    }
    manifest_path = tmp_path / 'tools' / 'release_asset_manifest.json'
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    return tmp_path, content


def test_real_asset_manifest_and_generated_outputs_are_current():
    rep = vrl.Report()
    vrl.check_asset_manifest(ROOT, rep)
    assert not rep.failures, rep.failures
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'generate_release_assets.py'),
         '--check'],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_bundled_sounds_are_generated_pcm_wav_files():
    sound_dir = ROOT / 'FTHR_UI' / 'assets' / 'sounds'
    expected_names = {
        'clip_captured.wav',
        'error.wav',
        'screenshot_saved.wav',
        'startup.wav',
    }
    assert {path.name for path in sound_dir.iterdir()} == expected_names

    hashes = set()
    for name in expected_names:
        sound = sound_dir / name
        hashes.add(_sha(sound.read_bytes()))
        with wave.open(str(sound), 'rb') as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == 44_100
            assert audio.getnframes() > 0

    assert len(hashes) == len(expected_names)


def test_asset_manifest_rejects_hash_drift(tmp_path):
    root, _ = _fixture(tmp_path)
    (root / 'assets' / 'logo.png').write_bytes(b'modified')
    rep = vrl.Report()
    vrl.check_asset_manifest(root, rep)
    assert any('SHA-256 differs' in failure for failure in rep.failures)


def test_asset_manifest_rejects_undocumented_file(tmp_path):
    root, _ = _fixture(tmp_path)
    (root / 'assets' / 'unknown.mp3').write_bytes(b'unknown sample')
    rep = vrl.Report()
    vrl.check_asset_manifest(root, rep)
    assert any('undocumented assets' in failure for failure in rep.failures)


def test_asset_manifest_rejects_unresolved_status(tmp_path):
    root, _ = _fixture(tmp_path, status='UNRESOLVED')
    rep = vrl.Report()
    vrl.check_asset_manifest(root, rep)
    assert any('redistribution status' in failure for failure in rep.failures)


def test_asset_artifact_accepts_exact_allowlist(tmp_path):
    source, content = _fixture(tmp_path / 'source')
    artifact = tmp_path / 'artifact'
    path = artifact / '_internal' / 'assets' / 'logo.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    rep = vrl.Report()
    vrl.check_asset_artifact(
        artifact, rep, 'windows', manifest_root=source)
    assert not rep.failures, rep.failures


def test_asset_artifact_rejects_unapproved_extra_file(tmp_path):
    source, content = _fixture(tmp_path / 'source')
    artifact = tmp_path / 'artifact'
    path = artifact / '_internal' / 'assets' / 'logo.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    (path.parent / 'legacy.mp3').write_bytes(b'unknown')
    rep = vrl.Report()
    vrl.check_asset_artifact(
        artifact, rep, 'windows', manifest_root=source)
    assert any('unapproved assets' in failure for failure in rep.failures)
