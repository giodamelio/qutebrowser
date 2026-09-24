# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import sessions
from qutebrowser.utils import qtutils


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


@pytest.fixture
def manager(registry, monkeypatch):
    mgr = windowsessions.SessionManager()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


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


def test_legacy_session_load_groups_private_windows(manager, tmp_path,
                                                    monkeypatch, qapp):
    (tmp_path / 'legacy.yml').write_text(
        "windows:\n"
        "- private: true\n"
        "  geometry: null\n"
        "  tabs: []\n"
        "- private: true\n"
        "  geometry: null\n"
        "  tabs: []\n"
        "- geometry: null\n"
        "  tabs: []\n",
        encoding='utf-8')
    sess_man = sessions.SessionManager(base_path=str(tmp_path))
    loaded = []
    monkeypatch.setattr(sess_man, '_load_window',
                        lambda _win, session: loaded.append(session))

    sess_man.load('legacy')

    first, second, third = loaded
    assert first.private
    assert first is second
    assert third is manager.default
