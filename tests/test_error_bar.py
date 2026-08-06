import sys
import os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtWidgets import QApplication

@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_hidden_on_init(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    assert not bar.isVisible()


def test_visible_after_push(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    bar.push('TEST TITLE', 'Some detail.')
    assert bar.isVisible()


def test_dismiss_hides_when_queue_empty(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    bar.push('ONLY', 'One error.')
    bar.dismiss_current()
    assert not bar.isVisible()


def test_dismiss_shows_next_when_queued(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    bar.push('FIRST', 'First error.')
    bar.push('SECOND', 'Second error.')
    bar.dismiss_current()
    assert bar.isVisible()


def test_clear_hides_bar(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    bar.push('A', 'Err A.')
    bar.push('B', 'Err B.')
    bar.clear()
    assert not bar.isVisible()


def test_action_fires_callback_and_dismisses(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    fired = []
    bar.push('TITLE', 'Detail.', actions=[('DO IT', lambda: fired.append(True))])
    bar._fire_action(lambda: fired.append(True))
    assert fired == [True]
    assert not bar.isVisible()


def test_action_fires_only_the_given_callback(qapp):
    from ui.error_bar import ErrorBar
    bar = ErrorBar()
    log = []
    bar.push('TITLE', 'Detail.', actions=[('ACT', lambda: log.append('cb'))])
    bar._fire_action(lambda: log.append('direct'))
    assert 'direct' in log
    assert not bar.isVisible()
