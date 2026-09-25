# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.qt.core import QUrl

from qutebrowser.browser.webengine import notification, profiles
from qutebrowser.mainwindow import mainwindow, windowsessions
from qutebrowser.misc import linkrouting
from qutebrowser.utils import objreg, qtutils, usertypes


URL = QUrl('https://example.org/')
OTHER = QUrl('https://example.com/')


class FakeProfile:

    def __init__(self, key, private):
        self.key = key
        self.private = private

    def deleteLater(self):
        pass


class FakeTab:

    def __init__(self, title):
        self._title = title

    def title(self):
        return self._title


class FakeTabWidget:

    def __init__(self, title):
        self._tab = FakeTab(title)

    def currentWidget(self):
        return self._tab


class FakeTabbedBrowser:

    def __init__(self, title):
        self.widget = FakeTabWidget(title)
        self.is_shutting_down = False
        self.opened = []

    def tabopen(self, url, background=False, related=True):
        self.opened.append((url, background))


class FakeWindow:

    def __init__(self, win_id, session, title):
        self.win_id = win_id
        self.session = session
        self.tabbed_browser = FakeTabbedBrowser(title)
        self.should_raise = False
        self.shown = False
        self.raised = False

    def show(self):
        self.shown = True

    def maybe_raise(self):
        self.raised = self.should_raise


@pytest.fixture(autouse=True)
def fake_timers(monkeypatch, stubs):
    monkeypatch.setattr(windowsessions.usertypes, 'Timer', stubs.FakeTimer)


@pytest.fixture
def windows(monkeypatch):
    registry = {}
    monkeypatch.setattr(objreg, 'window_registry', registry)
    return registry


@pytest.fixture
def manager(monkeypatch, tmp_path, windows, container_registry, state_config,
            fake_save_manager):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: False)
    monkeypatch.setattr(profiles, 'registry', profiles.ProfileRegistry(
        factory=FakeProfile, initializer=lambda _profile: (lambda: None)))
    mgr = windowsessions.SessionManager(
        tmp_path / 'sessions',
        serialize_window=lambda window: {'win': window.win_id})
    mgr.load_all()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


def open_window(manager, windows, session, win_id, title=''):
    window = FakeWindow(win_id, session, title)
    windows[win_id] = window
    manager.add_window(session, win_id)
    return window


@pytest.fixture
def games(manager, container_registry):
    container_registry.add('games', '#111111')
    return manager.new_session('play', container='games')


@pytest.fixture
def notified(monkeypatch):
    sent = []
    monkeypatch.setattr(notification, 'notify',
                        lambda title, body: sent.append((title, body)))
    return sent


@pytest.fixture
def two_profiles(manager, windows, games, monkeypatch):
    home = open_window(manager, windows, manager.default, 1, 'Start')
    play = open_window(manager, windows, games, 2, 'Game')
    monkeypatch.setattr(objreg, 'last_focused_window', lambda: home)
    return home, play


def test_distinct_profile_keys(manager, windows, games):
    open_window(manager, windows, manager.default, 1)
    open_window(manager, windows, manager.new_session('work'), 2)
    assert linkrouting.distinct_profile_keys() == {'default'}

    open_window(manager, windows, games, 3)
    assert linkrouting.distinct_profile_keys() == {'default', 'games'}

    open_window(manager, windows, manager.new_private(), 4)
    open_window(manager, windows, manager.new_private(), 5)
    assert linkrouting.distinct_profile_keys() == {
        'default', 'games', 'private-1', 'private-2'}


def test_distinct_profile_keys_ignores_closed_sessions(manager, windows,
                                                       games):
    open_window(manager, windows, manager.default, 1)
    assert linkrouting.distinct_profile_keys() == {'default'}


@pytest.mark.parametrize('target, expected', [
    ('tab', True),
    ('window', True),
    ('private-window', False),
])
def test_needs_picker(two_profiles, target, expected):
    assert linkrouting.needs_picker(target) is expected


def test_needs_picker_one_profile(manager, windows):
    open_window(manager, windows, manager.default, 1)
    open_window(manager, windows, manager.new_session('work'), 2)
    assert not linkrouting.needs_picker('tab')


def test_window_options(manager, windows, games):
    open_window(manager, windows, manager.default, 1, 'Start')
    open_window(manager, windows, games, 3, 'Other game')
    open_window(manager, windows, games, 2, 'Game')
    open_window(manager, windows, manager.new_private(), 4, 'Secret')
    assert linkrouting.window_options() == [
        ('1', 'default / default', 'Start'),
        ('2', 'play / games', 'Game'),
        ('3', 'play / games', 'Other game'),
        ('4', 'private-1 / private', 'Secret'),
    ]


def test_ask_and_open_asks_in_last_focused_window(two_profiles,
                                                  message_mock):
    linkrouting.ask_and_open([URL, OTHER], target='tab')
    question = message_mock.get_question()
    assert question.mode is usertypes.PromptMode.select
    assert question.win_id == 1
    assert [key for key, _label, _desc in question.options] == ['1', '2']
    assert 'https://example.org/' in question.text
    assert 'https://example.com/' in question.text


@pytest.mark.parametrize('target, background, raised', [
    ('tab', False, True),
    ('tab-bg', True, True),
    ('tab-silent', False, False),
    ('tab-bg-silent', True, False),
])
def test_answer_opens_in_chosen_window(two_profiles, message_mock, target,
                                       background, raised):
    home, play = two_profiles
    linkrouting.ask_and_open([URL, OTHER], target=target)
    question = message_mock.get_question()
    question.answer = '2'
    question.done()
    assert play.tabbed_browser.opened == [(URL, background),
                                          (OTHER, background)]
    assert not home.tabbed_browser.opened
    assert play.shown
    assert play.raised is raised


def test_answer_with_window_target(two_profiles, message_mock, monkeypatch,
                                   manager, windows):
    _home, play = two_profiles
    created = []

    def make(*, session):
        window = open_window(manager, windows, session, 10 + len(created))
        created.append(window)
        return window

    monkeypatch.setattr(mainwindow, 'MainWindow', make)
    linkrouting.ask_and_open([URL, OTHER], target='window')
    question = message_mock.get_question()
    question.answer = '2'
    question.done()
    assert [window.session for window in created] == [play.session,
                                                      play.session]
    assert [window.tabbed_browser.opened for window in created] == [
        [(URL, False)], [(OTHER, False)]]
    assert all(window.raised for window in created)
    assert not play.tabbed_browser.opened


def test_cancel_discards_with_message_and_notification(two_profiles,
                                                       message_mock,
                                                       notified):
    linkrouting.ask_and_open([URL, OTHER], target='tab')
    message_mock.get_question().cancel()
    msg = message_mock.getmsg(usertypes.MessageLevel.info)
    assert msg.text == ('Links not opened: https://example.org/, '
                        'https://example.com/')
    assert notified == [('Links not opened',
                         'https://example.org/\nhttps://example.com/')]
    assert not any(window.tabbed_browser.opened for window in two_profiles)


def test_answer_for_closed_window_discards(two_profiles, message_mock,
                                           notified, windows, caplog):
    linkrouting.ask_and_open([URL], target='tab')
    del windows[2]
    question = message_mock.get_question()
    question.answer = '2'
    with caplog.at_level(logging.ERROR):
        question.done()
    assert [msg.text for msg in message_mock.messages] == [
        'Window 2 closed before the links could open',
        'Links not opened: https://example.org/',
    ]
    assert notified == [('Links not opened', 'https://example.org/')]


def test_abort_discards_with_error_and_notification(two_profiles,
                                                    message_mock, notified,
                                                    caplog):
    linkrouting.ask_and_open([URL, OTHER], target='tab')
    question = message_mock.get_question()
    with caplog.at_level(logging.ERROR):
        question.abort()
    assert [msg.text for msg in message_mock.messages] == [
        'Window 1 closed before it could ask where to open the links',
        'Links not opened: https://example.org/, https://example.com/',
    ]
    assert notified == [('Links not opened',
                         'https://example.org/\nhttps://example.com/')]
    assert not any(window.tabbed_browser.opened for window in two_profiles)


def test_abort_after_cancel_discards_only_once(two_profiles, message_mock,
                                               notified, caplog):
    linkrouting.ask_and_open([URL], target='tab')
    question = message_mock.get_question()
    question.cancel()
    with caplog.at_level(logging.ERROR):
        question.abort()
    assert [msg.text for msg in message_mock.messages] == [
        'Links not opened: https://example.org/',
    ]
    assert notified == [('Links not opened', 'https://example.org/')]
