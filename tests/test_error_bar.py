import sys
import os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest


@pytest.fixture
def error_bar(qtbot):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    qtbot.addWidget(bar)
    return bar


def test_hidden_on_init(error_bar):
    assert not error_bar.isVisible()


def test_visible_after_push(error_bar):
    error_bar.push('TEST TITLE', 'Some detail.')
    assert error_bar.isVisible()


def test_dismiss_hides_when_queue_empty(error_bar):
    error_bar.push('ONLY', 'One error.')
    error_bar.dismiss_current()
    assert not error_bar.isVisible()


def test_dismiss_shows_next_when_queued(error_bar):
    error_bar.push('FIRST', 'First error.')
    error_bar.push('SECOND', 'Second error.')
    error_bar.dismiss_current()
    assert error_bar.isVisible()


def test_clear_hides_bar(error_bar):
    error_bar.push('A', 'Err A.')
    error_bar.push('B', 'Err B.')
    error_bar.clear()
    assert not error_bar.isVisible()


def test_action_fires_callback_and_dismisses(error_bar):
    fired = []
    error_bar.push('TITLE', 'Detail.',
                   actions=[('DO IT', lambda: fired.append(True))])
    error_bar._fire_action(lambda: fired.append(True))
    assert fired == [True]
    assert not error_bar.isVisible()


def test_action_fires_only_the_given_callback(error_bar):
    log = []
    error_bar.push('TITLE', 'Detail.',
                   actions=[('ACT', lambda: log.append('cb'))])
    error_bar._fire_action(lambda: log.append('direct'))
    assert 'direct' in log
    assert not error_bar.isVisible()
