# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import collections
import logging

import pytest
from qutebrowser.qt.core import QByteArray, QUrl

from qutebrowser.mainwindow import mainwindow, windowsessions
from qutebrowser.misc import historystore, sessionfile
from qutebrowser.utils import objreg, usertypes


def test_round_trip(tmp_path):
    path = tmp_path / 'work.yml'
    data = sessionfile.SessionData(
        container='default',
        windows=[{'geometry': b'geo', 'tabs': [{'history': []}]}],
        closed_windows=[{'closed_at': '2026-09-24T18:03:11Z', 'tabs': []}],
    )
    sessionfile.write(path, data)
    assert sessionfile.read(path) == data


def test_write_omits_empty_closed_windows(tmp_path):
    path = tmp_path / 'work.yml'
    sessionfile.write(path, sessionfile.SessionData())
    text = path.read_text(encoding='utf-8')
    assert 'closed_windows' not in text
    assert 'container: default' in text


def test_read_upstream_file(tmp_path, caplog):
    path = tmp_path / 'old.yml'
    path.write_text(
        "windows:\n"
        "- tabs: []\n"
        "- private: true\n"
        "  tabs: []\n",
        encoding='utf-8')
    with caplog.at_level(logging.WARNING, 'sessions'):
        data = sessionfile.read(path)
    assert data == sessionfile.SessionData(windows=[{'tabs': []}])
    assert 'skipping a private window' in caplog.text


@pytest.mark.parametrize('content, match', [
    ('', 'expected a mapping'),
    ('- a\n', 'expected a mapping'),
    ('windows: 3\n', "'windows' must be a list of mappings"),
    ('windows: [1]\n', "'windows' must be a list of mappings"),
    ('closed_windows: x\n', "'closed_windows' must be a list of mappings"),
    ('container: 3\n', "'container' must be a string"),
    ('windows: [\n', 'while parsing'),
])
def test_read_invalid(tmp_path, content, match):
    path = tmp_path / 'bad.yml'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(sessionfile.SessionFileError, match=match):
        sessionfile.read(path)


def test_read_missing(tmp_path):
    with pytest.raises(sessionfile.SessionFileError, match='missing.yml'):
        sessionfile.read(tmp_path / 'missing.yml')


def test_write_failure(tmp_path):
    with pytest.raises(sessionfile.SessionFileError):
        sessionfile.write(tmp_path / 'no-such-dir' / 'x.yml',
                          sessionfile.SessionData())


class FakeTabData:

    def __init__(self):
        self.pinned = False
        self.persistent_id = historystore.new_id()
        self.lazy_history = None


class FakeHistoryPrivate:

    def __init__(self):
        self.loaded = None
        self.deserialized = None

    def load_items(self, items):
        self.loaded = items

    def deserialize(self, data):
        self.deserialized = bytes(data)

    def serialize(self):
        return QByteArray(b'live history')


class FakeHistory:

    def __init__(self):
        self.private_api = FakeHistoryPrivate()

    def __iter__(self):
        return iter([])


class FakeSignal:

    def __init__(self):
        self.emitted = []
        self.slots = []

    def emit(self, *args):
        self.emitted.append(args)

    def connect(self, slot):
        self.slots.append(slot)


class FakeTab:

    def __init__(self):
        self.data = FakeTabData()
        self.history = FakeHistory()
        self.title_changed = FakeSignal()

    def set_pinned(self, pinned):
        self.data.pinned = pinned


class FakeTabWidget:

    def __init__(self):
        self.current_index = None

    def setCurrentIndex(self, index):
        self.current_index = index


class FakeTabbedBrowser:

    undo_stack_size = None

    def __init__(self):
        self.is_shut_down = False
        self.tabs = []
        self.undo_stack = collections.deque(maxlen=self.undo_stack_size)
        self.current_tab_changed = FakeSignal()
        self.widget = FakeTabWidget()

    def tabopen(self, background):
        tab = FakeTab()
        self.tabs.append(tab)
        return tab

    def widgets(self):
        return self.tabs

    def shutdown(self):
        self.is_shut_down = True


class FakeMainWindow:

    def __init__(self, *, geometry, session):
        self.win_id = 1
        self.session = session
        self.tabbed_browser = FakeTabbedBrowser()
        self.is_deleted = False

    def deleteLater(self):
        self.is_deleted = True


@pytest.fixture
def fake_mainwindows(monkeypatch, mocker, config_stub):
    created = []

    def make(**kwargs):
        window = FakeMainWindow(**kwargs)
        created.append(window)
        return window

    monkeypatch.setattr(mainwindow, 'MainWindow', make)
    monkeypatch.setattr(windowsessions, 'manager', mocker.Mock())
    return created


@pytest.mark.parametrize('data', [
    {},
    {'tabs': [{}]},
    {'tabs': [5]},
    {'tabs': [{'history': [{'title': 'no url'}]}]},
    {'tabs': [{'history': [{'url': 5, 'title': 'int url'}]}]},
])
def test_restore_window_invalid_data(fake_mainwindows, data):
    session = windowsessions.Session('work', private=False)
    with pytest.raises(sessionfile.SessionFileError, match='work'):
        sessionfile.restore_window(data, session)

    [window] = fake_mainwindows
    assert window.tabbed_browser.is_shut_down
    assert window.is_deleted
    windowsessions.manager.remove_window.assert_called_once_with(session, 1)


def test_restore_window_invalid_geometry(fake_mainwindows):
    session = windowsessions.Session('work', private=False)
    with pytest.raises(sessionfile.SessionFileError, match='work'):
        sessionfile.restore_window({'geometry': 'x', 'tabs': []}, session)
    assert not fake_mainwindows


def tab_data(tab_id, url='https://example.org/', *, active=False):
    data = {'history': [{'url': url, 'title': 'page', 'active': True}]}
    if tab_id is not None:
        data['id'] = tab_id
    if active:
        data['active'] = True
    return data


def restore(data, session=None):
    if session is None:
        session = windowsessions.Session('work', private=False)
    return sessionfile.restore_window(data, session, show=False)


def test_serialize_tab_writes_the_id():
    tab = FakeTab()
    assert sessionfile.serialize_tab(tab, True) == {
        'history': [], 'active': True, 'id': tab.data.persistent_id}


def test_tab_history():
    assert sessionfile.tab_history(FakeTab()) == b'live history'


def test_deserialize_tab():
    tab = FakeTab()
    sessionfile.deserialize_tab(tab, b'saved history')
    assert tab.history.private_api.deserialized == b'saved history'


# Restores in these tests can warn about missing history files from Task 5
# on, which isn't what they check.

def test_restore_window_keeps_saved_ids(fake_mainwindows, message_mock,
                                        caplog):
    first, second = historystore.new_id(), historystore.new_id()
    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [tab_data(first), tab_data(second)]})
    assert [tab.data.persistent_id for tab in window.tabbed_browser.tabs] == [
        first, second]


@pytest.mark.parametrize('saved_id', [
    None, 5, 'not-an-id', 'A' * 32, '../../../etc/passwd',
])
def test_restore_window_replaces_unusable_ids(fake_mainwindows, message_mock,
                                              caplog, saved_id):
    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [tab_data(saved_id)]})
    [tab] = window.tabbed_browser.tabs
    assert historystore.is_valid_id(tab.data.persistent_id)
    assert tab.data.persistent_id != saved_id


def test_restore_window_duplicate_id_gets_new_id(fake_mainwindows,
                                                 message_mock, caplog):
    tab_id = historystore.new_id()
    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [tab_data(tab_id), tab_data(tab_id)]})
    first, second = window.tabbed_browser.tabs
    assert first.data.persistent_id == tab_id
    assert second.data.persistent_id != tab_id


def test_restore_window_id_of_open_tab_gets_new_id(fake_mainwindows,
                                                   monkeypatch, message_mock,
                                                   caplog):
    session = windowsessions.Session('work', private=False)
    live = FakeMainWindow(geometry=None, session=session)
    live.win_id = 7
    live_tab = live.tabbed_browser.tabopen(background=False)
    monkeypatch.setattr(objreg, 'window_registry', {7: live})
    session.windows.add(7)

    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [tab_data(live_tab.data.persistent_id)]},
                         session)

    [tab] = window.tabbed_browser.tabs
    assert tab.data.persistent_id != live_tab.data.persistent_id


def test_native_history_bytes_restore_every_entry(qtbot, webengine_tab):
    """QtWebEngine's own bytes bring back every entry (upstream #5359)."""
    urls = [QUrl(f'qute://testdata/data/backforward/{name}')
            for name in ['1.txt', '2.txt', '3.txt']]
    for url in urls[:2]:
        with qtbot.wait_signal(webengine_tab.load_finished):
            webengine_tab.load_url(url)
    history = sessionfile.tab_history(webengine_tab)
    with qtbot.wait_signal(webengine_tab.load_finished):
        webengine_tab.load_url(urls[2])

    with qtbot.wait_signal(webengine_tab.load_finished):
        sessionfile.deserialize_tab(webengine_tab, history)

    assert [item.url() for item in webengine_tab.history] == urls[:2]
    assert webengine_tab.url() == urls[1]
