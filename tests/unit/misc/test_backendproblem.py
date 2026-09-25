# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import pathlib

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


def test_service_workers_removed_for_every_container(data_tmpdir,
                                                     state_config):
    data_dir = pathlib.Path(str(data_tmpdir))
    storage_dirs = [data_dir / 'webengine', data_dir / 'containers' / 'work']
    for storage_dir in storage_dirs:
        (storage_dir / 'Service Worker').mkdir(parents=True)
    state_config.qt_version_changed = True
    checker = backendproblem._BackendProblemChecker(
        no_err_windows=True, save_manager=None)

    checker._handle_serviceworker_nuking()

    for storage_dir in storage_dirs:
        assert not (storage_dir / 'Service Worker').exists()
        assert (storage_dir / 'Service Worker-bak').exists()
