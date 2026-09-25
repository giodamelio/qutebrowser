# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.api import cmdutils
from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import containercommands, containers, sessionfile
from qutebrowser.utils import objreg, qtutils


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


@pytest.fixture
def base_path(tmp_path):
    return tmp_path / 'sessions'


@pytest.fixture
def manager(container_registry, profile_registry, base_path, state_config,
            fake_save_manager, monkeypatch):
    monkeypatch.setattr(objreg, 'window_registry', {})
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id})
    mgr.load_all()
    # As in windowsessions.init, so renames that leave sessions behind adopt
    # the old name.
    container_registry.merged.connect(mgr.adopt_containers)
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


def make_storage(registry, name):
    data, cache = registry._paths_for(name)
    data.mkdir(parents=True)
    (data / 'Cookies').write_text('x', encoding='utf-8')
    cache.mkdir(parents=True)
    return data, cache


def test_container_new(manager, container_registry):
    containercommands.container_new('work')
    containercommands.container_new('play', color='#123456')
    assert container_registry.get('work') == containers.Container(
        'work', containers.FALLBACK_COLOR, 'runtime')
    assert container_registry.get('play').color == '#123456'


@pytest.mark.parametrize('name, color, match', [
    ('default', None, "'default' is reserved"),
    ('Work', None, "Invalid name 'Work'"),
    ('private-x', None, 'reserved for private sessions'),
    ('work', 'nope', "Invalid color 'nope'"),
])
def test_container_new_refused(manager, name, color, match):
    kwargs = {} if color is None else {'color': color}
    with pytest.raises(cmdutils.CommandError, match=match):
        containercommands.container_new(name, **kwargs)


def test_container_new_existing(manager):
    containercommands.container_new('work')
    with pytest.raises(cmdutils.CommandError,
                       match='Container work already exists!'):
        containercommands.container_new('work')


def test_container_delete(manager, container_registry):
    container_registry.add('work', '#111111')
    data, cache = make_storage(container_registry, 'work')
    containercommands.container_delete('work')
    assert 'work' not in container_registry
    assert not data.exists()
    assert not cache.exists()


@pytest.mark.parametrize('name, match', [
    ('default', "Container default can't be deleted"),
    ('nope', 'Container nope not found!'),
    ('decl', 'Container decl is declared in config.py, delete it there'),
])
def test_container_delete_refused(manager, container_registry, config_stub,
                                  name, match):
    config_stub.val.containers = {'decl': {'color': 'red'}}
    container_registry.on_config_changed('containers')
    with pytest.raises(cmdutils.CommandError, match=match):
        containercommands.container_delete(name)


def test_container_delete_refused_while_used(manager, container_registry):
    container_registry.add('work', '#111111')
    for name in ['a', 'b', 'c', 'd']:
        manager.new_session(name, container='work')
    with pytest.raises(cmdutils.CommandError,
                       match='Container work is used by sessions: a, b, c '
                             'and 1 more'):
        containercommands.container_delete('work')
    assert 'work' in container_registry


def test_container_delete_refused_while_loaded(manager, container_registry,
                                               profile_registry):
    container_registry.add('work', '#111111')
    profile_registry.acquire('work', private=False)
    with pytest.raises(cmdutils.CommandError,
                       match='Container work is still loaded'):
        containercommands.container_delete('work')
    assert 'work' in container_registry


def test_container_rename(manager, container_registry, base_path):
    container_registry.add('old', '#111111')
    data, _cache = make_storage(container_registry, 'old')
    manager.new_session('a', container='old')

    containercommands.container_rename('old', 'new')

    assert 'old' not in container_registry
    assert container_registry.get('new').color == '#111111'
    assert not data.exists()
    new_data, _new_cache = container_registry.storage_paths('new')
    assert (new_data / 'Cookies').read_text(encoding='utf-8') == 'x'
    assert sessionfile.read(base_path / 'a.yml').container == 'new'


@pytest.mark.parametrize('old, new, match', [
    ('default', 'x', "Container default can't be renamed"),
    ('nope', 'x', 'Container nope not found!'),
    ('decl', 'x', 'Container decl is declared in config.py, rename it there'),
    ('work', 'decl', 'Container decl already exists!'),
    ('work', 'Bad', "Invalid name 'Bad'"),
    ('work', 'default', "'default' is reserved"),
])
def test_container_rename_refused(manager, container_registry, config_stub,
                                  old, new, match):
    config_stub.val.containers = {'decl': {'color': 'red'}}
    container_registry.on_config_changed('containers')
    container_registry.add('work', '#111111')
    with pytest.raises(cmdutils.CommandError, match=match):
        containercommands.container_rename(old, new)
    assert 'work' in container_registry


def test_container_rename_refused_while_loaded(manager, container_registry,
                                               profile_registry):
    container_registry.add('work', '#111111')
    profile_registry.acquire('work', private=False)
    with pytest.raises(cmdutils.CommandError,
                       match='Container work is still loaded'):
        containercommands.container_rename('work', 'job')


def test_container_rename_refused_target_dir_exists(manager,
                                                    container_registry):
    container_registry.add('work', '#111111')
    make_storage(container_registry, 'job')
    with pytest.raises(cmdutils.CommandError, match='already exists'):
        containercommands.container_rename('work', 'job')
    assert 'work' in container_registry


def test_container_rename_session_write_failure(manager, container_registry,
                                                monkeypatch):
    container_registry.add('old', '#111111')
    manager.new_session('a', container='old')

    def fail(*_args, **_kwargs):
        raise sessionfile.SessionFileError('disk full')

    monkeypatch.setattr(sessionfile, 'write', fail)
    with pytest.raises(cmdutils.CommandError,
                       match='Renamed container old to new, but sessions '
                             'still use old: a'):
        containercommands.container_rename('old', 'new')
    assert 'new' in container_registry
    assert 'old' in container_registry
    assert manager.get('a').container == 'old'


def test_container_rename_rolls_back_on_registry_failure(
        manager, container_registry, monkeypatch):
    container_registry.add('old', '#111111')
    data, _cache = make_storage(container_registry, 'old')
    manager.new_session('a', container='old')

    def fail(*_args, **_kwargs):
        raise containers.Error('disk full')

    # Only the rename call below writes the registry from here on; the
    # earlier add() already happened.
    monkeypatch.setattr(container_registry, '_write', fail)
    with pytest.raises(cmdutils.CommandError, match='disk full'):
        containercommands.container_rename('old', 'new')

    assert manager.get('a').container == 'old'
    assert data.exists()
    new_data, _new_cache = container_registry._paths_for('new')
    assert not new_data.exists()
    assert 'old' in container_registry
    assert 'new' not in container_registry
