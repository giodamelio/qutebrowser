# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime
import logging
import os
import pathlib
import subprocess
import sys
import time

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.qt.core import QUrl

import qutebrowser
from qutebrowser.browser import sessionpages
from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import containercommands, sessioncommands, sessionfile
from qutebrowser.utils import objreg, qtutils


@pytest.fixture(autouse=True)
def fake_timers(monkeypatch, stubs):
    monkeypatch.setattr(windowsessions.usertypes, 'Timer', stubs.FakeTimer)


class FakeProfile:

    def __init__(self, key, private):
        self.key = key
        self.private = private

    def deleteLater(self):
        pass


@pytest.fixture
def profile_registry(monkeypatch):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: False)
    registry = profiles.ProfileRegistry(
        factory=FakeProfile, initializer=lambda _profile: (lambda: None))
    monkeypatch.setattr(profiles, 'registry', registry)
    return registry


class FakeTabbedBrowser:

    def __init__(self, tabs):
        self._tabs = [object() for _ in range(tabs)]

    def widgets(self):
        return self._tabs


class FakeDispatcher:

    def __init__(self):
        self.opened = []

    def openurl(self, url, **kwargs):
        self.opened.append((url, kwargs))


class FakeWindow:

    def __init__(self, win_id, tabs):
        self.win_id = win_id
        self.tabbed_browser = FakeTabbedBrowser(tabs)
        self.dispatcher = FakeDispatcher()
        self.registry = objreg.ObjectRegistry()
        self.registry['command-dispatcher'] = self.dispatcher


@pytest.fixture
def windows(monkeypatch):
    registry = {}
    monkeypatch.setattr(objreg, 'window_registry', registry)
    return registry


@pytest.fixture
def base_path(tmp_path):
    return tmp_path / 'sessions'


@pytest.fixture
def manager(container_registry, profile_registry, base_path, state_config,
            fake_save_manager, windows, monkeypatch):
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id})
    mgr.load_all()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


def open_window(manager, windows, session, win_id, tabs=1):
    windows[win_id] = FakeWindow(win_id, tabs)
    manager.add_window(session, win_id)
    return windows[win_id]


def declare(config_stub, registry, value):
    config_stub.val.containers = value
    registry.on_config_changed('containers')


def page(handler):
    mimetype, html = handler(QUrl('qute://ignored/'))
    assert mimetype == 'text/html'
    return html


def element(html, tag, element_id):
    """Get the source of the element with the given id.

    The element must not contain another element with the same tag.
    """
    start = html.index(f'<{tag} id="{element_id}"')
    return html[start:html.index(f'</{tag}>', start)]


def write_session(base_path, name, *, container='default', windows=(),
                  closed_windows=()):
    directory = base_path / name
    directory.mkdir(parents=True, exist_ok=True)
    sessionfile.write(directory / 'session.yml', sessionfile.SessionData(
        container=container, windows=list(windows),
        closed_windows=list(closed_windows)))


def saved_window(*titles):
    return {'tabs': [
        {'history': [{'url': f'http://example.com/{i}', 'title': title,
                      'active': True}]}
        for i, title in enumerate(titles)
    ]}


@pytest.mark.parametrize('host', ['containers', 'sessions'])
def test_qutescheme_registers_page(host):
    """Importing qutescheme alone registers the page, as the browser relies on."""
    code = ("from qutebrowser.browser import qutescheme; "
            "print(sorted(qutescheme._HANDLERS))")
    result = subprocess.run(
        [sys.executable, '-c', code], check=True, capture_output=True,
        text=True, cwd=pathlib.Path(qutebrowser.__file__).parents[1])
    assert repr(host) in result.stdout


def test_containers_page_rows(manager, windows, container_registry,
                              config_stub):
    container_registry.add('work', 'red')
    declare(config_stub, container_registry,
            {'decl': {'color': 'rgb(0, 0, 255)'}})
    job = manager.new_session('job', container='work')
    manager.new_session('side', container='work')
    open_window(manager, windows, job, 1)

    html = page(sessionpages.qute_containers)

    work = element(html, 'tr', 'container-work')
    assert ('<td class="name">work <span class="swatch" '
            'style="background-color: #ff0000"></span></td>') in work
    assert '<td class="source">runtime</td>' in work
    assert '<td class="sessions">job (open), side</td>' in work
    assert '<td class="loaded">loaded</td>' in work

    decl = element(html, 'tr', 'container-decl')
    assert 'style="background-color: #0000ff"' in decl
    assert '<td class="source">declared</td>' in decl
    assert '<td class="sessions">none</td>' in decl
    assert '<td class="loaded">not loaded</td>' in decl

    default = element(html, 'tr', 'container-default')
    assert 'style="background-color: #3b4252"' in default
    assert '<td class="source">built-in</td>' in default
    assert '<td class="sessions">default</td>' in default

    assert (html.index('id="container-decl"') <
            html.index('id="container-default"') <
            html.index('id="container-work"'))


def test_containers_page_unused_warning(manager, container_registry,
                                        config_stub):
    container_registry.add('spare', '#111111')
    container_registry.add('kept', '#222222')
    manager.new_session('keeper', container='kept')
    declare(config_stub, container_registry, {'decl': {'color': '#333333'}})

    html = page(sessionpages.qute_containers)

    assert ('<td class="sessions"><span class="warning">No session uses it. '
            'Delete it with <span class="mono">:container-delete spare</span>'
            '</span></td>') in element(html, 'tr', 'container-spare')
    for name in ['kept', 'decl', 'default']:
        assert 'container-delete' not in element(html, 'tr',
                                                 f'container-{name}')


def test_sessions_page_counts(manager, windows, base_path):
    write_session(base_path, 'closed',
                  windows=[saved_window('a', 'b'), saved_window('c')])
    manager.load_all()
    live = manager.new_session('live')
    open_window(manager, windows, live, 1, tabs=3)
    open_window(manager, windows, live, 2, tabs=1)

    html = page(sessionpages.qute_sessions)

    assert ('<p class="state">closed, 2 windows, 3 tabs, last saved ' in
            element(html, 'section', 'session-closed'))
    assert ('<p class="state">open, 2 windows, 4 tabs, last saved ' in
            element(html, 'section', 'session-live'))
    assert ('<p class="state">closed, 0 windows, 0 tabs, last saved ' in
            element(html, 'section', 'session-default'))
    assert 'class="unreadable"' not in html


def test_sessions_page_container_and_order(manager, container_registry):
    container_registry.add('work', 'red')
    manager.new_session('job', container='work')
    manager.new_session('alpha')
    private = manager.new_private()

    html = page(sessionpages.qute_sessions)

    assert ('<p class="container">Container: work <span class="swatch" '
            'style="background-color: #ff0000"></span></p>' in
            element(html, 'section', 'session-job'))
    assert ('<p class="container">Container: default <span class="swatch" '
            'style="background-color: #3b4252"></span></p>' in
            element(html, 'section', 'session-alpha'))
    assert (html.index('id="session-alpha"') <
            html.index('id="session-default"') <
            html.index('id="session-job"') <
            html.index(f'id="session-{private.name}"'))


def test_sessions_page_last_saved(manager, base_path):
    write_session(base_path, 'old')
    os.utime(base_path / 'old' / 'session.yml', (1790000000, 1790000000))
    manager.load_all()
    fresh = manager.new_session('fresh')
    fresh.last_saved = datetime.datetime(2026, 9, 25, 10, 30, 0)

    html = page(sessionpages.qute_sessions)

    old_time = datetime.datetime.fromtimestamp(1790000000).strftime(
        '%Y-%m-%d %H:%M:%S')
    assert (f'last saved {old_time}</p>' in
            element(html, 'section', 'session-old'))
    assert ('last saved 2026-09-25 10:30:00</p>' in
            element(html, 'section', 'session-fresh'))
    assert 'last saved never</p>' in element(html, 'section', 'session-default')


def test_sessions_page_private_only_while_open(manager, windows):
    private = manager.new_private()
    open_window(manager, windows, private, 1, tabs=2)

    section = element(page(sessionpages.qute_sessions), 'section',
                      f'session-{private.name}')
    assert ('<p class="state">private, in memory and never saved, 1 window, '
            '2 tabs</p>') in section
    assert 'swatch' not in section
    assert 'Container:' not in section

    manager.remove_window(private, 1)
    del windows[1]
    html = page(sessionpages.qute_sessions)
    assert private.name not in html
    assert 'Private sessions' not in html


def test_sessions_page_unreadable(manager, base_path, message_mock, caplog):
    (base_path / 'broken').mkdir(parents=True)
    (base_path / 'broken' / 'session.yml').write_text('windows: [\n',
                                                      encoding='utf-8')
    with caplog.at_level(logging.ERROR):
        manager.load_all()

    html = page(sessionpages.qute_sessions)

    assert f'<li class="mono">{base_path / "broken"}</li>' in html
    assert "These sessions couldn't be read." in html
    assert 'id="session-broken"' not in html


def test_sessions_page_counts_bad_tabs_field(manager, base_path):
    write_session(base_path, 'malformed',
                  windows=[{'tabs': None}, {'tabs': 'x'}, {},
                           {'tabs': [{}, {}]}])
    manager.load_all()

    html = page(sessionpages.qute_sessions)

    assert ('<p class="state">closed, 4 windows, 2 tabs, last saved ' in
            element(html, 'section', 'session-malformed'))


def test_last_saved_permission_error(manager):
    session = manager.new_session('locked')
    session.last_saved = None

    class Unreadable:
        def stat(self):
            raise PermissionError

    manager.path_for = lambda _session: Unreadable()

    assert sessionpages._last_saved(session) == 'never'


def closed_entry(closed_at, *titles):
    return {'closed_at': closed_at, 'window': saved_window(*titles)}


def local_time(when):
    return when.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def test_sessions_page_closed_windows(manager, base_path):
    newest = datetime.datetime(2026, 9, 24, 18, 3, 11,
                               tzinfo=datetime.timezone.utc)
    older = datetime.datetime(2026, 9, 23, 8, 0, 0,
                              tzinfo=datetime.timezone.utc)
    untitled = saved_window('')
    write_session(base_path, 'hist', closed_windows=[
        # Quoted in YAML, so it comes back as a string.
        closed_entry('2026-09-24T18:03:11Z', 'Newest', 'Other'),
        # Unquoted in YAML, so it comes back as a datetime.
        closed_entry(older, 'Older'),
        {'closed_at': '2026-09-22T12:00:00Z', 'window': untitled},
        {'closed_at': 'not a time', 'window': {'tabs': []}},
    ])
    manager.load_all()

    section = element(page(sessionpages.qute_sessions), 'section',
                      'session-hist')

    assert '<p class="history">4 closed windows:</p>' in section
    assert (f'<tr><td class="index">1</td><td class="closed-at">'
            f'{local_time(newest)}</td><td class="tabs">2</td>'
            f'<td class="title">Newest</td><td class="mono">'
            f':session-restore-window hist 1</td></tr>') in section
    assert (f'<tr><td class="index">2</td><td class="closed-at">'
            f'{local_time(older)}</td><td class="tabs">1</td>'
            f'<td class="title">Older</td><td class="mono">'
            f':session-restore-window hist 2</td></tr>') in section
    assert ('<td class="title">http://example.com/0</td><td class="mono">'
            ':session-restore-window hist 3</td>') in section
    assert ('<tr><td class="index">4</td><td class="closed-at">not a time'
            '</td><td class="tabs">0</td><td class="title"></td>') in section


def test_normalize_iso_z():
    # Asserted directly against the helper, not just the rendered page: on
    # Python 3.11+ fromisoformat already accepts a bare 'Z', so a page-level
    # assertion alone would keep passing even if the Z replacement in
    # _closed_at were deleted. This fails either way.
    assert (sessionpages._normalize_iso_z('2026-09-24T18:03:11Z') ==
            '2026-09-24T18:03:11+00:00')
    assert (sessionpages._normalize_iso_z('2026-09-24T18:03:11+02:00') ==
            '2026-09-24T18:03:11+02:00')


def test_closed_at_none_renders_unknown(manager, base_path):
    write_session(base_path, 'nulled', closed_windows=[
        {'closed_at': None, 'window': saved_window('Title')},
    ])
    manager.load_all()

    section = element(page(sessionpages.qute_sessions), 'section',
                      'session-nulled')

    assert '<td class="closed-at">unknown</td>' in section


def test_closed_at_overflow_renders_raw(manager, base_path):
    # A timezone behind UTC pushes year 1 below datetime.MINYEAR when
    # astimezone() converts it to local time, which raises OverflowError.
    original_tz = os.environ.get('TZ')
    os.environ['TZ'] = 'Etc/GMT+12'
    time.tzset()
    try:
        write_session(base_path, 'ancient', closed_windows=[
            {'closed_at': '0001-01-01T00:00:00Z',
             'window': saved_window('Old')},
        ])
        manager.load_all()

        section = element(page(sessionpages.qute_sessions), 'section',
                          'session-ancient')

        assert ('<td class="closed-at">0001-01-01T00:00:00Z</td>'
                in section)
    finally:
        if original_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = original_tz
        time.tzset()


def test_sessions_page_no_closed_windows(manager):
    section = element(page(sessionpages.qute_sessions), 'section',
                      'session-default')
    assert '<p class="history">No closed windows.</p>' in section


def test_sessions_page_escapes_titles(manager, base_path):
    write_session(base_path, 'esc', closed_windows=[
        closed_entry('2026-09-24T18:03:11Z',
                     '<script>alert("x")</script> & co'),
    ])
    manager.load_all()

    html = page(sessionpages.qute_sessions)

    assert '<script>alert' not in html
    assert ('<td class="title">&lt;script&gt;alert(&#34;x&#34;)'
            '&lt;/script&gt; &amp; co</td>') in html


def test_sessions_page_malformed_closed_window_entries(manager, base_path):
    # sessionfile.read requires each closed_windows entry to be a mapping,
    # but not that its 'window' value is one, or that 'window.tabs' is a
    # list: these come from hand-edited files and must not crash the page.
    write_session(base_path, 'weird', closed_windows=[
        {'closed_at': '2026-09-24T18:03:11Z'},
        {'closed_at': '2026-09-24T18:03:11Z', 'window': 'not a mapping'},
        {'closed_at': '2026-09-24T18:03:11Z', 'window': {'tabs': 'nope'}},
        {'closed_at': '2026-09-24T18:03:11Z', 'window': {}},
    ])
    manager.load_all()

    section = element(page(sessionpages.qute_sessions), 'section',
                      'session-weird')

    assert '<p class="history">4 closed windows:</p>' in section
    assert section.count('<td class="tabs">0</td>') == 4


def test_closed_windows_entry_not_a_mapping(manager):
    # session.closed_windows can also be mutated directly in memory, so
    # _closed_windows itself must not assume every entry is a mapping.
    session = manager.new_session('odd')
    session.closed_windows = ['garbage', {'closed_at': 'unknown'}]

    rows = sessionpages._closed_windows(session)

    assert [row.tabs for row in rows] == [0, 0]
    assert [row.closed_at for row in rows] == ['unknown', 'unknown']


@pytest.mark.parametrize('tabs', [
    ['not-a-mapping'],
    [{'history': 'sometext'}],
    [{'history': ['not-a-mapping']}],
    [{'history': []}],
], ids=['tab-not-mapping', 'history-not-list', 'history-item-not-mapping',
       'history-empty'])
def test_sessions_page_closed_window_malformed_history(manager, base_path,
                                                       tabs):
    # tabs/history round-trip through sessionfile.read unvalidated, so a
    # hand-edited file can put anything there; the page must still render.
    write_session(base_path, 'badtab', closed_windows=[
        {'closed_at': '2026-09-24T18:03:11Z', 'window': {'tabs': tabs}},
    ])
    manager.load_all()

    section = element(page(sessionpages.qute_sessions), 'section',
                      'session-badtab')

    assert '<td class="title"></td>' in section


@pytest.mark.parametrize('module, name, url', [
    (sessioncommands, 'session_list', 'qute://sessions/'),
    (containercommands, 'container_list', 'qute://containers/'),
])
@pytest.mark.parametrize('flags', [
    {}, {'tab': True}, {'bg': True}, {'window': True},
])
def test_list_commands_open_page(manager, windows, module, name, url, flags):
    window = open_window(manager, windows, manager.default, 1)

    getattr(module, name)(**flags, win_id=1)

    assert window.dispatcher.opened == [
        (url, {'tab': False, 'bg': False, 'window': False, **flags})]
