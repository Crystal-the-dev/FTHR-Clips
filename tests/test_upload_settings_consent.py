from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'FTHR_UI'))

from core.upload_manager import CATBOX_LEGAL_VERSION, LUSTFUL_LEGAL_VERSION
from core.uploader_bundle_manifest import (
    HARDWARE_POLICY_VERSION,
    UPLOADER_PRIVACY_VERSION,
    UPLOADER_TERMS_VERSION,
)
import ui.upload_settings_widget as upload_ui


class _Manager:
    def __init__(self):
        self.values = {
            'upload_provider': 'catbox',
            'upload_mode': 'manual',
        }
        self.uploader_installed = False
        self.hardware_installed = False
        self.provider_versions = {}
        self.uploader_activation = None
        self.hardware_activation = None

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save_settings(self):
        return True

    def is_enabled(self):
        return bool(self.values.get('upload_enabled') and self.uploader_installed)

    def is_plugin_installed(self):
        return self.uploader_installed

    def uploader_legal_text(self):
        return 'Uploader terms', 'Uploader privacy'

    def activate_plugin(self, terms, privacy):
        self.uploader_activation = (terms, privacy)
        self.uploader_installed = True
        self.values['upload_enabled'] = True
        return True, 'installed'

    def set_plugin_enabled(self, enabled):
        self.values['upload_enabled'] = bool(enabled)
        self.save_settings()
        return True, 'updated'

    def provider_consent_current(self, provider):
        expected = CATBOX_LEGAL_VERSION if provider == 'catbox' else LUSTFUL_LEGAL_VERSION
        return self.provider_versions.get(provider) == expected

    def record_provider_consent(self, provider, version):
        self.provider_versions[provider] = version
        return True

    def is_hardware_identity_installed(self):
        return self.hardware_installed

    def hardware_legal_text(self):
        return 'Hardware terms', 'Hardware privacy'

    def activate_hardware_identity(self, version):
        self.hardware_activation = version
        self.hardware_installed = True
        return True, 'installed'

    def local_account(self):
        return None


def test_enable_installs_uploader_only_after_both_legal_acceptances(
        qapp, monkeypatch):
    manager = _Manager()
    widget = upload_ui.UploadSettingsWidget(manager)
    install_calls = []
    monkeypatch.setattr(
        upload_ui,
        '_legal_install_dialog',
        lambda *args, **kwargs: install_calls.append(kwargs['title']) or True,
    )
    monkeypatch.setattr(upload_ui, '_provider_consent_dialog', lambda *_args: True)

    widget._on_enabled_changed(2)

    assert manager.uploader_activation == (
        UPLOADER_TERMS_VERSION, UPLOADER_PRIVACY_VERSION)
    assert manager.provider_versions['catbox'] == CATBOX_LEGAL_VERSION
    assert manager.hardware_activation is None
    assert install_calls == ['Install FTHR Upload Extension']
    assert not widget._body.isHidden()


def test_lustful_consent_then_installs_separate_hardware_capability(
        qapp, monkeypatch):
    manager = _Manager()
    manager.uploader_installed = True
    manager.values['upload_enabled'] = True
    widget = upload_ui.UploadSettingsWidget(manager)
    legal_calls = []
    install_calls = []
    monkeypatch.setattr(
        upload_ui,
        '_provider_consent_dialog',
        lambda _parent, provider: legal_calls.append(provider) or True,
    )
    monkeypatch.setattr(
        upload_ui,
        '_legal_install_dialog',
        lambda *args, **kwargs: install_calls.append(kwargs['title']) or True,
    )

    assert widget._ensure_provider_ready('lustful')

    assert legal_calls == ['lustful']
    assert manager.provider_versions['lustful'] == LUSTFUL_LEGAL_VERSION
    assert manager.hardware_activation == HARDWARE_POLICY_VERSION
    assert install_calls == ['Install Lustful Hardware Identity']


def test_disable_toggle_updates_manager_and_hides_settings(qapp):
    manager = _Manager()
    manager.uploader_installed = True
    manager.values['upload_enabled'] = True
    widget = upload_ui.UploadSettingsWidget(manager)

    widget.enable_check.click()

    assert manager.values['upload_enabled'] is False
    assert widget._body.isHidden()


def test_upload_toggle_is_saved_without_pressing_save_button(qapp):
    manager = _Manager()
    manager.uploader_installed = True
    manager.provider_versions['catbox'] = CATBOX_LEGAL_VERSION
    saves = []
    manager.save_settings = lambda: saves.append(True) or True
    widget = upload_ui.UploadSettingsWidget(manager)

    widget.enable_check.click()

    assert manager.values['upload_enabled'] is True
    assert len(saves) == 1
    assert not widget._body.isHidden()

    widget.enable_check.click()

    assert manager.values['upload_enabled'] is False
    assert len(saves) == 2
    assert widget._body.isHidden()


def test_failed_connection_test_emits_reason_for_bottom_error_bar(qapp):
    manager = _Manager()
    widget = upload_ui.UploadSettingsWidget(manager)
    failures = []
    widget.connection_failed.connect(failures.append)

    widget._on_test_done(False, 'Provider timed out')

    assert failures == ['Provider timed out']


def test_auto_compress_setting_is_saved_with_upload_settings(qapp):
    manager = _Manager()
    widget = upload_ui.UploadSettingsWidget(manager)

    widget.auto_compress_check.setChecked(True)
    widget._on_save()

    assert manager.values['upload_auto_compress'] is True


def test_donation_buttons_open_official_provider_pages(qapp, monkeypatch):
    manager = _Manager()
    opened = []
    monkeypatch.setattr(
        upload_ui.QDesktopServices,
        'openUrl',
        lambda url: opened.append(url.toString()) or True,
    )
    widget = upload_ui.UploadSettingsWidget(manager)

    widget.catbox_donate_btn.click()
    widget.lustful_donate_btn.click()

    assert opened == [upload_ui.CATBOX_SUPPORT_URL, upload_ui.LUSTFUL_DONATE_URL]


def test_only_selected_provider_support_bar_is_visible(qapp):
    manager = _Manager()
    widget = upload_ui.UploadSettingsWidget(manager)
    widget.show()

    assert not widget.catbox_donate_btn.isHidden()
    assert widget.lustful_donate_btn.isHidden()
    widget.provider_combo.setCurrentIndex(
        widget.provider_combo.findData('lustful'))

    assert widget.catbox_donate_btn.isHidden()
    assert not widget.lustful_donate_btn.isHidden()
    assert 'background: #f4d43a' in widget.lustful_donate_btn.styleSheet()
