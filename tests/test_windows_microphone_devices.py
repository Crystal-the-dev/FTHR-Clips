from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.windows_microphone_devices import (
    list_native_microphones,
    migrate_legacy_microphone_name,
    parse_microphone_inventory,
)


def _inventory() -> str:
    return '''{"schema_version":1,"microphones":[
        {"endpoint_id":"default-id","display_name":"FIFINE","device_state":1,"is_default":true},
        {"endpoint_id":"disabled-id","display_name":"FIFINE","device_state":2,"is_default":false},
        {"endpoint_id":"camo-id","display_name":"Camo","device_state":1,"is_default":false}
    ]}'''


def test_native_inventory_preserves_stable_id_and_state():
    endpoints = parse_microphone_inventory(_inventory())

    assert [(item.endpoint_id, item.is_active, item.is_default) for item in endpoints] == [
        ('default-id', True, True),
        ('disabled-id', False, False),
        ('camo-id', True, False),
    ]


def test_legacy_name_migrates_only_when_one_active_native_endpoint_matches():
    endpoints = parse_microphone_inventory(_inventory())

    assert migrate_legacy_microphone_name('Camo', endpoints) == 'camo-id'
    assert migrate_legacy_microphone_name('FIFINE', endpoints) == 'default-id'
    assert migrate_legacy_microphone_name('Missing', endpoints) is None


def test_duplicate_active_legacy_names_do_not_bind_to_a_random_device():
    endpoints = parse_microphone_inventory(
        '''{"schema_version":1,"microphones":[
            {"endpoint_id":"one","display_name":"Same","device_state":1,"is_default":false},
            {"endpoint_id":"two","display_name":"Same","device_state":1,"is_default":false}
        ]}''')

    assert migrate_legacy_microphone_name('Same', endpoints) is None


def test_engine_inventory_is_invoked_with_a_bounded_helper_mode(tmp_path):
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=_inventory(), stderr='')

    endpoints = list_native_microphones(tmp_path / 'FTHRclips.exe', runner=runner)

    assert endpoints[0].endpoint_id == 'default-id'
    assert calls[0][0][-1] == '--list-microphones'
    assert calls[0][1]['timeout'] == 5


def test_invalid_native_inventory_is_rejected():
    with pytest.raises(ValueError, match='valid JSON'):
        parse_microphone_inventory('not json')
