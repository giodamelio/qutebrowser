# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import pytest

from qutebrowser.misc import sessionfile


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
