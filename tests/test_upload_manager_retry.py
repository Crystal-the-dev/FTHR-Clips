import sys, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from unittest.mock import patch, MagicMock


def test_upload_error_signal_has_four_args(qapp):
    """upload_error must carry (title, detail, level, clip_path)."""
    from core.upload_manager import UploadManager
    sm = MagicMock()
    sm.get.return_value = False
    um = UploadManager(sm)

    received = []
    um.upload_error.connect(lambda t, d, l, p: received.append((t, d, l, p)))

    # Simulate UPLOAD NOT CONFIGURED (no path)
    assert um._do_single_upload.__func__ is not None  # the method exists
    with patch.object(um, '_http_post', return_value=200):
        pass  # we test signal shape via direct emit
    um.upload_error.emit('TEST', 'detail', 'warning', '')
    assert received == [('TEST', 'detail', 'warning', '')]


def test_upload_failed_emits_path(qapp, tmp_path):
    """All-retries-exhausted path must emit clip_path in upload_error."""
    from core.upload_manager import UploadManager
    sm = MagicMock()
    sm.get.side_effect = lambda key, default=None: {
        'upload_enabled': True,
        'upload_server_url': 'http://localhost:9',
        'upload_auth_header': '',
        'upload_auto_delete': False,
    }.get(key, default)
    um = UploadManager(sm)

    clip = tmp_path / 'test_clip.mp4'
    clip.write_bytes(b'\x00' * 64)

    received = []
    um.upload_error.connect(lambda t, d, l, p: received.append((t, d, l, p)))

    # _RETRY_DELAYS is (5, 15, 45) — patch time.sleep + _http_post to fail fast
    with patch('core.upload_manager.time.sleep'), \
         patch.object(um, '_http_post', side_effect=OSError('refused')):
        um._do_single_upload(str(clip))

    failed = [r for r in received if r[0] == 'UPLOAD FAILED']
    assert failed, 'UPLOAD FAILED not emitted'
    assert failed[0][3] == str(clip), 'clip_path missing from signal'


def test_partial_file_is_rejected_by_all_upload_entrypoints(qapp, tmp_path):
    from core.upload_manager import UploadManager
    sm = MagicMock()
    sm.get.side_effect = lambda key, default=None: {
        'upload_enabled': True,
        'upload_mode': 'immediate',
        'upload_server_url': 'https://example.invalid/upload',
    }.get(key, default)
    um = UploadManager(sm)
    partial = tmp_path / 'clip.mp4.partial'
    partial.write_bytes(b'partial')

    ready = um.notify_clip_saved(str(partial))
    um.enqueue_upload(str(partial))
    success, message = um._do_single_upload(str(partial))

    assert ready.is_set()
    assert um._queue.empty()
    assert not success
    assert 'Incomplete clip' in message


def test_interval_scan_does_not_enqueue_partial_files(qapp, tmp_path, monkeypatch):
    import core.upload_manager as upload_module
    sm = MagicMock()
    sm.get.side_effect = lambda key, default=None: {
        'upload_enabled': True,
        'upload_mode': 'interval',
    }.get(key, default)
    um = upload_module.UploadManager(sm)
    partial = tmp_path / 'desktop_clip_from_11Aug2026_12-00-00.mp4.partial'
    partial.write_bytes(b'partial')
    monkeypatch.setattr(upload_module, '_CLIPS_DIR', tmp_path)

    um._interval_scan()

    assert um._queue.empty()
