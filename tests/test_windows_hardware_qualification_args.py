from argparse import Namespace
from pathlib import Path

from tools.qualify_windows_hardware import build_engine_command


def _args(*, separate_audio: bool) -> Namespace:
    return Namespace(
        engine=Path('FTHRclips.exe'), fps=60, width=0, height=0,
        bitrate=16000, scaling_mode='stretch', audio=True,
        separate_audio=separate_audio,
    )


def test_engine_command_preserves_crop_slots_and_separate_audio_position():
    command = build_engine_command(
        _args(separate_audio=True), 'h264', 'monitor-path')

    assert len(command) == 24
    assert command[18:23] == ['0', '0', '0', '1', '1']
    assert command[23] == '1'


def test_engine_command_can_disable_separate_audio():
    command = build_engine_command(
        _args(separate_audio=False), 'h264', 'monitor-path')

    assert command[23] == '0'
