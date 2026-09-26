# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import collections
import logging
import types
from unittest import mock

import pytest
from qutebrowser.qt.core import QUrl

from qutebrowser.mainwindow import tabbedbrowser
from qutebrowser.misc import historystore
from qutebrowser.utils import usertypes


class TestTabDeque:

    @pytest.mark.parametrize('size', [-1, 5])
    def test_size_handling(self, size, config_stub):
        config_stub.val.tabs.focus_stack_size = size
        dq = tabbedbrowser.TabDeque()
        dq.update_size()


class FakeHistoryPrivate:

    def __init__(self):
        self.loaded = None

    def deserialize(self, data):
        raise OSError("corrupt history")

    def load_items(self, items):
        self.loaded = items


class FakeUndoTab:

    def __init__(self):
        self.data = types.SimpleNamespace(persistent_id=None, pinned=False)
        self.history = types.SimpleNamespace(private_api=FakeHistoryPrivate())
        self.title_changed = mock.Mock()
        self.set_pinned = mock.Mock()
        self.setFocus = mock.Mock()


def test_undo_loads_page_when_saved_history_is_corrupt(
        config_stub, message_mock, caplog):
    config_stub.val.tabs.last_close = 'ignore'
    tab = FakeUndoTab()
    browser = types.SimpleNamespace(
        widget=mock.Mock(**{'count.return_value': 1}),
        undo_stack=collections.deque(),
        tabopen=lambda background, idx: tab)
    tab_id = historystore.new_id()
    browser.undo_stack.append([tabbedbrowser._UndoEntry(
        url=QUrl('https://example.org/'), history=historystore.Snapshot(b'corrupt'),
        index=0, pinned=False, tab_id=tab_id,
        tab={'id': tab_id, 'history': [
            {'url': 'https://example.org/', 'title': 'page',
             'active': True}]})])

    with caplog.at_level(logging.ERROR):
        tabbedbrowser.TabbedBrowser.undo(browser)

    assert not browser.undo_stack
    assert tab.data.persistent_id == tab_id
    [item] = tab.history.private_api.loaded
    assert item.url == QUrl('https://example.org/')
    assert message_mock.getmsg(usertypes.MessageLevel.error).text == (
        "Failed to restore the history of https://example.org/: "
        "corrupt history")
