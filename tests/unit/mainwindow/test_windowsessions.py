# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import collections
import itertools
import logging
import types

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.api import cmdutils
from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import mainwindow, windowsessions, windowundo
from qutebrowser.misc import sessioncommands, sessionfile
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
           windows):
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id})
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


def saved(base_path, name):
    return sessionfile.read(base_path / f'{name}.yml').windows


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


def test_default_never_closes(manager):
    # The registry never acquired 'default' here, so a release would raise.
    manager.add_window(manager.default, 1)
    manager.remove_window(manager.default, 1)
    assert manager.default.windows == set()


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


def test_load_all_tolerates_upstream_files(registry, base_path, state_config,
                                           fake_save_manager, windows,
                                           message_mock, caplog):
    (base_path / 'before-qt-515').mkdir(parents=True)
    (base_path / 'before-qt-515' / 'old.yml').write_text('windows: []\n')
    (base_path / '_autosave.yml').write_text('windows: []\n')
    (base_path / 'work.yml').write_text('container: default\nwindows:\n- tabs: []\n')
    (base_path / 'legacy.yml').write_text(
        'windows:\n- private: true\n  tabs: []\n- tabs: []\n')
    (base_path / 'broken.yml').write_text('windows: [\n')
    mgr = windowsessions.SessionManager(base_path)

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    assert [s.name for s in mgr.sessions()] == ['default', 'legacy', 'work']
    assert mgr.get('legacy').saved_windows == [{'tabs': []}]
    assert mgr.get('default').saved_windows == []
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert msg.text.startswith('Skipping session broken:')


def test_saved_open_names(manager, state_config):
    state_config['general']['open_sessions'] = 'work,default'
    assert manager.saved_open_names() == ['work', 'default']


def test_get_unknown(manager):
    with pytest.raises(windowsessions.UnknownSessionError,
                       match='Session nope not found!'):
        manager.get('nope')


def test_new_session_writes_file(manager, base_path):
    session = manager.new_session('work')
    assert sessionfile.read(base_path / 'work.yml') == sessionfile.SessionData()
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
    assert not (base_path / 'private-1.yml').exists()


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
    (base_path / 'work.yml').unlink()
    work.dirty = True
    manager.save_dirty()
    assert not (base_path / 'work.yml').exists()


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
    manager.delete_session('work')
    assert not (base_path / 'work.yml').exists()
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
    manager.rename_session('work', 'play')
    assert (base_path / 'play.yml').exists()
    assert not (base_path / 'work.yml').exists()
    assert manager.get('play') is work
    assert work.name == 'play'
    assert open_list(state_config) == 'play'


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
    (base_path / 'default.yml').write_text('windows: [\n')
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id})

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    broken = base_path / 'default.yml.broken'
    assert broken.read_text() == 'windows: [\n'
    assert not (base_path / 'default.yml').exists()
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert msg.text.startswith('Skipping session default:')
    assert str(broken) in msg.text

    open_window(mgr, windows, mgr.default, 1)
    mgr.save_dirty()
    assert saved(base_path, 'default') == [{'win': 1}]


def test_load_all_reports_default_broken_conflict(base_path, state_config,
                                                  fake_save_manager,
                                                  windows, message_mock,
                                                  caplog):

    base_path.mkdir(parents=True)
    (base_path / 'default.yml').write_text('windows: [\n')
    (base_path / 'default.yml.broken').write_text('already here\n')
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id})

    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    assert (base_path / 'default.yml').read_text() == 'windows: [\n'
    assert (base_path / 'default.yml.broken').read_text() == 'already here\n'
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert 'default.yml' in msg.text
    assert 'default.yml.broken' in msg.text

    with pytest.raises(sessionfile.SessionFileError):
        mgr.save(mgr.default)


def test_new_session_refuses_unreadable_file(base_path, message_mock, caplog):
    base_path.mkdir(parents=True)
    (base_path / 'work.yml').write_text('windows: [\n')
    mgr = windowsessions.SessionManager(base_path)
    with caplog.at_level(logging.ERROR):
        mgr.load_all()

    with pytest.raises(windowsessions.SessionExistsError, match=r'work\.yml'):
        mgr.new_session('work')
    assert (base_path / 'work.yml').read_text() == 'windows: [\n'


def test_rename_session_refuses_unreadable_file(base_path, message_mock,
                                                caplog):
    base_path.mkdir(parents=True)
    (base_path / 'work.yml').write_text('windows: [\n')
    mgr = windowsessions.SessionManager(base_path)
    with caplog.at_level(logging.ERROR):
        mgr.load_all()
    other = mgr.new_session('other')

    with pytest.raises(windowsessions.SessionExistsError, match=r'work\.yml'):
        mgr.rename_session('other', 'work')
    assert (base_path / 'work.yml').read_text() == 'windows: [\n'
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


def test_undo_window_restores_closed_default(manager, windows, base_path,
                                             fake_mainwindow):
    open_window(manager, windows, manager.new_session('work'), 1)
    manager.default.saved_windows = [{'win': 'a'}]
    undo_manager = types.SimpleNamespace(_undos=collections.deque([
        windowundo._WindowUndoEntry(geometry=None, tab_stack=[])]))

    windowundo.WindowUndoManager.undo_last_window_close(undo_manager)
    manager.save_dirty()

    assert saved(base_path, 'default') == [{'win': 10}, {'win': 11}]


@pytest.fixture
def work_with_bad_window(manager, base_path, monkeypatch, fake_mainwindow):
    work = manager.new_session('work')
    work.saved_windows = [{'win': 'a'}, {'bad': True}]
    sessionfile.write(base_path / 'work.yml',
                      sessionfile.SessionData(windows=work.saved_windows))

    def restore(data, session, *, show=True):
        if data.get('bad'):
            raise sessionfile.SessionFileError('bad window')
        return fake_mainwindow(session=session)

    monkeypatch.setattr(sessionfile, 'restore_window', restore)
    return work


def test_open_session_moves_file_aside_when_a_window_fails(
        manager, base_path, work_with_bad_window, message_mock, caplog):
    original = (base_path / 'work.yml').read_text()
    with caplog.at_level(logging.ERROR):
        sessioncommands.open_session(work_with_bad_window)

    broken = base_path / 'work.yml.broken'
    assert broken.read_text() == original
    assert any(str(broken) in msg.text for msg in message_mock.messages)

    manager.save_dirty()
    assert saved(base_path, 'work') == [{'win': 10}]
    assert broken.read_text() == original


def test_open_session_refuses_saving_when_broken_file_exists(
        manager, base_path, work_with_bad_window, message_mock, caplog):
    original = (base_path / 'work.yml').read_text()
    (base_path / 'work.yml.broken').write_text('already here\n')
    with caplog.at_level(logging.ERROR):
        sessioncommands.open_session(work_with_bad_window)
        manager.save_dirty()

    assert (base_path / 'work.yml').read_text() == original
    assert (base_path / 'work.yml.broken').read_text() == 'already here\n'


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
