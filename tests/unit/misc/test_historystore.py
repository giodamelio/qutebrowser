# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import stat
import zlib

import pytest

from qutebrowser.misc import historystore


ID_A = 'a' * 32
ID_B = 'b' * 32


@pytest.fixture(autouse=True)
def running_version(monkeypatch):
    monkeypatch.setattr(historystore, 'running_version', lambda: '6.11.2')


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_new_id():
    first, second = historystore.new_id(), historystore.new_id()
    assert historystore.is_valid_id(first)
    assert first != second


@pytest.mark.parametrize('value', [
    None, 5, '', 'A' * 32, 'a' * 31, 'a' * 33, 'g' * 32,
    '../' + 'a' * 29, '/etc/passwd',
])
def test_invalid_ids(value):
    assert not historystore.is_valid_id(value)


def test_encode_header():
    raw = historystore.encode(b'history', '6.11.2')
    assert raw[:8] == b'QUTEHIST'
    assert raw[8:10] == b'\x00\x01'
    assert raw[10:12] == b'\x00\x06'
    assert raw[12:18] == b'6.11.2'
    assert zlib.decompress(raw[18:]) == b'history'


def test_encode_compresses():
    data = b'the same history entry ' * 1000
    assert len(historystore.encode(data, '6.11.2')) < len(data) // 10


def test_round_trip(tmp_path):
    historystore.write_changed(tmp_path, {ID_A: b'history'}, {})
    assert historystore.read(tmp_path, ID_A) == b'history'
    raw = (tmp_path / f'{ID_A}.bin').read_bytes()
    assert historystore.decode(raw) == (b'history', '6.11.2')


def test_files_are_private(tmp_path):
    historystore.write_changed(tmp_path, {ID_A: b'history'}, {})
    assert mode(tmp_path / f'{ID_A}.bin') == 0o600
    assert not list(tmp_path.glob('*.tmp'))


def test_leftover_temporary_file_is_replaced(tmp_path):
    leftover = tmp_path / f'{ID_A}.bin.tmp'
    leftover.write_bytes(b'partial')
    leftover.chmod(0o644)
    historystore.write_changed(tmp_path, {ID_A: b'history'}, {})
    assert not leftover.exists()
    assert mode(tmp_path / f'{ID_A}.bin') == 0o600


def test_file_from_another_version(tmp_path):
    (tmp_path / f'{ID_A}.bin').write_bytes(
        historystore.encode(b'history', '6.10.0'))
    with pytest.raises(historystore.UnusableHistoryError) as excinfo:
        historystore.read(tmp_path, ID_A)
    assert excinfo.value.reason == 'saved by QtWebEngine 6.10.0, running 6.11.2'


def test_missing_file(tmp_path):
    with pytest.raises(historystore.UnusableHistoryError) as excinfo:
        historystore.read(tmp_path, ID_A)
    assert excinfo.value.reason == 'history file missing'


@pytest.mark.parametrize('raw', [
    b'',
    b'QUTEHIST',
    b'NOTHIST!\x00\x01\x00\x00' + zlib.compress(b'x'),
    b'QUTEHIST\x00\x02\x00\x00' + zlib.compress(b'x'),
    b'QUTEHIST\x00\x01\x00\x02\xff\xfe' + zlib.compress(b'x'),
    historystore.encode(b'history', '6.11.2')[:-3],
])
def test_unreadable_file(tmp_path, raw):
    (tmp_path / f'{ID_A}.bin').write_bytes(raw)
    with pytest.raises(historystore.UnusableHistoryError) as excinfo:
        historystore.read(tmp_path, ID_A)
    assert excinfo.value.reason == 'history file unreadable'


def test_write_changed_skips_known_bytes(tmp_path, monkeypatch):
    digests = {}
    historystore.write_changed(tmp_path, {ID_A: b'a', ID_B: b'b'}, digests)
    assert digests == {ID_A: historystore.digest(b'a'),
                       ID_B: historystore.digest(b'b')}

    written = []
    monkeypatch.setattr(historystore, '_write',
                        lambda path, data: written.append(path.name))
    historystore.write_changed(tmp_path, {ID_A: b'a', ID_B: b'b2'}, digests)
    assert written == [f'{ID_B}.bin']
    assert digests[ID_B] == historystore.digest(b'b2')


def test_write_changed_failure(tmp_path):
    digests = {}
    with pytest.raises(historystore.Error, match=ID_A):
        historystore.write_changed(tmp_path / 'missing', {ID_A: b'a'}, digests)
    assert digests == {}


def test_remove_unreferenced(tmp_path):
    digests = {}
    historystore.write_changed(tmp_path, {ID_A: b'a', ID_B: b'b'}, digests)
    (tmp_path / f'{ID_A}.bin.tmp').write_bytes(b'partial')
    (tmp_path / 'notes.txt').write_text('mine', encoding='utf-8')
    (tmp_path / 'nothex.bin').write_bytes(b'mine')

    historystore.remove_unreferenced(tmp_path, {ID_A}, digests)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f'{ID_A}.bin', 'notes.txt', 'nothex.bin']
    assert digests == {ID_A: historystore.digest(b'a')}


def test_remove_unreferenced_without_directory(tmp_path):
    historystore.remove_unreferenced(tmp_path / 'missing', set(), {})
