# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import logging

import pytest
import pytest_bdd as bdd
bdd.scenarios('sessions.feature')


@pytest.fixture(autouse=True)
def turn_on_scroll_logging(quteproc):
    quteproc.turn_on_scroll_logging()


def _session_path(quteproc, name):
    return os.path.join(quteproc.basedir, 'data', 'sessions', name,
                        'session.yml')


@bdd.when(bdd.parsers.parse('I have a "{name}" session file:'))
def create_session_file(quteproc, name, docstring):
    filename = _session_path(quteproc, name)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(docstring)


@bdd.when(bdd.parsers.parse('I replace "{pattern}" by "{replacement}" in the '
                            '"{name}" session file'))
def session_replace(quteproc, server, pattern, replacement, name):
    # First wait until the session was actually saved
    quteproc.wait_for(category='message', loglevel=logging.INFO,
                      message='Saved session {}.'.format(name))
    filename = _session_path(quteproc, name)
    replacement = replacement.replace('(port)', str(server.port))  # yo dawg
    with open(filename, 'r', encoding='utf-8') as f:
        data = f.read()
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(data.replace(pattern, replacement))


@bdd.then(bdd.parsers.parse("the session {name} should exist"))
def session_should_exist(quteproc, name):
    assert os.path.exists(_session_path(quteproc, name))


@bdd.then(bdd.parsers.parse("the session {name} should not exist"))
def session_should_not_exist(quteproc, name):
    assert not os.path.exists(os.path.dirname(_session_path(quteproc, name)))
