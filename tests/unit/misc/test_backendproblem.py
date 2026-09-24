# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import pytest

from qutebrowser.misc import backendproblem, objects
from qutebrowser.utils import usertypes


def test_qtwebkit_refused(monkeypatch, caplog):
    monkeypatch.setattr(objects, 'backend', usertypes.Backend.QtWebKit)
    checker = backendproblem._BackendProblemChecker(
        no_err_windows=True, save_manager=None)

    with caplog.at_level(logging.ERROR, 'init'):
        with pytest.raises(SystemExit) as excinfo:
            checker.check()

    assert excinfo.value.code == usertypes.Exit.err_init
    assert 'only supports the QtWebEngine backend' in caplog.text
