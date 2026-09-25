# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import logging

import pytest

from qutebrowser.api import cmdutils
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import ipc, quitter


def test_failed_restart_resumes_sessions(mocker, monkeypatch, caplog):
    manager = mocker.Mock()
    monkeypatch.setattr(windowsessions, 'manager', manager)
    monkeypatch.setattr(ipc, 'server', mocker.Mock())
    instance = quitter.Quitter(args=argparse.Namespace())
    mocker.patch.object(instance, '_compile_modules')
    mocker.patch.object(instance, '_get_restart_args', return_value=['nope'])
    mocker.patch.object(quitter.subprocess, 'Popen', side_effect=OSError)

    with caplog.at_level(logging.ERROR):
        assert not instance.restart()

    manager.shutdown.assert_called_once_with()
    manager.resume.assert_called_once_with()


def test_restart_command_reports_failure(mocker, monkeypatch):
    instance = mocker.Mock()
    instance.restart.return_value = False
    monkeypatch.setattr(quitter, 'instance', instance)
    with pytest.raises(cmdutils.CommandError, match='Restart failed'):
        quitter.restart()
    instance.shutdown.assert_not_called()
