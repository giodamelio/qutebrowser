# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import itertools
import logging
import struct

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.api import cmdutils
from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import mainwindow, windowsessions
from qutebrowser.misc import containers, historystore, sessioncommands, sessionfile
from qutebrowser.utils import objreg, qtutils, usertypes


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
def registry(monkeypatch):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: False)
    reg = profiles.ProfileRegistry(factory=FakeProfile,
                                   initializer=lambda _profile: (lambda: None))
    monkeypatch.setattr(profiles, 'registry', reg)
    return reg


class FakeTab:

    def __init__(self, session):
        self.session = session


class FakeTabbedBrowser:

    def __init__(self, session):
        self.session = session
        self._tabs = [FakeTab(session), FakeTab(session)]

    def widgets(self):
        return self._tabs

    def undo(self):
        pass


class FakeWindow:

    def __init__(self, win_id, session):
        self.win_id = win_id
        self.session = session
        self.tabbed_browser = FakeTabbedBrowser(session)

    def show(self):
        pass


@pytest.fixture
def windows(monkeypatch):
    registry = {}
    monkeypatch.setattr(objreg, 'window_registry', registry)
    return registry


@pytest.fixture
def base_path(tmp_path):
    return tmp_path / 'sessions'


@pytest.fixture
def manager(registry, monkeypatch, base_path, state_config, fake_save_manager,
           windows, container_registry):
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id},
        window_history=lambda window: {})
    mgr.load_all()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


def open_window(manager, windows, session, win_id):
    window = FakeWindow(win_id, session)
    windows[win_id] = window
    manager.add_window(session, win_id)
    return window


@pytest.fixture
def fake_mainwindow(monkeypatch, manager, windows):
    win_ids = itertools.count(10)

    def make(*, session, geometry=None):
        return open_window(manager, windows, session, next(win_ids))

    def restore(data, session, *, show=True):
        return make(session=session)

    monkeypatch.setattr(mainwindow, 'MainWindow', make)
    monkeypatch.setattr(sessionfile, 'restore_window', restore)
    return make


def open_list(state_config):
    return state_config['general'].get('open_sessions', '')


def session_file(base_path, name):
    """Get the session.yml of a session directory, creating the directory."""
    directory = base_path / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory / 'session.yml'


def saved(base_path, name):
    return sessionfile.read(session_file(base_path, name)).windows


def test_default_session(manager):
    assert manager.default.name == 'default'
    assert not manager.default.private
    assert manager.default.profile_key == profiles.DEFAULT_KEY


def test_new_private_names(manager, registry):
    first = manager.new_private()
    second = manager.new_private()
    assert (first.name, second.name) == ('private-1', 'private-2')
    assert first.private
    assert first.profile_key == 'private-1'
    assert registry.get('private-1') is not None
    assert manager.private_sessions() == [first, second]


def test_private_closes_with_last_window(manager, registry):
    session = manager.new_private()
    manager.add_window(session, 1)
    manager.add_window(session, 2)

    manager.remove_window(session, 1)
    assert registry.get('private-1') is not None
    assert manager.private_sessions() == [session]

    manager.remove_window(session, 2)
    assert registry.get('private-1') is None
    assert manager.private_sessions() == []


def test_remove_window_twice(manager):
    session = manager.new_private()
    manager.add_window(session, 1)
    manager.remove_window(session, 1)
    manager.remove_window(session, 1)


def test_default_never_closes(manager, registry):
    # The registry never acquired 'default' here, so a release would raise.
    manager.add_window(manager.default, 1)
    assert registry.get('default') is None
    manager.remove_window(manager.default, 1)
    assert registry.get('default') is None
    assert manager.default.windows == set()


def test_add_window_acquires_container_profile(manager, registry, windows,
                                               container_registry):
    container_registry.add('work', '#111111')
    session = manager.new_session('job', container='work')
    assert registry.get('work') is None

    open_window(manager, windows, session, 1)
    assert registry.get('work') is not None


def test_two_sessions_share_one_container_profile(manager, registry, windows,
                                                  container_registry):
    container_registry.add('work', '#111111')
    a = manager.new_session('a', container='work')
    b = manager.new_session('b', container='work')

    open_window(manager, windows, a, 1)
    profile = registry.get('work')
    open_window(manager, windows, b, 2)

    assert registry.get('work') is profile


def test_remove_window_releases_container_profile(manager, registry, windows,
                                                  container_registry):
    container_registry.add('work', '#111111')
    session = manager.new_session('job', container='work')
    # A second window elsewhere, so removing session's window isn't closing
    # the browser's last window.
    open_window(manager, windows, manager.default, 2)
    open_window(manager, windows, session, 1)
    assert registry.is_loaded('work')

    manager.remove_window(session, 1)
    assert not registry.is_loaded('work')


def test_remove_window_keeps_container_as_last_window(
        manager, registry, windows, container_registry):
    container_registry.add('work', '#111111')
    session = manager.new_session('job', container='work')
    open_window(manager, windows, session, 1)

    manager.remove_window(session, 1)
    assert registry.is_loaded('work')


def test_reopening_after_kept_last_window_holds_container_once(
        manager, registry, windows, container_registry):
    container_registry.add('work', '#111111')
    session = manager.new_session('job', container='work')
    # Like a broken first window at startup: removing it looks like closing
    # the browser's last window, so the release is skipped.
    open_window(manager, windows, session, 1)
    manager.remove_window(session, 1)
    open_window(manager, windows, session, 2)
    open_window(manager, windows, manager.default, 3)

    manager.remove_window(session, 2)
    assert not registry.is_loaded('work')


def test_shutdown_leaves_container_profile_for_process_exit(
        manager, registry, windows, container_registry):
    container_registry.add('work', '#111111')
    session = manager.new_session('job', container='work')
    open_window(manager, windows, session, 1)

    manager.shutdown()
    manager.remove_window(session, 1)

    assert registry.is_loaded('work')


def test_new_private_single_process(manager, monkeypatch):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: True)
    with pytest.raises(windowsessions.PrivateUnavailableError):
        manager.new_private()
    assert manager.private_sessions() == []


@pytest.mark.parametrize('name', ['work', 'a', 'aws-prod', 'x_1', '9lives'])
def test_validate_name_valid(name):
    windowsessions.validate_name(name)
    windowsessions.validate_new_name(name)


@pytest.mark.parametrize('name, match', [
    ('', 'Invalid name'),
    ('Work', 'Invalid name'),
    ('_autosave', 'Invalid name'),
    ('-x', 'Invalid name'),
    ('a b', 'Invalid name'),
    ('a/b', 'Invalid name'),
    ('private-1', 'reserved for private sessions'),
])
def test_validate_name_invalid(name, match):
    with pytest.raises(windowsessions.InvalidNameError, match=match):
        windowsessions.validate_name(name)


def test_validate_new_name_rejects_default():
    windowsessions.validate_name('default')
    with pytest.raises(windowsessions.InvalidNameError, match="'default' is reserved"):
        windowsessions.validate_new_name('default')


def test_load_all_reads_session_directories(registry, base_path, state_config,
                                            fake_save_manager, windows,
                                            message_mock, caplog):
    session_file(base_path, 'work').write_text(
        'container: default\nwindows:\n- tabs: []\n')
    session_file(base_path, 'legacy').write_text(
        'windows:\n- private: true\n  tabs: []\n- tabs: []\n')
    session_file(base_path, 'broken').write_text('windows: [\n')
    session_file(base_path, 'gone.broken').write_text('windows: []\n')
    (base_path / 'old.yml').write_text('windows: []\n')
    (base_path / 'before-qt-515').mkdir()
    (base_path / 'before-qt-515' / 'old.yml').write_text('windows: []\n')
    (base_path / 'empty').mkdir()
    mgr = windowsessions.SessionManager(base_path)

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    assert [s.name for s in mgr.sessions()] == ['default', 'legacy', 'work']
    assert mgr.get('legacy').saved_windows == [{'tabs': []}]
    assert mgr.get('default').saved_windows == []
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert msg.text.startswith('Skipping session broken:')
    assert mgr.unreadable_paths() == [base_path / 'broken']


def test_saved_open_names(manager, state_config):
    state_config['general']['open_sessions'] = 'work,default'
    assert manager.saved_open_names() == ['work', 'default']


def test_get_unknown(manager):
    with pytest.raises(windowsessions.UnknownSessionError,
                       match='Session nope not found!'):
        manager.get('nope')


def test_new_session_writes_file(manager, base_path):
    session = manager.new_session('work')
    assert sessionfile.read(session_file(base_path, 'work')) == sessionfile.SessionData()
    assert manager.get('work') is session
    assert not session.is_open


@pytest.mark.parametrize('name, container, exc', [
    ('default', 'default', windowsessions.InvalidNameError),
    ('Work', 'default', windowsessions.InvalidNameError),
    ('work', 'other', windowsessions.UnknownContainerError),
])
def test_new_session_rejects(manager, name, container, exc):
    with pytest.raises(exc):
        manager.new_session(name, container=container)


def test_new_session_exists(manager):
    manager.new_session('work')
    with pytest.raises(windowsessions.SessionExistsError):
        manager.new_session('work')


def test_add_window_records_open_order(manager, windows, state_config,
                                       fake_save_manager):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    open_window(manager, windows, work, 2)
    open_window(manager, windows, work, 3)
    assert open_list(state_config) == 'default,work'
    fake_save_manager.save.assert_called_with('state-config', force=True,
                                              silent=True)
    assert work.dirty


def test_private_sessions_not_in_open_list(manager, windows, state_config):
    open_window(manager, windows, manager.new_private(), 1)
    assert open_list(state_config) == ''


def test_window_closing_last_browser_window_keeps_session(
        manager, windows, state_config, base_path):
    window = open_window(manager, windows, manager.default, 1)
    manager.window_closing(window)
    assert open_list(state_config) == 'default'
    assert saved(base_path, 'default') == [{'win': 1}]


def test_window_closing_last_session_window_closes_session(
        manager, windows, state_config, base_path):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    window = open_window(manager, windows, work, 2)
    manager.window_closing(window)
    assert open_list(state_config) == 'default'
    assert saved(base_path, 'work') == [{'win': 2}]


def test_window_closing_drops_window_from_session(manager, windows,
                                                  state_config, base_path):
    open_window(manager, windows, manager.default, 1)
    window = open_window(manager, windows, manager.default, 2)
    manager.window_closing(window)
    assert saved(base_path, 'default') == [{'win': 1}]
    assert open_list(state_config) == 'default'


def test_window_closing_ignored_after_shutdown(manager, windows, base_path):
    open_window(manager, windows, manager.default, 1)
    window = open_window(manager, windows, manager.default, 2)
    manager.shutdown()
    manager.window_closing(window)
    assert saved(base_path, 'default') == [{'win': 1}, {'win': 2}]


def test_window_closing_private_does_nothing(manager, windows, base_path):
    open_window(manager, windows, manager.default, 1)
    window = open_window(manager, windows, manager.new_private(), 2)
    manager.window_closing(window)
    assert not (base_path / 'private-1').exists()


def test_begin_close(manager, windows, state_config, base_path):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    window = open_window(manager, windows, work, 2)
    open_window(manager, windows, work, 3)

    manager.begin_close(work)
    assert open_list(state_config) == 'default'
    assert saved(base_path, 'work') == [{'win': 2}, {'win': 3}]

    manager.window_closing(window)
    assert saved(base_path, 'work') == [{'win': 2}, {'win': 3}]


def test_save_dirty_skips_sessions_without_windows(manager, base_path):
    work = manager.new_session('work')
    (session_file(base_path, 'work')).unlink()
    work.dirty = True
    manager.save_dirty()
    assert not (session_file(base_path, 'work')).exists()


def test_save_dirty_saves_open_sessions(manager, windows, base_path):
    open_window(manager, windows, manager.default, 1)
    assert manager.default.dirty
    manager.save_dirty()
    assert saved(base_path, 'default') == [{'win': 1}]
    assert not manager.default.dirty
    assert manager.default.last_saved is not None


def test_shutdown_saves_and_stops_marking(manager, windows, base_path):
    open_window(manager, windows, manager.default, 1)
    manager.shutdown()
    assert saved(base_path, 'default') == [{'win': 1}]
    manager.mark_dirty(manager.default)
    assert not manager.default.dirty


def test_delete_session(manager, windows, base_path):
    manager.new_session('work')
    history = base_path / 'work' / 'history'
    history.mkdir(exist_ok=True)
    (history / f"{'a' * 32}.bin").write_bytes(b'history')
    manager.delete_session('work')
    assert not (base_path / 'work').exists()
    with pytest.raises(windowsessions.UnknownSessionError):
        manager.get('work')


def test_delete_session_refused(manager, windows):
    work = manager.new_session('work')
    open_window(manager, windows, work, 1)
    open_window(manager, windows, manager.new_private(), 2)
    for name in ['default', 'work', 'private-1']:
        with pytest.raises(windowsessions.SessionStateError):
            manager.delete_session(name)
    with pytest.raises(windowsessions.UnknownSessionError):
        manager.delete_session('nope')


def test_rename_session(manager, windows, state_config, base_path):
    work = manager.new_session('work')
    open_window(manager, windows, work, 1)
    (base_path / 'work' / 'history').mkdir(exist_ok=True)
    (base_path / 'work' / 'history' / f"{'a' * 32}.bin").write_bytes(b'x')
    manager.rename_session('work', 'play')
    assert (base_path / 'play' / 'session.yml').exists()
    assert not (base_path / 'work').exists()
    assert manager.get('play') is work
    assert work.name == 'play'
    assert open_list(state_config) == 'play'
    assert (base_path / 'play' / 'history' / f"{'a' * 32}.bin").read_bytes() == b'x'


def test_rename_session_refused(manager):
    manager.new_session('work')
    manager.new_session('play')
    with pytest.raises(windowsessions.SessionStateError):
        manager.rename_session('default', 'other')
    with pytest.raises(windowsessions.SessionExistsError):
        manager.rename_session('work', 'play')
    with pytest.raises(windowsessions.InvalidNameError):
        manager.rename_session('work', 'Bad')


def test_move_window_saves_source_without_it(manager, windows, state_config,
                                            base_path):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    moved = open_window(manager, windows, work, 2)
    open_window(manager, windows, work, 3)

    manager.move_window(moved, manager.default)
    assert moved.session is manager.default
    assert moved.tabbed_browser.session is manager.default
    assert all(tab.session is manager.default
               for tab in moved.tabbed_browser.widgets())
    assert manager.default.windows == {1, 2}
    assert saved(base_path, 'work') == [{'win': 3}]

    manager.move_window(windows[3], manager.default)
    assert saved(base_path, 'work') == []
    assert open_list(state_config) == 'default'


def test_load_all_moves_broken_default_aside(base_path, state_config,
                                             fake_save_manager, windows,
                                             message_mock, caplog):
    base_path.mkdir(parents=True)
    (session_file(base_path, 'default')).write_text('windows: [\n')
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id},
        window_history=lambda window: {})

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    broken = session_file(base_path, 'default.broken')
    assert broken.read_text() == 'windows: [\n'
    assert not (session_file(base_path, 'default')).exists()
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert msg.text.startswith('Skipping session default:')
    assert str(broken.parent) in msg.text

    open_window(mgr, windows, mgr.default, 1)
    mgr.save_dirty()
    assert saved(base_path, 'default') == [{'win': 1}]


def test_load_all_reports_default_broken_conflict(base_path, state_config,
                                                  fake_save_manager,
                                                  windows, message_mock,
                                                  caplog):

    base_path.mkdir(parents=True)
    (session_file(base_path, 'default')).write_text('windows: [\n')
    (session_file(base_path, 'default.broken')).write_text('already here\n')
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id},
        window_history=lambda window: {})

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    assert (session_file(base_path, 'default')).read_text() == 'windows: [\n'
    assert (session_file(base_path, 'default.broken')).read_text() == 'already here\n'
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert str(base_path / 'default') in msg.text
    assert str(base_path / 'default.broken') in msg.text

    with pytest.raises(sessionfile.SessionFileError):
        mgr.save(mgr.default)


def test_new_session_refuses_unreadable_file(base_path, message_mock, caplog):
    base_path.mkdir(parents=True)
    (session_file(base_path, 'work')).write_text('windows: [\n')
    mgr = windowsessions.SessionManager(base_path)
    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    with pytest.raises(windowsessions.SessionExistsError, match=r"sessions/work exists but can't be read"):
        mgr.new_session('work')
    assert (session_file(base_path, 'work')).read_text() == 'windows: [\n'


def test_rename_session_refuses_unreadable_file(base_path, message_mock,
                                                caplog, container_registry):
    base_path.mkdir(parents=True)
    (session_file(base_path, 'work')).write_text('windows: [\n')
    mgr = windowsessions.SessionManager(base_path)
    with caplog.at_level(logging.ERROR):
        mgr.load_all()
    other = mgr.new_session('other')

    with pytest.raises(windowsessions.SessionExistsError, match=r"sessions/work exists but can't be read"):
        mgr.rename_session('other', 'work')
    assert (session_file(base_path, 'work')).read_text() == 'windows: [\n'
    assert mgr.get('other') is other


def make_debouncer(calls):
    return windowsessions._Debouncer(lambda: calls.append(1),
                                     quiet_ms=1500, max_wait_ms=5000)


def test_debouncer_fires_after_quiet_period():
    calls = []
    debouncer = make_debouncer(calls)
    debouncer.trigger()
    debouncer.trigger()
    assert debouncer._quiet.isActive()
    assert debouncer._max_wait.isActive()
    assert debouncer._quiet.interval() == 1500
    assert debouncer._max_wait.interval() == 5000

    debouncer._quiet.timeout.emit()
    assert calls == [1]
    assert not debouncer._max_wait.isActive()


def test_debouncer_fires_at_max_wait():
    calls = []
    debouncer = make_debouncer(calls)
    debouncer.trigger()
    debouncer._max_wait.timeout.emit()
    assert calls == [1]
    assert not debouncer._quiet.isActive()


def test_debouncer_does_not_restart_max_wait(mocker):
    debouncer = make_debouncer([])
    debouncer.trigger()
    start = mocker.patch.object(debouncer._max_wait, 'start')
    debouncer.trigger()
    start.assert_not_called()


def test_mark_dirty_autosaves(manager, windows, base_path):
    open_window(manager, windows, manager.default, 1)
    manager._autosave._quiet.timeout.emit()
    assert saved(base_path, 'default') == [{'win': 1}]


def test_shutdown_cancels_autosave(manager, windows):
    open_window(manager, windows, manager.default, 1)
    manager.shutdown()
    assert not manager._autosave._quiet.isActive()
    assert not manager._autosave._max_wait.isActive()


def test_new_window_restores_closed_default(manager, windows, base_path,
                                            fake_mainwindow):
    open_window(manager, windows, manager.new_session('work'), 1)
    manager.default.saved_windows = [{'win': 'a'}, {'win': 'b'}]

    window = mainwindow.get_window(via_ipc=True, target='window')
    manager.save_dirty()

    assert window.session is manager.default
    assert saved(base_path, 'default') == [{'win': 10}, {'win': 11},
                                           {'win': 12}]


@pytest.fixture
def work_with_bad_window(manager, base_path, monkeypatch, fake_mainwindow):
    work = manager.new_session('work')
    work.saved_windows = [{'win': 'a'}, {'bad': True}]
    sessionfile.write(session_file(base_path, 'work'),
                      sessionfile.SessionData(windows=work.saved_windows))

    def restore(data, session, *, show=True):
        if data.get('bad'):
            raise sessionfile.SessionFileError('bad window')
        return fake_mainwindow(session=session)

    monkeypatch.setattr(sessionfile, 'restore_window', restore)
    return work


def test_open_session_moves_file_aside_when_a_window_fails(
        manager, base_path, work_with_bad_window, message_mock, caplog):
    original = (session_file(base_path, 'work')).read_text()
    with caplog.at_level(logging.ERROR):
        sessioncommands.open_session(work_with_bad_window)

    broken = session_file(base_path, 'work.broken')
    assert broken.read_text() == original
    assert any(str(broken.parent) in msg.text for msg in message_mock.messages)

    manager.save_dirty()
    assert saved(base_path, 'work') == [{'win': 10}]
    assert broken.read_text() == original


def test_open_session_refuses_saving_when_broken_file_exists(
        manager, base_path, work_with_bad_window, message_mock, caplog):
    original = (session_file(base_path, 'work')).read_text()
    (session_file(base_path, 'work.broken')).write_text('already here\n')
    with caplog.at_level(logging.ERROR):
        sessioncommands.open_session(work_with_bad_window)
        manager.save_dirty()

    assert (session_file(base_path, 'work')).read_text() == original
    assert (session_file(base_path, 'work.broken')).read_text() == 'already here\n'


def test_resume_after_shutdown(manager, windows, base_path):
    open_window(manager, windows, manager.default, 1)
    manager.shutdown()
    manager.resume()

    open_window(manager, windows, manager.default, 2)
    assert manager.default.dirty
    manager._autosave._quiet.timeout.emit()
    assert saved(base_path, 'default') == [{'win': 1}, {'win': 2}]


def test_resume_without_shutdown(manager, windows, base_path):
    manager.resume()
    open_window(manager, windows, manager.default, 1)
    manager.save_dirty()
    assert saved(base_path, 'default') == [{'win': 1}]


def test_move_window_saves_target(manager, windows, base_path):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    moved = open_window(manager, windows, work, 2)
    open_window(manager, windows, work, 3)

    manager.move_window(moved, manager.default)
    assert saved(base_path, 'default') == [{'win': 1}, {'win': 2}]


def test_move_window_releases_source_container(manager, registry, windows,
                                               container_registry):
    container_registry.add('work', '#111111')
    source = manager.new_session('source', container='work')
    target = manager.new_session('target', container='work')
    # A second window elsewhere, so removing the moved window below isn't
    # closing the browser's last window.
    open_window(manager, windows, manager.default, 2)
    moved = open_window(manager, windows, source, 1)

    manager.move_window(moved, target)
    assert registry.is_loaded('work')

    manager.remove_window(target, 1)
    assert not registry.is_loaded('work')


def test_move_window_aborts_when_source_save_fails(manager, windows,
                                                  base_path):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    moved = open_window(manager, windows, work, 2)
    manager._unreadable.add('work')

    with pytest.raises(sessionfile.SessionFileError):
        manager.move_window(moved, manager.default)
    assert moved.session is work
    assert work.windows == {2}
    assert manager.default.windows == {1}


def test_session_move_window_reports_failed_save(manager, windows):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    moved = open_window(manager, windows, work, 2)
    manager._unreadable.add('work')

    with pytest.raises(cmdutils.CommandError, match='work'):
        sessioncommands.session_move_window('default', win_id=2)
    assert moved.session is work


def test_load_all_warns_about_invalid_names(base_path, message_mock, caplog):
    session_file(base_path, 'Work').write_text('windows: []\n')
    mgr = windowsessions.SessionManager(base_path)

    with caplog.at_level(logging.WARNING):
        mgr.load_all()

    assert [s.name for s in mgr.sessions()] == ['default']
    msg = message_mock.getmsg(usertypes.MessageLevel.warning)
    assert str(base_path / 'Work') in msg.text


def test_new_session_with_container(manager, container_registry, base_path):
    container_registry.add('work', '#111111')
    session = manager.new_session('job', container='work')
    assert session.container == 'work'
    assert session.profile_key == 'work'
    assert sessionfile.read(session_file(base_path, 'job')).container == 'work'


def test_new_session_unknown_container(manager):
    with pytest.raises(windowsessions.UnknownContainerError,
                       match="Unknown container 'nope', create it with "
                             ":container-new nope"):
        manager.new_session('job', container='nope')


def test_load_all_skips_invalid_container(registry, base_path, state_config,
                                          fake_save_manager, windows,
                                          container_registry, message_mock,
                                          caplog):
    base_path.mkdir(parents=True)
    (session_file(base_path, 'job')).write_text('container: Bad\nwindows: []\n')
    mgr = windowsessions.SessionManager(base_path)

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    assert [s.name for s in mgr.sessions()] == ['default']
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert msg.text.startswith('Skipping session job:')
    with pytest.raises(windowsessions.SessionExistsError):
        mgr.new_session('job')
    assert (session_file(base_path, 'job')).read_text() == 'container: Bad\nwindows: []\n'


def test_adopt_containers_at_load(registry, base_path, state_config,
                                  fake_save_manager, windows,
                                  container_registry):
    base_path.mkdir(parents=True)
    (session_file(base_path, 'job')).write_text('container: gone\nwindows: []\n')
    (session_file(base_path, 'side')).write_text('container: gone\nwindows: []\n')
    mgr = windowsessions.SessionManager(base_path)
    mgr.load_all()
    mgr.adopt_containers()
    assert container_registry.get('gone') == containers.Container(
        'gone', containers.FALLBACK_COLOR, 'runtime')


def test_adopt_after_declared_container_removed(manager, windows,
                                                container_registry,
                                                config_stub):
    container_registry.merged.connect(manager.adopt_containers)
    config_stub.val.containers = {'decl': {'color': 'red'}}
    container_registry.on_config_changed('containers')
    session = manager.new_session('job', container='decl')
    open_window(manager, windows, session, 1)

    config_stub.val.containers = {}
    container_registry.on_config_changed('containers')

    assert container_registry.get('decl').source == 'runtime'
    assert session.is_open


def test_sessions_using(manager, container_registry):
    container_registry.add('work', '#111111')
    manager.new_session('b', container='work')
    manager.new_session('a', container='work')
    manager.new_session('c')
    manager.new_private()
    assert [s.name for s in manager.sessions_using('work')] == ['a', 'b']
    assert [s.name for s in manager.sessions_using('default')] == ['c', 'default']


def test_rename_container(manager, container_registry, base_path):
    container_registry.add('old', '#111111')
    manager.new_session('a', container='old')
    manager.new_session('c')

    assert manager.rename_container('old', 'new') == []

    assert manager.get('a').container == 'new'
    assert sessionfile.read(session_file(base_path, 'a')).container == 'new'
    assert manager.get('c').container == 'default'


def test_rename_container_write_failure(manager, container_registry,
                                        monkeypatch):
    container_registry.add('old', '#111111')
    manager.new_session('a', container='old')

    def fail(*_args, **_kwargs):
        raise sessionfile.SessionFileError('disk full')

    monkeypatch.setattr(sessionfile, 'write', fail)
    assert manager.rename_container('old', 'new') == [('a', 'disk full')]
    assert manager.get('a').container == 'old'


def test_window_closing_last_regular_window_with_private_open(
        manager, windows, state_config, base_path):
    window = open_window(manager, windows, manager.default, 1)
    open_window(manager, windows, manager.new_private(), 2)
    manager.window_closing(window)
    assert open_list(state_config) == 'default'
    assert saved(base_path, 'default') == [{'win': 1}]


def test_window_closing_session_last_window_with_private_open(
        manager, windows, state_config, base_path):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    window = open_window(manager, windows, work, 2)
    open_window(manager, windows, manager.new_private(), 3)
    manager.window_closing(window)
    assert open_list(state_config) == 'default'
    assert saved(base_path, 'work') == [{'win': 2}]


def test_check_same_profile(manager, container_registry):
    container_registry.add('work', '#111111')
    job = manager.new_session('job', container='work')
    side = manager.new_session('side', container='work')
    first = manager.new_private()
    second = manager.new_private()

    windowsessions.check_same_profile(job, side)
    windowsessions.check_same_profile(first, first)

    with pytest.raises(
            windowsessions.ProfileMismatchError,
            match=r"Can't move tabs from session default \(container "
                  r"default\) to session job \(container work\)"):
        windowsessions.check_same_profile(manager.default, job)
    with pytest.raises(
            windowsessions.ProfileMismatchError,
            match=r"Can't move tabs from session private-1 \(private\) to "
                  r"session private-2 \(private\)"):
        windowsessions.check_same_profile(first, second)
    with pytest.raises(windowsessions.ProfileMismatchError,
                       match=r"to session default \(container default\)"):
        windowsessions.check_same_profile(first, manager.default)


@pytest.mark.parametrize('color, expected', [
    ('#2e7d32', ('#2e7d32', '#ffffff')),
    ('yellow', ('#ffff00', '#000000')),
    ('rgb(255, 255, 255)', ('#ffffff', '#000000')),
    ('#808080', ('#808080', '#000000')),
])
def test_session_colors_container(container_registry, color, expected):
    container_registry.add('shop', color)
    session = windowsessions.Session('work', private=False, container='shop')
    assert windowsessions.session_colors(session) == expected


def test_session_colors_default(container_registry):
    session = windowsessions.Session('default', private=False)
    assert windowsessions.session_colors(session) == ('#3b4252', '#ffffff')


def test_session_colors_declared_default(container_registry, config_stub):
    config_stub.val.containers = {'default': {'color': 'silver'}}
    container_registry.on_config_changed('containers')
    session = windowsessions.Session('default', private=False)
    assert windowsessions.session_colors(session) == ('#c0c0c0', '#000000')


def test_session_colors_private(container_registry, config_stub):
    session = windowsessions.Session('private-1', private=True)
    assert windowsessions.session_colors(session) == ('#666666', '#ffffff')
    config_stub.val.colors.statusbar.private_session = 'white'
    assert windowsessions.session_colors(session) == ('#ffffff', '#000000')


def test_rename_session_notifies(manager, qtbot):
    manager.new_session('work')
    with qtbot.wait_signal(windowsessions.notifier.changed):
        manager.rename_session('work', 'play')
    with qtbot.assert_not_emitted(windowsessions.notifier.changed):
        with pytest.raises(windowsessions.SessionStateError):
            manager.rename_session('default', 'other')


def test_move_window_notifies(manager, windows, qtbot):
    work = manager.new_session('work')
    open_window(manager, windows, manager.default, 1)
    moved = open_window(manager, windows, work, 2)
    with qtbot.wait_signal(windowsessions.notifier.changed):
        manager.move_window(moved, manager.default)


def test_notifier_follows_colors(manager, container_registry, config_stub,
                                 qtbot):
    windowsessions.init()
    with qtbot.wait_signal(windowsessions.notifier.changed):
        config_stub.val.colors.statusbar.private_session = '#123456'
    with qtbot.wait_signal(windowsessions.notifier.changed):
        container_registry.add('shop', '#2e7d32')
    with qtbot.assert_not_emitted(windowsessions.notifier.changed):
        config_stub.val.colors.hints.fg = 'red'


def test_colors_after_declared_container_removed(manager, container_registry,
                                                 config_stub):
    windowsessions.init()
    config_stub.val.containers = {'decl': {'color': 'red'}}
    container_registry.on_config_changed('containers')
    session = windowsessions.manager.new_session('job', container='decl')
    seen = []

    def on_changed():
        seen.append(windowsessions.session_colors(session))

    windowsessions.notifier.changed.connect(on_changed)
    try:
        config_stub.val.containers = {}
        container_registry.on_config_changed('containers')
    finally:
        windowsessions.notifier.changed.disconnect(on_changed)

    assert seen
    assert seen[-1] == (containers.FALLBACK_COLOR, '#000000')


def test_session_close_unlists_session_without_windows(manager, windows,
                                                       state_config):
    regular = open_window(manager, windows, manager.default, 1)
    open_window(manager, windows, manager.new_private(), 2)
    manager.window_closing(regular)
    manager.remove_window(manager.default, 1)
    assert open_list(state_config) == 'default'

    sessioncommands.session_close('default')
    assert open_list(state_config) == ''

    with pytest.raises(cmdutils.CommandError,
                       match='Session default is not open'):
        sessioncommands.session_close('default')


ID_A = 'a' * 32
ID_B = 'b' * 32


def serialize_with_ids(window):
    return {'win': window.win_id,
            'tabs': [{'id': tab_id, 'history': []} for tab_id in window.history]}


@pytest.fixture
def history_manager(registry, monkeypatch, base_path, state_config,
                    fake_save_manager, windows, container_registry):
    monkeypatch.setattr(historystore, 'running_version', lambda: '6.11.2')
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=serialize_with_ids,
        window_history=lambda window: dict(window.history))
    mgr.load_all()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


def history_window(manager, windows, session, win_id, history):
    window = FakeWindow(win_id, session)
    window.history = history
    windows[win_id] = window
    manager.add_window(session, win_id)
    return window


def history_files(base_path, name):
    return sorted(path.name for path in (base_path / name / 'history').iterdir())


def count_writes(monkeypatch):
    written = []
    real_write = historystore._write

    def write(path, data):
        written.append(path.name)
        real_write(path, data)

    monkeypatch.setattr(historystore, '_write', write)
    return written


def test_save_writes_history_files(history_manager, windows, base_path):
    default = history_manager.default
    history_window(history_manager, windows, default, 1, {ID_A: b'history a'})
    history_manager.save(default)
    assert history_files(base_path, 'default') == [f'{ID_A}.bin']
    assert historystore.read(base_path / 'default' / 'history',
                             ID_A) == b'history a'
    assert saved(base_path, 'default') == [
        {'win': 1, 'tabs': [{'id': ID_A, 'history': []}]}]


def test_save_skips_unchanged_history(history_manager, windows, monkeypatch):
    default = history_manager.default
    window = history_window(history_manager, windows, default, 1,
                            {ID_A: b'a', ID_B: b'b'})
    history_manager.save(default)
    written = count_writes(monkeypatch)
    window.history[ID_B] = b'b changed'
    history_manager.save(default)
    assert written == [f'{ID_B}.bin']


def test_failed_history_write_keeps_session_yml(history_manager, windows,
                                                base_path, monkeypatch):
    default = history_manager.default
    window = history_window(history_manager, windows, default, 1, {ID_A: b'a'})
    history_manager.save(default)
    before = session_file(base_path, 'default').read_text()
    window.history = {ID_A: b'a', ID_B: b'b'}

    def fail(path, data):
        raise OSError('disk full')

    monkeypatch.setattr(historystore, '_write', fail)
    with pytest.raises(sessionfile.SessionFileError, match='disk full'):
        history_manager.save(default)
    assert session_file(base_path, 'default').read_text() == before
    assert history_files(base_path, 'default') == [f'{ID_A}.bin']


def test_save_removes_unreferenced_history(history_manager, windows,
                                           base_path):
    default = history_manager.default
    window = history_window(history_manager, windows, default, 1,
                            {ID_A: b'a', ID_B: b'b'})
    history_manager.save(default)
    del window.history[ID_B]
    history_manager.save(default)
    assert history_files(base_path, 'default') == [f'{ID_A}.bin']
    assert set(default.history_digests) == {ID_A}


def test_startup_removes_history_files_nothing_refers_to(
        history_manager, windows, base_path):
    default = history_manager.default
    history_window(history_manager, windows, default, 1, {ID_A: b'a'})
    history_manager.save(default)
    # A crash after writing history files and before session.yml.
    history_dir = base_path / 'default' / 'history'
    historystore.write_changed(history_dir, {ID_B: b'never referenced'}, {})
    (history_dir / f'{ID_A}.bin.tmp').write_bytes(b'partial')

    fresh = windowsessions.SessionManager(base_path)
    fresh.load_all()

    assert history_files(base_path, 'default') == [f'{ID_A}.bin']
    assert fresh.default.saved_windows == [
        {'win': 1, 'tabs': [{'id': ID_A, 'history': []}]}]


@pytest.mark.parametrize('content', [
    f'windows:\n- tabs:\n    first: {{id: {ID_A}, history: []}}\n',
    'windows: []\nclosed_windows:\n'
    f'- window: {{tabs: [], closed_tabs: {{x: [{{id: {ID_A}}}]}}}}\n',
    'windows: []\nclosed_windows:\n- window: junk\n',
], ids=['tabs', 'closed_tabs', 'closed_window'])
def test_startup_keeps_files_it_cannot_account_for(
        history_manager, base_path, content, caplog):
    """A hand-edited session.yml must reach .broken/ with its files."""
    session_file(base_path, 'work').write_text(content)
    history_dir = base_path / 'work' / 'history'
    history_dir.mkdir()
    historystore.write_changed(history_dir, {ID_A: b'a'}, {})

    fresh = windowsessions.SessionManager(base_path)
    with caplog.at_level(logging.DEBUG):
        fresh.load_all()

    assert history_files(base_path, 'work') == [f'{ID_A}.bin']
    assert 'Keeping every history file of session work' in caplog.text


def test_closed_window_files_live_while_it_is_kept(history_manager, windows,
                                                   base_path):
    default = history_manager.default
    history_window(history_manager, windows, default, 1, {ID_A: b'a'})
    default.closed_windows = [{
        'closed_at': '2026-09-25T10:00:00.000+00:00',
        'window': {'tabs': [{'id': ID_B, 'history': []}]},
    }]
    default.held_history = {ID_B: b'closed window'}
    history_manager.save(default)
    assert history_files(base_path, 'default') == [f'{ID_A}.bin', f'{ID_B}.bin']
    assert default.held_history == {}

    default.closed_windows = []
    history_manager.save(default)
    assert history_files(base_path, 'default') == [f'{ID_A}.bin']


def test_move_window_moves_history_files(history_manager, windows, base_path):
    work = history_manager.new_session('work')
    history_window(history_manager, windows, work, 1, {ID_A: b'stays'})
    moving = history_window(history_manager, windows, work, 2,
                            {ID_B: b'moves'})
    history_manager.save(work)
    play = history_manager.new_session('play')

    history_manager.move_window(moving, play)

    assert history_files(base_path, 'work') == [f'{ID_A}.bin']
    assert history_files(base_path, 'play') == [f'{ID_B}.bin']
    assert historystore.read(base_path / 'play' / 'history', ID_B) == b'moves'


def test_move_aside_forgets_history_digests(history_manager, base_path,
                                            message_mock, caplog):
    work = history_manager.new_session('work')
    work.history_digests = {ID_A: historystore.digest(b'a')}
    with caplog.at_level(logging.ERROR):
        history_manager.move_aside(work)
    assert work.history_digests == {}
    assert (base_path / 'work.broken' / 'session.yml').exists()


def test_open_session_moves_aside_after_every_window(
        history_manager, base_path, monkeypatch, message_mock, caplog):
    """A window QtWebEngine refuses doesn't cost later windows their history.

    Moving the directory aside mid-loop would take their files with it.
    """
    work = history_manager.new_session('work')
    work.saved_windows = [{'tabs': [{'id': ID_A}]}, {'tabs': [{'id': ID_B}]}]
    history_dir = base_path / 'work' / 'history'
    historystore.write_changed(
        history_dir, {ID_A: b'refused', ID_B: b'good'}, {})
    restored = []

    def restore(data, session, *, show=True):
        [tab] = data['tabs']
        problems: list[str] = []
        history = sessionfile._read_history(session, tab['id'], problems)
        if history == b'refused':
            raise sessionfile.SessionFileError(
                "Session work has an invalid window: OSError: refused")
        restored.append((history, problems))
        return object()

    moved_after = []
    real_move_aside = history_manager.move_aside

    def move_aside(session):
        moved_after.append(list(restored))
        real_move_aside(session)

    monkeypatch.setattr(sessionfile, 'restore_window', restore)
    monkeypatch.setattr(history_manager, 'move_aside', move_aside)
    with caplog.at_level(logging.ERROR):
        sessioncommands.open_session(work)

    assert restored == [(b'good', [])]
    assert moved_after == [[(b'good', [])]]
    assert (base_path / 'work.broken' / 'session.yml').exists()


class _StubTabData:

    def __init__(self, persistent_id):
        self.persistent_id = persistent_id


class _StubTab:

    def __init__(self, persistent_id):
        self.data = _StubTabData(persistent_id)


class _StubTabbedBrowser:

    def __init__(self, tabs):
        self._tabs = tabs
        self.undo_stack = []

    def widgets(self):
        return self._tabs


def test_save_keeps_file_when_tab_history_looks_empty(
        registry, monkeypatch, base_path, state_config, fake_save_manager,
        windows, container_registry):
    """QTBUG-117489: a count-0 stub must never overwrite a real file."""
    monkeypatch.setattr(historystore, 'running_version', lambda: '6.11.2')
    manager = windowsessions.SessionManager(
        base_path, serialize_window=serialize_with_ids,
        window_history=sessionfile.window_history)
    manager.load_all()
    monkeypatch.setattr(windowsessions, 'manager', manager)
    default = manager.default

    window = FakeWindow(1, default)
    window.history = {ID_A: None}
    window.tabbed_browser = _StubTabbedBrowser([_StubTab(ID_A)])
    windows[1] = window
    manager.add_window(default, 1)

    real = struct.pack('>IIi', 4, 2, 0) + b'real history'
    monkeypatch.setattr(sessionfile, 'tab_history', lambda tab: real)
    manager.save(default)
    path = base_path / 'default' / 'history' / f'{ID_A}.bin'
    before = path.read_bytes()

    stub = struct.pack('>IIi', 4, 0, -1)
    monkeypatch.setattr(sessionfile, 'tab_history', lambda tab: stub)
    manager.save(default)

    assert path.read_bytes() == before
    assert history_files(base_path, 'default') == [f'{ID_A}.bin']


def test_read_history_prefers_held_bytes(history_manager):
    default = history_manager.default
    default.held_history[ID_A] = b'held'
    assert history_manager.read_history(default, ID_A) == b'held'


def test_read_history_remembers_the_file(history_manager, windows, base_path,
                                         monkeypatch):
    default = history_manager.default
    history_dir = base_path / 'default' / 'history'
    history_dir.mkdir(parents=True)
    historystore.write_changed(history_dir, {ID_A: b'on disk'}, {})

    assert history_manager.read_history(default, ID_A) == b'on disk'
    assert default.history_digests == {ID_A: historystore.digest(b'on disk')}

    written = count_writes(monkeypatch)
    history_window(history_manager, windows, default, 1, {ID_A: b'on disk'})
    history_manager.save(default)
    assert written == []


def test_read_history_missing(history_manager):
    with pytest.raises(historystore.UnusableHistoryError, match='missing'):
        history_manager.read_history(history_manager.default, ID_A)
