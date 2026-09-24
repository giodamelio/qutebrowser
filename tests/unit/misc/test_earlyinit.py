# SPDX-FileCopyrightText: Freya Bruhin (The-Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test qutebrowser.misc.earlyinit."""

import sys

import pytest

from qutebrowser.misc import earlyinit


@pytest.mark.parametrize('attr', ['stderr', '__stderr__'])
def test_init_faulthandler_stderr_none(monkeypatch, attr):
    """Make sure init_faulthandler works when sys.stderr/__stderr__ is None."""
    monkeypatch.setattr(sys, attr, None)
    earlyinit.init_faulthandler()


@pytest.mark.parametrize('same', [True, False])
def test_qt_version(same):
    if same:
        qt_version_str = '5.14.0'
        expected = '5.14.0'
    else:
        qt_version_str = '5.13.0'
        expected = '5.14.0 (compiled 5.13.0)'
    actual = earlyinit.qt_version(qversion='5.14.0', qt_version_str=qt_version_str)
    assert actual == expected


def test_qt_version_no_args():
    """Make sure qt_version without arguments at least works."""
    earlyinit.qt_version()


class _Died(Exception):
    pass


def _fake_die(message, exception=None):
    raise _Died(message)


def test_check_qt_version_refuses_qt5(monkeypatch):
    from qutebrowser.qt import core as qtcore
    monkeypatch.setattr(qtcore, 'QT_VERSION', 0x050F0A)
    monkeypatch.setattr(qtcore, 'QT_VERSION_STR', '5.15.10')
    monkeypatch.setattr(earlyinit, 'get_qt_version',
                        lambda: qtcore.QVersionNumber(5, 15, 10))
    monkeypatch.setattr(earlyinit, '_die', _fake_die)

    with pytest.raises(_Died, match='requires Qt 6'):
        earlyinit.check_qt_version()


def test_check_qt_version_accepts_qt6(monkeypatch):
    monkeypatch.setattr(earlyinit, '_die', _fake_die)
    earlyinit.check_qt_version()
