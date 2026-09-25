# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import datetime

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import prompt, windowsessions
from qutebrowser.misc import closedwindows, sessioncommands, sessionfile
from qutebrowser.utils import message, objreg, qtutils, usertypes


CloseChoice = closedwindows.CloseChoice


class FakeProfile:

    def __init__(self, key, private):
        self.key = key
        self.private = private

    def deleteLater(self):
        pass


class FakeWindow:

    def __init__(self, win_id, session):
        self.win_id = win_id
        self.session = session
        self.close_choice = None
        self.closed = False

    def close(self):
        self.closed = True


class FakeAsk:

    """Stands in for the blocking message.ask."""

    def __init__(self):
        self.calls = []
        self.answer = 'window'

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.answer


class FakePromptQueue:

    """Stands in for prompt.prompt_queue, only set up for a real window."""

    def __init__(self):
        self.aborted_win_ids = []

    def abort_window(self, win_id):
        self.aborted_win_ids.append(win_id)


@pytest.fixture(autouse=True)
def fake_timers(monkeypatch, stubs):
    monkeypatch.setattr(windowsessions.usertypes, 'Timer', stubs.FakeTimer)


@pytest.fixture(autouse=True)
def fake_prompt_queue(monkeypatch):
    """close_session() cancels a closing window's own prompt via this."""
    queue = FakePromptQueue()
    monkeypatch.setattr(prompt, 'prompt_queue', queue)
    return queue


@pytest.fixture
def windows(monkeypatch):
    registry = {}
    monkeypatch.setattr(objreg, 'window_registry', registry)
    return registry


@pytest.fixture
def manager(monkeypatch, tmp_path, windows, container_registry, state_config,
            fake_save_manager, config_stub):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: False)
    monkeypatch.setattr(profiles, 'registry', profiles.ProfileRegistry(
        factory=FakeProfile, initializer=lambda _profile: (lambda: None)))
    mgr = windowsessions.SessionManager(
        tmp_path / 'sessions',
        serialize_window=lambda window: {'win': window.win_id})
    mgr.load_all()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    monkeypatch.setattr(sessionfile, 'serialize_window',
                        lambda window: {'win': window.win_id})
    return mgr


@pytest.fixture
def fake_ask(monkeypatch):
    ask = FakeAsk()
    monkeypatch.setattr(message, 'ask', ask)
    return ask


def open_window(manager, windows, session, win_id):
    window = FakeWindow(win_id, session)
    windows[win_id] = window
    manager.add_window(session, win_id)
    return window


def two_windows(manager, windows, session):
    return (open_window(manager, windows, session, 1),
            open_window(manager, windows, session, 2))


@pytest.mark.parametrize('preset', [
    CloseChoice.plain, CloseChoice.session, CloseChoice.window])
def test_close_choice_preset_skips_prompt(manager, windows, fake_ask, preset):
    window, _other = two_windows(manager, windows, manager.default)
    assert closedwindows.close_choice(window, preset) is preset
    assert not fake_ask.calls


@pytest.mark.parametrize('preset', [None, CloseChoice.window])
def test_close_choice_single_window_session(manager, windows, fake_ask,
                                            preset):
    window = open_window(manager, windows, manager.default, 1)
    assert closedwindows.close_choice(window, preset) is CloseChoice.plain
    assert not fake_ask.calls


@pytest.mark.parametrize('preset', [
    None, CloseChoice.window, CloseChoice.session])
def test_close_choice_shutting_down(manager, windows, fake_ask, preset):
    window, _other = two_windows(manager, windows, manager.default)
    manager.shutdown()
    assert closedwindows.close_choice(window, preset) is CloseChoice.plain
    assert not fake_ask.calls


@pytest.mark.parametrize('answer, expected', [
    ('window', CloseChoice.window),
    ('session', CloseChoice.session),
    ('cancel', CloseChoice.cancel),
    (None, CloseChoice.cancel),
])
def test_close_choice_asks(manager, windows, fake_ask, answer, expected):
    window, _other = two_windows(manager, windows, manager.default)
    fake_ask.answer = answer
    assert closedwindows.close_choice(window) is expected
    [kwargs] = fake_ask.calls
    assert kwargs['win_id'] == 1
    assert kwargs['mode'] is usertypes.PromptMode.select
    assert [key for key, _label, _desc in kwargs['options']] == [
        'window', 'session', 'cancel']


def test_close_choice_asks_in_private_sessions(manager, windows, fake_ask):
    window, _other = two_windows(manager, windows, manager.new_private())
    fake_ask.answer = 'session'
    assert closedwindows.close_choice(window) is CloseChoice.session
    [kwargs] = fake_ask.calls
    assert 'discard' in kwargs['options'][1][2]


def test_window_close(manager, windows):
    window = open_window(manager, windows, manager.default, 1)
    closedwindows.window_close(win_id=1)
    assert window.closed
    assert window.close_choice is None


def test_window_close_no_prompt(manager, windows):
    window = open_window(manager, windows, manager.default, 1)
    closedwindows.window_close(no_prompt=True, win_id=1)
    assert window.closed
    assert window.close_choice is CloseChoice.window


def test_close_session_closes_without_asking(manager, windows,
                                             fake_prompt_queue):
    work = manager.new_session('work')
    first, second = two_windows(manager, windows, work)
    sessioncommands.close_session(work)
    assert first.closed and second.closed
    assert first.close_choice is CloseChoice.plain
    assert second.close_choice is CloseChoice.plain
    # Cancels each window's own close prompt (if any), since it becomes
    # moot once the whole session is closing regardless of its answer.
    assert fake_prompt_queue.aborted_win_ids == [1, 2]


def test_record_newest_first(manager, windows):
    work = manager.new_session('work')
    first, second = two_windows(manager, windows, work)
    closedwindows.record(first)
    closedwindows.record(second)
    assert [entry['window'] for entry in work.closed_windows] == [
        {'win': 2}, {'win': 1}]
    closed_at = datetime.datetime.fromisoformat(
        work.closed_windows[0]['closed_at'])
    assert closed_at.utcoffset() == datetime.timedelta(0)
    assert work.dirty


def test_record_caps_history(manager, windows, config_stub):
    config_stub.val.session.closed_windows_max = 2
    work = manager.new_session('work')
    for win_id in [1, 2, 3]:
        closedwindows.record(open_window(manager, windows, work, win_id))
    assert [entry['window'] for entry in work.closed_windows] == [
        {'win': 3}, {'win': 2}]


def test_record_cap_zero_keeps_nothing(manager, windows, config_stub):
    config_stub.val.session.closed_windows_max = 0
    window = open_window(manager, windows, manager.default, 1)
    closedwindows.record(window)
    assert manager.default.closed_windows == []


def test_record_skips_private(manager, windows):
    private = manager.new_private()
    window = open_window(manager, windows, private, 1)
    closedwindows.record(window)
    assert private.closed_windows == []


def test_recorded_window_is_saved_with_its_session(manager, windows,
                                                   tmp_path):
    work = manager.new_session('work')
    closing, _other = two_windows(manager, windows, work)
    closedwindows.record(closing)
    manager.window_closing(closing)
    data = sessionfile.read(tmp_path / 'sessions' / 'work.yml')
    assert data.windows == [{'win': 2}]
    assert [entry['window'] for entry in data.closed_windows] == [{'win': 1}]
    assert isinstance(data.closed_windows[0]['closed_at'], str)


def test_shrinking_the_cap_trims_every_session(manager, config_stub):
    closedwindows.init()
    work = manager.new_session('work')
    work.closed_windows = [{'closed_at': str(i), 'window': {}}
                           for i in range(3)]
    manager.default.closed_windows = [{'closed_at': str(i), 'window': {}}
                                      for i in range(2)]
    config_stub.val.session.closed_windows_max = 1
    assert work.closed_windows == [{'closed_at': '0', 'window': {}}]
    assert manager.default.closed_windows == [{'closed_at': '0', 'window': {}}]
