"""The exception-handling gate must be able to fail (AUDIT-007).

Same reasoning as the engine contract gate: this one guards a property nobody
can see at runtime — that a failure said something before it disappeared. A
gate that cannot go red is not evidence of anything.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

from verify_exception_handling import BASELINE, check_file, main  # noqa: E402


def _write(tmp_path: Path, body: str, name: str = 'fixture.py') -> Path:
    p = tmp_path / name
    p.write_text(body, encoding='utf-8')
    return p


def _rules(findings):
    return {f.rule for f in findings}


# ---------------------------------------------------------------------------
# Must reject
# ---------------------------------------------------------------------------

def test_rejects_bare_except(tmp_path):
    src = _write(tmp_path, 'try:\n    f()\nexcept:\n    pass\n')
    assert 'BARE-EXCEPT' in _rules(check_file(src))


def test_rejects_base_exception(tmp_path):
    src = _write(tmp_path, 'try:\n    f()\nexcept BaseException:\n    pass\n')
    assert 'BASE-EXCEPTION' in _rules(check_file(src))


def test_rejects_undocumented_broad_swallow(tmp_path):
    src = _write(tmp_path, 'try:\n    f()\nexcept Exception:\n    pass\n')
    assert 'BROAD-SWALLOW' in _rules(check_file(src))


def test_rejects_undocumented_narrow_pass(tmp_path):
    src = _write(tmp_path, 'try:\n    f()\nexcept OSError:\n    pass\n')
    assert 'UNDOCUMENTED-PASS' in _rules(check_file(src))


def test_rejects_diagnostically_empty_return(tmp_path):
    """`except Exception: return False` tells the caller nothing it can act
    on — it is the shape AUDIT-007 calls semantically silent."""
    src = _write(tmp_path, 'def f():\n    try:\n        g()\n'
                           '    except Exception:\n        return False\n')
    assert 'BROAD-SWALLOW' in _rules(check_file(src))


# ---------------------------------------------------------------------------
# Valid documented handlers must remain accepted.
# ---------------------------------------------------------------------------

def test_accepts_a_documented_cleanup_handler(tmp_path):
    src = _write(tmp_path,
                 'try:\n'
                 '    tmp.unlink()\n'
                 'except FileNotFoundError:\n'
                 '    # The mux worker already removed it; nothing to do.\n'
                 '    pass\n')
    assert check_file(src) == []


def test_accepts_a_comment_above_the_handler(tmp_path):
    src = _write(tmp_path,
                 'try:\n'
                 '    sock.close()\n'
                 '# Socket may already be closed during shutdown.\n'
                 'except OSError:\n'
                 '    pass\n')
    assert check_file(src) == []


def test_accepts_a_handler_that_logs(tmp_path):
    src = _write(tmp_path,
                 'try:\n    f()\nexcept Exception as e:\n'
                 '    logger.exception("f failed: %s", e)\n')
    assert check_file(src) == []


def test_a_test_fixture_containing_the_text_is_not_a_finding(tmp_path):
    """The gate's own tests contain the string 'except: pass'. Regex would
    flag them; the AST does not."""
    src = _write(tmp_path,
                 'SAMPLE = """\\ntry:\\n    f()\\nexcept:\\n    pass\\n"""\n'
                 'OTHER = "except Exception: pass"\n')
    assert check_file(src) == []


def test_docstring_only_handler_is_still_silent(tmp_path):
    """A docstring is not a justification — it does not say why the error is
    safe to drop, and it is not visible at the call site."""
    src = _write(tmp_path,
                 'try:\n    f()\nexcept Exception:\n    """ignored"""\n')
    assert 'BROAD-SWALLOW' in _rules(check_file(src))


# ---------------------------------------------------------------------------
# The real tree and the ratchet
# ---------------------------------------------------------------------------

def test_production_tree_has_no_forbidden_handlers():
    """Bare except and BaseException fail the build outright, at zero."""
    assert main([]) == 0


def test_ratchet_fails_when_a_new_silent_handler_appears(tmp_path):
    """The point of the baseline: adding one more must go red."""
    _write(tmp_path, 'try:\n    f()\nexcept Exception:\n    pass\n')
    assert main(['--root', str(tmp_path), '--baseline', '0']) == 1
    assert main(['--root', str(tmp_path), '--baseline', '1']) == 0


def test_baseline_reflects_the_current_debt():
    """If someone lowers the debt they must lower BASELINE with it, or the
    ratchet silently stops ratcheting."""
    files = list((REPO / 'FTHR_UI').rglob('*.py'))
    findings = [f for path in files for f in check_file(path)
                if f.rule in ('BROAD-SWALLOW', 'UNDOCUMENTED-PASS')]
    assert len(findings) <= BASELINE, (
        f'{len(findings)} undocumented silent handlers, baseline is '
        f'{BASELINE} — the ratchet must never be raised')


def test_empty_tree_is_a_failure_not_a_pass(tmp_path):
    """A renamed directory must not turn the gate into a no-op that reports
    success forever."""
    assert main(['--root', str(tmp_path / 'gone')]) == 1
