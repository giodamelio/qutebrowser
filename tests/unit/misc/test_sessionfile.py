# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import collections
import datetime
import logging
import struct

import pytest
from qutebrowser.qt.core import QByteArray, QUrl

from qutebrowser.mainwindow import mainwindow, windowsessions, tabbedbrowser
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
def histories():
    """History bytes restore_window finds, by tab id; other ids have none."""
    return {}


@pytest.fixture
def fake_mainwindows(monkeypatch, mocker, config_stub, histories):
    created = []

    def make(**kwargs):
        window = FakeMainWindow(**kwargs)
        created.append(window)
        return window

    def read_history(session, tab_id):
        if tab_id not in histories:
            raise historystore.UnusableHistoryError('history file missing')
        return histories[tab_id]

    manager = mocker.Mock()
    manager.read_history.side_effect = read_history
    monkeypatch.setattr(mainwindow, 'MainWindow', make)
    monkeypatch.setattr(windowsessions, 'manager', manager)
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


def test_window_history():
    tabbed_browser = FakeTabbedBrowser()
    tab = tabbed_browser.tabopen(background=False)
    window = FakeMainWindow(geometry=None, session=None)
    window.tabbed_browser = tabbed_browser
    assert sessionfile.window_history(window) == {
        tab.data.persistent_id: b'live history'}


def test_referenced_ids():
    ids = [historystore.new_id() for _ in range(4)]
    data = sessionfile.SessionData(
        windows=[
            {'tabs': [{'id': ids[0]}, {'id': '../evil'}, {}, 5],
             'closed_tabs': [[{'id': ids[1]}], 'junk']},
            'junk',
        ],
        closed_windows=[
            {'window': {'tabs': [{'id': ids[2]}],
                        'closed_tabs': [[{'id': ids[3]}]]}},
            {'window': None},
            5,
        ])
    assert sessionfile.referenced_ids(data) == set(ids)


@pytest.mark.parametrize('window', [
    'junk',
    {'tabs': {'first': {'id': 'a' * 32}}},
    {'tabs': [{'id': 'a' * 32}, 5]},
    {'tabs': [], 'closed_tabs': ['junk']},
    {'tabs': [], 'closed_tabs': [[{'id': 'a' * 32}, 'junk']]},
])
def test_referenced_ids_strict(window):
    for data in [sessionfile.SessionData(windows=[window]),
                 sessionfile.SessionData(closed_windows=[{'window': window}])]:
        with pytest.raises(ValueError):
            sessionfile.referenced_ids(data, strict=True)


def test_referenced_ids_strict_accepts_valid_shapes():
    ids = [historystore.new_id() for _ in range(3)]
    data = sessionfile.SessionData(
        windows=[{'tabs': [{'id': ids[0]}, {'id': '../evil'}]}],
        closed_windows=[{'window': {'tabs': [{'id': ids[1]}],
                                    'closed_tabs': [[{'id': ids[2]}]]}}])
    assert sessionfile.referenced_ids(data, strict=True) == set(ids)


def test_restore_window_loads_history_bytes(fake_mainwindows, histories):
    tab_id = historystore.new_id()
    histories[tab_id] = b'saved history'
    window = restore({'tabs': [tab_data(tab_id)]})
    [tab] = window.tabbed_browser.tabs
    assert tab.history.private_api.deserialized == b'saved history'
    assert tab.history.private_api.loaded is None
    assert tab.title_changed.emitted == [('page',)]


def tab_data_with_back(tab_id):
    data = tab_data(tab_id)
    data['history'].insert(0, {'url': 'https://example.com/', 'title': 'back'})
    return data


def test_restore_window_falls_back_with_one_warning(fake_mainwindows,
                                                    histories, message_mock,
                                                    caplog):
    kept, missing, old = (historystore.new_id() for _ in range(3))
    histories[kept] = b'saved history'
    read_history = windowsessions.manager.read_history.side_effect

    def read_old(session, tab_id):
        if tab_id == old:
            raise historystore.UnusableHistoryError(
                'saved by QtWebEngine 6.10.0, running 6.11.2')
        return read_history(session, tab_id)

    windowsessions.manager.read_history.side_effect = read_old
    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [
            tab_data_with_back(kept), tab_data_with_back(missing),
            tab_data_with_back(old), tab_data_with_back(None)]})

    tabs = window.tabbed_browser.tabs
    assert [tab.history.private_api.deserialized for tab in tabs] == [
        b'saved history', None, None, None]
    assert [item.url for item in tabs[1].history.private_api.loaded] == [
        QUrl('https://example.com/'), QUrl('https://example.org/')]
    msg = message_mock.getmsg(usertypes.MessageLevel.warning)
    assert msg.text == (
        "3 tabs restored without back history: history file missing; "
        "saved by QtWebEngine 6.10.0, running 6.11.2; no usable tab id")


def test_restore_window_single_entry_without_history_is_silent(
        fake_mainwindows, message_mock):
    """A tab with one entry has no back history to lose.

    Its history file may legitimately be missing: empty histories are never
    written.
    """
    window = restore({'tabs': [tab_data(historystore.new_id()),
                               tab_data(None)]})
    tabs = window.tabbed_browser.tabs
    assert [[item.url for item in tab.history.private_api.loaded]
            for tab in tabs] == [[QUrl('https://example.org/')]] * 2
    assert not message_mock.messages


def test_restore_window_duplicate_id_reads_bytes_once(fake_mainwindows,
                                                      histories, message_mock,
                                                      caplog):
    tab_id = historystore.new_id()
    histories[tab_id] = b'saved history'
    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [tab_data_with_back(tab_id),
                                   tab_data_with_back(tab_id)]})
    first, second = window.tabbed_browser.tabs
    assert first.history.private_api.deserialized == b'saved history'
    assert second.history.private_api.deserialized is None
    calls = windowsessions.manager.read_history.call_args_list
    assert [call.args[1] for call in calls] == [tab_id]
    assert message_mock.getmsg(usertypes.MessageLevel.warning).text == (
        "1 tab restored without back history: no usable tab id")


def test_restore_window_rejected_history(fake_mainwindows, histories,
                                         monkeypatch):
    tab_id = historystore.new_id()
    histories[tab_id] = b'bytes qt refuses'

    def reject(self, data):
        raise OSError("QDataStream: read past end")

    monkeypatch.setattr(FakeHistoryPrivate, 'deserialize', reject)
    session = windowsessions.Session('work', private=False)
    with pytest.raises(sessionfile.SessionFileError, match='OSError'):
        sessionfile.restore_window({'tabs': [tab_data(tab_id)]}, session,
                                   show=False)
    [window] = fake_mainwindows
    assert window.is_deleted


def test_restore_window_lazy_tabs_wait_until_shown(fake_mainwindows,
                                                   histories, config_stub):
    config_stub.val.session.lazy_restore = True
    hidden_id, shown_id = historystore.new_id(), historystore.new_id()
    histories.update({hidden_id: b'hidden history',
                      shown_id: b'shown history'})

    window = restore({'tabs': [
        tab_data(hidden_id, 'https://hidden.example/'),
        tab_data(shown_id, 'https://shown.example/', active=True),
    ]})

    hidden, shown = window.tabbed_browser.tabs
    assert shown.history.private_api.deserialized == b'shown history'
    assert shown.data.lazy_history is None
    assert hidden.history.private_api.deserialized is None
    assert hidden.history.private_api.loaded is None
    assert hidden.data.lazy_history.history == b'hidden history'
    assert hidden.data.lazy_history.url == QUrl('https://hidden.example/')
    assert hidden.title_changed.emitted == [('page',)]
    assert window.tabbed_browser.current_tab_changed.slots == [
        sessionfile.load_lazy_history]

    sessionfile.load_lazy_history(hidden)
    assert hidden.history.private_api.deserialized == b'hidden history'
    assert hidden.data.lazy_history is None


def test_restore_window_lazy_needs_its_own_tab_bar(fake_mainwindows,
                                                   histories, config_stub):
    config_stub.val.session.lazy_restore = True
    config_stub.val.tabs.tabs_are_windows = True
    first_id, second_id = historystore.new_id(), historystore.new_id()
    histories.update({first_id: b'history', second_id: b'other'})
    window = restore({'tabs': [tab_data(first_id),
                               tab_data(second_id, active=True)]})
    first = window.tabbed_browser.tabs[0]
    assert first.data.lazy_history is None
    assert first.history.private_api.deserialized == b'history'


def test_lazy_tab_saves_its_saved_history():
    tab = FakeTab()
    tab.data.pinned = True
    saved = {
        'id': historystore.new_id(),
        'history': [
            {'url': 'https://a.example/', 'title': 'a', 'pinned': False},
            {'url': 'https://b.example/', 'title': 'b', 'active': True,
             'pinned': False},
        ],
    }
    tab.data.lazy_history = sessionfile.LazyHistory(data=saved,
                                                    history=b'saved bytes')

    assert sessionfile.serialize_tab(tab, False) == {
        'id': tab.data.persistent_id,
        'history': [
            {'url': 'https://a.example/', 'title': 'a', 'pinned': True},
            {'url': 'https://b.example/', 'title': 'b', 'active': True,
             'pinned': True},
        ],
    }
    assert sessionfile.tab_history(tab) == b'saved bytes'
    assert tab.data.lazy_history.url == QUrl('https://b.example/')


def lazy_history(*, active=True):
    return sessionfile.LazyHistory(data={'history': [
        {'url': 'https://a.example/', 'title': 'a'},
        {'url': 'https://b.example/', 'title': 'b', 'active': active},
    ]}, history=b'saved bytes')


@pytest.mark.parametrize('active', [True, False])
def test_tab_url_and_title_of_an_unshown_lazy_tab(active):
    tab = FakeTab()
    tab.data.lazy_history = lazy_history(active=active)
    assert sessionfile.tab_url(tab) == QUrl('https://b.example/')
    assert sessionfile.tab_title(tab) == 'b'


def test_tab_url_and_title_follow_the_live_page_once_shown():
    tab = FakeTab()
    tab.url = lambda: QUrl('https://live.example/')
    tab.title = lambda: 'live'
    tab.data.lazy_history = lazy_history()

    sessionfile.load_lazy_history(tab)

    assert sessionfile.tab_url(tab) == QUrl('https://live.example/')
    assert sessionfile.tab_title(tab) == 'live'


def test_lazy_restore_without_bytes_keeps_upstream_behavior(
        fake_mainwindows, config_stub, message_mock, caplog):
    config_stub.val.session.lazy_restore = True
    with caplog.at_level(logging.WARNING):
        window = restore({'tabs': [tab_data(historystore.new_id())]})
    [tab] = window.tabbed_browser.tabs
    assert tab.data.lazy_history is None
    assert [item.url.toString() for item in tab.history.private_api.loaded] == [
        'https://example.org/', 'qute://back#page']


def test_lazy_history_qt_refuses_loads_the_saved_page(config_stub,
                                                      message_mock, caplog):
    tab = FakeTab()

    def reject(data):
        raise OSError('QDataStream: read past end')

    tab.history.private_api.deserialize = reject
    tab.data.lazy_history = sessionfile.LazyHistory(
        data=tab_data(None, 'https://a.example/'), history=b'bad')

    with caplog.at_level(logging.ERROR):
        sessionfile.load_lazy_history(tab)

    assert tab.data.lazy_history is None
    assert [item.url for item in tab.history.private_api.loaded] == [
        QUrl('https://a.example/')]
    assert 'read past end' in message_mock.getmsg(
        usertypes.MessageLevel.error).text


def test_restore_window_lazy_shows_the_last_active_tab(fake_mainwindows,
                                                       histories, config_stub):
    config_stub.val.session.lazy_restore = True
    first_id, second_id = historystore.new_id(), historystore.new_id()
    histories.update({first_id: b'first', second_id: b'second'})
    window = restore({'tabs': [tab_data(first_id, active=True),
                               tab_data(second_id, active=True)]})
    first, second = window.tabbed_browser.tabs
    assert window.tabbed_browser.widget.current_index == 1
    assert second.data.lazy_history is None
    assert second.history.private_api.deserialized == b'second'
    assert first.data.lazy_history.history == b'first'


def undo_entry(tab_id, url, *, index=0, pinned=False,
               history=b'closed history'):
    return tabbedbrowser._UndoEntry(
        url=QUrl(url), history=historystore.Snapshot(history), index=index,
        pinned=pinned, created_at=datetime.datetime(2026, 9, 25, 12, 0, 0),
        tab_id=tab_id, tab=tab_data(tab_id, url))


def test_serialize_closed_tabs_newest_first():
    first, second, third = (historystore.new_id() for _ in range(3))
    stack = collections.deque([
        [undo_entry(first, 'https://a.example/')],
        [undo_entry(second, 'https://b.example/', index=2, pinned=True),
         undo_entry(third, 'https://c.example/', index=3)],
    ])

    closed = sessionfile._serialize_closed_tabs(stack)

    assert [[item['id'] for item in group] for group in closed] == [
        [second, third], [first]]
    item = closed[0][0]
    assert (item['index'], item['pinned']) == (2, True)
    assert item['tab'] == tab_data(second, 'https://b.example/')
    assert (datetime.datetime.fromisoformat(item['closed_at']) ==
            datetime.datetime(2026, 9, 25, 12, 0, 0).astimezone())


def test_window_history_includes_closed_tabs():
    tabbed_browser = FakeTabbedBrowser()
    tab = tabbed_browser.tabopen(background=False)
    closed_id, lost_id, stub_id = (historystore.new_id() for _ in range(3))
    lost = undo_entry(lost_id, 'https://lost.example/')
    lost.history = None
    # QTBUG-117489's count-0 stub, from a tab closed mid-load.
    stub = undo_entry(stub_id, 'https://stub.example/',
                      history=struct.pack('>IIi', 4, 0, 0))
    tabbed_browser.undo_stack.append(
        [undo_entry(closed_id, 'https://a.example/'), lost, stub])
    window = FakeMainWindow(geometry=None, session=None)
    window.tabbed_browser = tabbed_browser
    assert sessionfile.window_history(window) == {
        tab.data.persistent_id: b'live history', closed_id: b'closed history'}


def closed_item(tab_id, url, closed_at, *, index=0, pinned=False,
                back=False):
    tab = tab_data(tab_id, url)
    if back:
        tab['history'].insert(0, {'url': 'https://back.example/',
                                  'title': 'back'})
    return {'id': tab_id, 'index': index, 'pinned': pinned,
            'closed_at': closed_at, 'tab': tab}


def test_restore_window_rebuilds_closed_tabs(fake_mainwindows, histories,
                                             message_mock, caplog):
    open_id, kept, missing = (historystore.new_id() for _ in range(3))
    histories.update({open_id: b'open', kept: b'closed history'})
    data = {'tabs': [tab_data(open_id)], 'closed_tabs': [
        [closed_item(kept, 'https://kept.example/',
                     '2026-09-25T10:00:00.000+00:00', index=1, pinned=True)],
        [closed_item(missing, 'https://missing.example/',
                     '2026-09-25T09:00:00.000+00:00', back=True)],
    ]}

    with caplog.at_level(logging.WARNING):
        window = restore(data)

    stack = window.tabbed_browser.undo_stack
    assert [[entry.tab_id for entry in group] for group in stack] == [
        [missing], [kept]]
    [entry] = stack[-1]
    assert entry.history == b'closed history'
    assert entry.url == QUrl('https://kept.example/')
    assert (entry.index, entry.pinned) == (1, True)
    assert entry.tab == tab_data(kept, 'https://kept.example/')
    assert entry.created_at == datetime.datetime(
        2026, 9, 25, 10, tzinfo=datetime.timezone.utc).astimezone().replace(
            tzinfo=None)
    assert stack[0][0].history is None
    assert message_mock.getmsg(usertypes.MessageLevel.warning).text == (
        "1 tab restored without back history: history file missing")


def test_save_hashes_an_unchanged_closed_tab_once(fake_mainwindows,
                                                  histories, tmp_path,
                                                  monkeypatch):
    """A closed tab's bytes never change, so every autosave needn't hash them."""
    tab_id = historystore.new_id()
    histories[tab_id] = b'closed history'
    window = restore({'tabs': [], 'closed_tabs': [
        [closed_item(tab_id, 'https://a.example/',
                     '2026-09-25T10:00:00.000+00:00')]]})
    hashed = []
    real_digest = historystore.digest

    def digest(data):
        hashed.append(bytes(data))
        return real_digest(data)

    monkeypatch.setattr(historystore, 'digest', digest)
    monkeypatch.setattr(historystore, 'running_version', lambda: '6.11.2')
    digests = {}
    historystore.write_changed(tmp_path, sessionfile.window_history(window),
                               digests)
    assert hashed == [b'closed history']

    hashed.clear()
    historystore.write_changed(tmp_path, sessionfile.window_history(window),
                               digests)
    assert hashed == []


def test_restore_window_closed_tab_without_back_history_is_silent(
        fake_mainwindows, message_mock):
    missing = historystore.new_id()
    window = restore({'tabs': [], 'closed_tabs': [
        [closed_item(missing, 'https://missing.example/',
                     '2026-09-25T09:00:00.000+00:00')],
        [closed_item(None, 'https://no-id.example/',
                     '2026-09-25T08:00:00.000+00:00')],
    ]})
    [[missing_entry], [no_id_entry]] = reversed(
        window.tabbed_browser.undo_stack)
    assert missing_entry.history is None
    assert historystore.is_valid_id(no_id_entry.tab_id)
    assert not message_mock.messages


def test_restore_window_caps_closed_tabs(fake_mainwindows, histories,
                                         monkeypatch):
    monkeypatch.setattr(FakeTabbedBrowser, 'undo_stack_size', 1)
    newest, oldest = historystore.new_id(), historystore.new_id()
    histories.update({newest: b'newest', oldest: b'oldest'})
    window = restore({'tabs': [], 'closed_tabs': [
        [closed_item(newest, 'https://new.example/',
                     '2026-09-25T10:00:00.000+00:00')],
        [closed_item(oldest, 'https://old.example/',
                     '2026-09-25T09:00:00.000+00:00')],
    ]})
    assert [[entry.tab_id for entry in group]
            for group in window.tabbed_browser.undo_stack] == [[newest]]


@pytest.mark.parametrize('closed_tabs', [
    5,
    [5],
    [[{'id': 'a' * 32}]],
    [[{'id': 'a' * 32, 'index': 'x', 'pinned': False,
       'closed_at': '2026-09-25T10:00:00.000+00:00', 'tab': tab_data(None)}]],
    [[{'id': 'a' * 32, 'index': 0, 'pinned': False, 'closed_at': 'never',
       'tab': tab_data(None)}]],
    [[{'id': 'a' * 32, 'index': 0, 'pinned': False,
       'closed_at': '2026-09-25T10:00:00.000+00:00',
       'tab': {'history': [{'url': 'https://example.org/'}]}}]],
    [[{'id': 'a' * 32, 'index': 0, 'pinned': False,
       'closed_at': '2026-09-25T10:00:00.000+00:00',
       'tab': {'history': [{'url': 5, 'title': 'page'}]}}]],
    [[{'id': 'a' * 32, 'index': 0, 'pinned': False,
       'closed_at': '2026-09-25T10:00:00.000+00:00',
       'tab': {'history': 'x'}}]],
])
def test_restore_window_invalid_closed_tabs(fake_mainwindows, closed_tabs,
                                            message_mock, caplog):
    session = windowsessions.Session('work', private=False)
    with caplog.at_level(logging.WARNING), pytest.raises(
            sessionfile.SessionFileError, match='work'):
        sessionfile.restore_window({'tabs': [], 'closed_tabs': closed_tabs},
                                   session, show=False)
    [window] = fake_mainwindows
    assert window.is_deleted


def test_restore_window_closed_tab_with_taken_id_gets_new_one(
        fake_mainwindows, histories):
    tab_id = historystore.new_id()
    histories[tab_id] = b'open'
    window = restore({'tabs': [tab_data(tab_id)], 'closed_tabs': [
        [closed_item(tab_id, 'https://closed.example/',
                     '2026-09-25T10:00:00.000+00:00')],
    ]})
    [[entry]] = window.tabbed_browser.undo_stack
    assert entry.tab_id != tab_id
    assert entry.history is None
    assert entry.tab['id'] == entry.tab_id
