# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import pathlib

import pytest

from qutebrowser.config import configexc
from qutebrowser.misc import containers
from qutebrowser.utils import usertypes


@pytest.fixture
def data_dir(data_tmpdir):
    return pathlib.Path(str(data_tmpdir))


@pytest.fixture
def cache_dir(cache_tmpdir):
    return pathlib.Path(str(cache_tmpdir))


@pytest.fixture
def runtime_file(data_dir):
    return data_dir / 'containers.yml'


def make_registry(data_dir, cache_dir):
    registry = containers.ContainerRegistry(data_dir, cache_dir)
    registry.load()
    return registry


def declare(config_stub, registry, value):
    config_stub.val.containers = value
    registry.on_config_changed('containers')


def test_default_always_exists(container_registry):
    assert container_registry.containers() == [
        containers.Container('default', '#3b4252', 'builtin')]


def test_merge_precedence(container_registry, config_stub):
    container_registry.add('work', '#111111')
    container_registry.add('play', '#222222')
    declare(config_stub, container_registry, {
        'work': {'color': '#333333'},
        'default': {'color': '#444444'},
        'decl': {'color': '#555555'},
    })
    assert container_registry.containers() == [
        containers.Container('decl', '#555555', 'declared'),
        containers.Container('default', '#444444', 'declared'),
        containers.Container('play', '#222222', 'runtime'),
        containers.Container('work', '#333333', 'declared'),
    ]


def test_shadowed_runtime_container_comes_back(container_registry,
                                               config_stub):
    container_registry.add('work', '#111111')
    declare(config_stub, container_registry, {'work': {'color': '#333333'}})
    declare(config_stub, container_registry, {})
    assert container_registry.get('work') == containers.Container(
        'work', '#111111', 'runtime')


def test_merged_signal(container_registry, config_stub, qtbot):
    with qtbot.wait_signal(container_registry.merged):
        declare(config_stub, container_registry, {'x': {'color': 'red'}})


def test_other_options_ignored(container_registry, qtbot):
    with qtbot.assert_not_emitted(container_registry.merged):
        container_registry.on_config_changed('content.javascript.enabled')


def test_runtime_round_trip(container_registry, data_dir, cache_dir,
                            runtime_file):
    container_registry.add('work', '#111111')
    assert 'work:' in runtime_file.read_text(encoding='utf-8')
    assert make_registry(data_dir, cache_dir).get('work') == (
        containers.Container('work', '#111111', 'runtime'))


def test_add_rejects_existing_and_bad_color(container_registry):
    with pytest.raises(containers.Error, match='Container default already exists'):
        container_registry.add('default', '#111111')
    with pytest.raises(containers.Error, match="Invalid color 'nope'"):
        container_registry.add('work', 'nope')
    assert 'work' not in container_registry


def test_remove_and_rename(container_registry, data_dir, cache_dir):
    container_registry.add('work', '#111111')
    container_registry.add('gone', '#222222')
    container_registry.remove('gone')
    container_registry.rename('work', 'job')
    reloaded = make_registry(data_dir, cache_dir)
    assert [c.name for c in reloaded.containers()] == ['default', 'job']
    assert reloaded.get('job').color == '#111111'


def test_get_unknown(container_registry):
    with pytest.raises(containers.UnknownContainerError,
                       match='Container nope not found!'):
        container_registry.get('nope')


def test_storage_paths(container_registry, data_dir, cache_dir):
    container_registry.add('work', '#111111')
    assert container_registry.storage_paths('default') == (
        data_dir / 'webengine', cache_dir / 'webengine')
    assert container_registry.storage_paths('work') == (
        data_dir / 'containers' / 'work', cache_dir / 'containers' / 'work')


@pytest.mark.parametrize('content', [
    'work: [\n',
    '- a\n',
    'work: {colour: red}\n',
    'Work: {color: red}\n',
    'work: {color: nope}\n',
])
def test_load_broken_file_moved_aside(data_dir, cache_dir, runtime_file,
                                      config_stub, message_mock, caplog,
                                      content):
    runtime_file.write_text(content, encoding='utf-8')
    with caplog.at_level(logging.ERROR):
        registry = make_registry(data_dir, cache_dir)
    broken = data_dir / 'containers.yml.broken'
    assert broken.read_text(encoding='utf-8') == content
    assert not runtime_file.exists()
    assert [c.name for c in registry.containers()] == ['default']
    msg = message_mock.getmsg(usertypes.MessageLevel.error)
    assert f'Moved it to {broken}' in msg.text

    registry.add('work', '#111111')
    assert broken.read_text(encoding='utf-8') == content


def test_broken_file_with_existing_backup_blocks_changes(
        data_dir, cache_dir, runtime_file, config_stub, message_mock, caplog):
    runtime_file.write_text('work: [\n', encoding='utf-8')
    broken = data_dir / 'containers.yml.broken'
    broken.write_text('older\n', encoding='utf-8')
    with caplog.at_level(logging.ERROR):
        registry = make_registry(data_dir, cache_dir)
    assert runtime_file.read_text(encoding='utf-8') == 'work: [\n'
    assert broken.read_text(encoding='utf-8') == 'older\n'

    with pytest.raises(containers.Error, match="Can't change runtime containers"):
        registry.check_writable()
    with pytest.raises(containers.Error, match="Can't change runtime containers"):
        registry.add('work', '#111111')
    assert runtime_file.read_text(encoding='utf-8') == 'work: [\n'


def test_adopt(container_registry, runtime_file, caplog):
    with caplog.at_level(logging.INFO):
        container_registry.adopt({'gone': ['b', 'a'], 'old': ['c']})
    assert container_registry.get('gone') == containers.Container(
        'gone', '#808080', 'runtime')
    assert 'old:' in runtime_file.read_text(encoding='utf-8')
    assert ("Keeping container gone as a runtime container, because "
            "sessions use it: a, b") in caplog.text


def test_adopt_keeps_container_when_unwritable(
        data_dir, cache_dir, runtime_file, config_stub, message_mock, caplog):
    runtime_file.write_text('work: [\n', encoding='utf-8')
    (data_dir / 'containers.yml.broken').write_text('older\n', encoding='utf-8')
    with caplog.at_level(logging.ERROR):
        registry = make_registry(data_dir, cache_dir)
        registry.adopt({'gone': ['a']})
    assert 'gone' in registry


def test_delete_storage(container_registry, data_dir, cache_dir):
    container_registry.add('work', '#111111')
    data, cache = container_registry.storage_paths('work')
    (data / 'Cookies').parent.mkdir(parents=True)
    (data / 'Cookies').write_text('', encoding='utf-8')
    cache.mkdir(parents=True)
    container_registry.remove('work')
    container_registry.delete_storage('work')
    assert not data.exists()
    assert not cache.exists()


def test_delete_storage_missing_dirs(container_registry):
    container_registry.delete_storage('never-used')


def test_delete_storage_rejects_bad_name(container_registry, data_dir):
    (data_dir / 'containers' / 'x').mkdir(parents=True)
    (data_dir / 'containers' / 'x' / 'Cookies').write_text('', encoding='utf-8')
    (data_dir / 'file').write_text('', encoding='utf-8')
    for name in ('', '..'):
        with pytest.raises(configexc.ValidationError):
            container_registry.delete_storage(name)
    assert (data_dir / 'containers' / 'x' / 'Cookies').exists()
    assert (data_dir / 'file').exists()


def test_delete_storage_refuses_default(container_registry, data_dir):
    (data_dir / 'webengine').mkdir(parents=True)
    (data_dir / 'webengine' / 'Cookies').write_text('', encoding='utf-8')
    with pytest.raises(AssertionError):
        container_registry.delete_storage('default')
    assert (data_dir / 'webengine' / 'Cookies').exists()


def test_move_storage_refuses_default(container_registry, data_dir):
    (data_dir / 'webengine').mkdir(parents=True)
    (data_dir / 'webengine' / 'Cookies').write_text('', encoding='utf-8')
    with pytest.raises(AssertionError):
        container_registry.move_storage('default', 'x')
    assert (data_dir / 'webengine' / 'Cookies').exists()


def test_move_storage(container_registry, data_dir, cache_dir):
    old_data = data_dir / 'containers' / 'old'
    old_data.mkdir(parents=True)
    (old_data / 'Cookies').write_text('x', encoding='utf-8')
    container_registry.move_storage('old', 'new')
    assert (data_dir / 'containers' / 'new' / 'Cookies').read_text(
        encoding='utf-8') == 'x'
    assert not old_data.exists()


def test_move_storage_refuses_existing_target(container_registry, data_dir,
                                              cache_dir):
    (data_dir / 'containers' / 'old').mkdir(parents=True)
    (cache_dir / 'containers' / 'new').mkdir(parents=True)
    with pytest.raises(containers.Error, match='already exists'):
        container_registry.move_storage('old', 'new')
    assert (data_dir / 'containers' / 'old').exists()


def test_move_storage_undoes_partial_move(container_registry, data_dir,
                                          cache_dir, monkeypatch):
    (data_dir / 'containers' / 'old').mkdir(parents=True)
    (cache_dir / 'containers' / 'old').mkdir(parents=True)
    real_rename = pathlib.Path.rename

    def rename(self, target):
        if self == cache_dir / 'containers' / 'old':
            raise OSError('disk on fire')
        return real_rename(self, target)

    monkeypatch.setattr(pathlib.Path, 'rename', rename)
    with pytest.raises(containers.Error, match='disk on fire'):
        container_registry.move_storage('old', 'new')
    assert (data_dir / 'containers' / 'old').exists()
    assert not (data_dir / 'containers' / 'new').exists()


def test_all_storage_dirs(data_dir):
    (data_dir / 'containers' / 'b').mkdir(parents=True)
    (data_dir / 'containers' / 'a').mkdir(parents=True)
    (data_dir / 'containers' / 'file').write_text('', encoding='utf-8')
    assert containers.all_storage_dirs() == [
        data_dir / 'webengine',
        data_dir / 'containers' / 'a',
        data_dir / 'containers' / 'b',
    ]
