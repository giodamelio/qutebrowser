# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import copy
import logging
import types

import pytest

from qutebrowser.qt.core import QUrl
from qutebrowser.api import cmdutils
from qutebrowser.browser import browsertab, commands
from qutebrowser.misc import historystore, sessionfile
from qutebrowser.utils import usertypes


class TestAction:

    def test_run_string_valid(self, qtbot, web_tab):
        url_1 = QUrl("qute://testdata/data/backforward/1.txt")
        url_2 = QUrl("qute://testdata/data/backforward/2.txt")

        with qtbot.wait_signal(web_tab.load_finished):
            web_tab.load_url(url_1)
        with qtbot.wait_signal(web_tab.load_finished):
            web_tab.load_url(url_2)

        assert web_tab.url() == url_2
        with qtbot.wait_signal(web_tab.load_finished):
            web_tab.action.run_string("Back")
        assert web_tab.url() == url_1

    @pytest.mark.parametrize("member", ["blah", "PermissionUnknown"])
    def test_run_string_invalid(self, qtbot, web_tab, member):
        with pytest.raises(
            browsertab.WebTabError,
            match=f"{member} is not a valid web action!",
        ):
            web_tab.action.run_string(member)


def test_tab_data_gets_a_fresh_persistent_id():
    first, second = browsertab.TabData(), browsertab.TabData()
    assert historystore.is_valid_id(first.persistent_id)
    assert first.persistent_id != second.persistent_id


def _raise_web_tab_error(tab):
    raise browsertab.WebTabError("boom")


def test_tab_give_wraps_history_error(config_stub, monkeypatch):
    dispatcher = commands.CommandDispatcher(
        0, types.SimpleNamespace(session=object()))
    target = types.SimpleNamespace(session=object(), is_private=False)
    monkeypatch.setattr(commands.objreg, 'window_registry', {1: target})
    monkeypatch.setattr(commands.objreg, 'get', lambda *args, **kwargs: target)
    monkeypatch.setattr(commands.windowsessions, 'check_same_profile',
                        lambda a, b: None)
    monkeypatch.setattr(dispatcher, '_current_widget', lambda: object())
    monkeypatch.setattr(commands.sessionfile, 'tab_history',
                        _raise_web_tab_error)

    with pytest.raises(cmdutils.CommandError):
        dispatcher.tab_give(win_id=1)


def test_tab_take_wraps_history_error(config_stub, monkeypatch):
    dispatcher = commands.CommandDispatcher(
        0, types.SimpleNamespace(session=object()))
    other = types.SimpleNamespace(session=object())
    monkeypatch.setattr(dispatcher, '_resolve_tab_index',
                        lambda index: (other, object()))
    monkeypatch.setattr(commands.windowsessions, 'check_same_profile',
                        lambda a, b: None)
    monkeypatch.setattr(commands.sessionfile, 'tab_history',
                        _raise_web_tab_error)

    with pytest.raises(cmdutils.CommandError):
        dispatcher.tab_take('1/1')


@pytest.mark.parametrize('keep', [False, True])
def test_tab_take_lazy_tab_qt_refuses(config_stub, monkeypatch, message_mock,
                                      caplog, keep):
    config_stub.val.session.lazy_restore = True

    def reject(data):
        raise OSError('QDataStream: read past end')

    loaded = []
    newtab = types.SimpleNamespace(
        data=browsertab.TabData(),
        history=types.SimpleNamespace(private_api=types.SimpleNamespace(
            deserialize=reject, load_items=loaded.extend)),
        title_changed=types.SimpleNamespace(emit=lambda title: None))
    saved = {'history': [{'url': 'https://a.example/', 'title': 'a',
                          'active': True}]}
    tab = types.SimpleNamespace(data=browsertab.TabData())
    tab.data.lazy_history = sessionfile.LazyHistory(data=copy.deepcopy(saved),
                                                    history=b'bad')
    dispatcher = commands.CommandDispatcher(0, types.SimpleNamespace(
        session=object(), tabopen=lambda background, related: newtab))
    other = types.SimpleNamespace(session=object(),
                                  close_tab=lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatcher, '_resolve_tab_index',
                        lambda index: (other, tab))
    monkeypatch.setattr(commands.windowsessions, 'check_same_profile',
                        lambda a, b: None)

    with caplog.at_level(logging.ERROR):
        dispatcher.tab_take('1/1', keep=keep)

    assert loaded[0].url == QUrl('https://a.example/')
    assert tab.data.lazy_history.data == saved
    assert 'read past end' in message_mock.getmsg(
        usertypes.MessageLevel.error).text
