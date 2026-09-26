# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import logging

import pytest

from qutebrowser.api import cmdutils
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import closedwindows, ipc, quitter
from qutebrowser.utils import message, usertypes


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


@pytest.mark.parametrize('running, answer, quits', [
    (0, None, True),
    (1, True, True),
    (1, False, False),
    (2, None, False),
])
def test_quit_asks_about_running_downloads(mocker, monkeypatch, running,
                                           answer, quits):
    instance = mocker.Mock()
    monkeypatch.setattr(quitter, 'instance', instance)
    monkeypatch.setattr(closedwindows, 'running_downloads', lambda: running)
    ask = mocker.patch.object(message, 'ask', return_value=answer)

    quitter.quit_(win_id=3)

    assert instance.shutdown.called is quits
    if running:
        noun = 'download' if running == 1 else 'downloads'
        ask.assert_called_once_with(
            title=f"{running} {noun} still running. Close anyway?",
            mode=usertypes.PromptMode.yesno, default=False, win_id=3)
    else:
        ask.assert_not_called()
