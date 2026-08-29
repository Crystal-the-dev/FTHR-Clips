from __future__ import annotations

import pytest

from core.theme_manager import ThemeManager
from ui.customize_page import CustomizePage
from ui.style import Colors, Fonts


@pytest.fixture(autouse=True)
def _reset_theme_singleton():
    ThemeManager._instance = None
    yield
    ThemeManager._instance = None
    Fonts.configure('Oswald', 'Oswald')


def test_theme_export_import_round_trips_fonts(monkeypatch, tmp_path):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    theme = ThemeManager()
    source = tmp_path / 'MyFont.ttf'
    source.write_bytes(b'test font payload')
    theme.set_custom_font(['My Font'], source)
    theme.set_font('display', 'My Font')
    theme.set_font('body', 'Oswald')
    theme.save()
    archive = tmp_path / 'theme.zip'

    assert theme.export_theme(archive)
    theme.reset_fonts()
    theme.save()
    assert theme.import_theme(archive)
    assert theme.get_fonts() == {
        'display': 'My Font',
        'body': 'Oswald',
    }
    assert theme.get_custom_font_paths()['My Font'].read_bytes() == b'test font payload'


def test_customize_page_only_exposes_oswald_until_fonts_are_imported(
        qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    ThemeManager._instance = None
    page = CustomizePage()
    qtbot.addWidget(page)

    assert set(page._font_combos) == {'display', 'body'}
    for combo in page._font_combos.values():
        assert [combo.itemData(i) for i in range(combo.count())] == ['Oswald']


def test_customize_font_dropdowns_use_a_raised_surface(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    ThemeManager._instance = None
    page = CustomizePage()
    qtbot.addWidget(page)

    for combo in page._font_combos.values():
        assert f'background-color: {Colors.SURFACE_3}' in combo.styleSheet()
