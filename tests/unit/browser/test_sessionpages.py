# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pathlib
import subprocess
import sys

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.qt.core import QUrl

import qutebrowser
from qutebrowser.browser import sessionpages
from qutebrowser.browser.webengine import profiles
from qutebrowser.mainwindow import windowsessions
from qutebrowser.utils import objreg, qtutils


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
def profile_registry(monkeypatch):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: False)
    registry = profiles.ProfileRegistry(
        factory=FakeProfile, initializer=lambda _profile: (lambda: None))
    monkeypatch.setattr(profiles, 'registry', registry)
    return registry


class FakeTabbedBrowser:

    def __init__(self, tabs):
        self._tabs = [object() for _ in range(tabs)]

    def widgets(self):
        return self._tabs


class FakeDispatcher:

    def __init__(self):
        self.opened = []

    def openurl(self, url, **kwargs):
        self.opened.append((url, kwargs))


class FakeWindow:

    def __init__(self, win_id, tabs):
        self.win_id = win_id
        self.tabbed_browser = FakeTabbedBrowser(tabs)
        self.dispatcher = FakeDispatcher()
        self.registry = objreg.ObjectRegistry()
        self.registry['command-dispatcher'] = self.dispatcher


@pytest.fixture
def windows(monkeypatch):
    registry = {}
    monkeypatch.setattr(objreg, 'window_registry', registry)
    return registry


@pytest.fixture
def base_path(tmp_path):
    return tmp_path / 'sessions'


@pytest.fixture
def manager(container_registry, profile_registry, base_path, state_config,
            fake_save_manager, windows, monkeypatch):
    mgr = windowsessions.SessionManager(
        base_path, serialize_window=lambda window: {'win': window.win_id})
    mgr.load_all()
    monkeypatch.setattr(windowsessions, 'manager', mgr)
    return mgr


def open_window(manager, windows, session, win_id, tabs=1):
    windows[win_id] = FakeWindow(win_id, tabs)
    manager.add_window(session, win_id)
    return windows[win_id]


def declare(config_stub, registry, value):
    config_stub.val.containers = value
    registry.on_config_changed('containers')


def page(handler):
    mimetype, html = handler(QUrl('qute://ignored/'))
    assert mimetype == 'text/html'
    return html


def element(html, tag, element_id):
    """Get the source of the element with the given id.

    The element must not contain another element with the same tag.
    """
    start = html.index(f'<{tag} id="{element_id}"')
    return html[start:html.index(f'</{tag}>', start)]


@pytest.mark.parametrize('host', ['containers'])
def test_qutescheme_registers_page(host):
    """Importing qutescheme alone registers the page, as the browser relies on."""
    code = ("from qutebrowser.browser import qutescheme; "
            "print(sorted(qutescheme._HANDLERS))")
    result = subprocess.run(
        [sys.executable, '-c', code], check=True, capture_output=True,
        text=True, cwd=pathlib.Path(qutebrowser.__file__).parents[1])
    assert repr(host) in result.stdout


def test_containers_page_rows(manager, windows, container_registry,
                              config_stub):
    container_registry.add('work', 'red')
    declare(config_stub, container_registry,
            {'decl': {'color': 'rgb(0, 0, 255)'}})
    job = manager.new_session('job', container='work')
    manager.new_session('side', container='work')
    open_window(manager, windows, job, 1)

    html = page(sessionpages.qute_containers)

    work = element(html, 'tr', 'container-work')
    assert ('<td class="name">work <span class="swatch" '
            'style="background-color: #ff0000"></span></td>') in work
    assert '<td class="source">runtime</td>' in work
    assert '<td class="sessions">job (open), side</td>' in work
    assert '<td class="loaded">loaded</td>' in work

    decl = element(html, 'tr', 'container-decl')
    assert 'style="background-color: #0000ff"' in decl
    assert '<td class="source">declared</td>' in decl
    assert '<td class="sessions">none</td>' in decl
    assert '<td class="loaded">not loaded</td>' in decl

    default = element(html, 'tr', 'container-default')
    assert 'style="background-color: #3b4252"' in default
    assert '<td class="source">built-in</td>' in default
    assert '<td class="sessions">default</td>' in default

    assert (html.index('id="container-decl"') <
            html.index('id="container-default"') <
            html.index('id="container-work"'))


def test_containers_page_unused_warning(manager, container_registry,
                                        config_stub):
    container_registry.add('spare', '#111111')
    container_registry.add('kept', '#222222')
    manager.new_session('keeper', container='kept')
    declare(config_stub, container_registry, {'decl': {'color': '#333333'}})

    html = page(sessionpages.qute_containers)

    assert ('<td class="sessions"><span class="warning">No session uses it. '
            'Delete it with <span class="mono">:container-delete spare</span>'
            '</span></td>') in element(html, 'tr', 'container-spare')
    for name in ['kept', 'decl', 'default']:
        assert 'container-delete' not in element(html, 'tr',
                                                 f'container-{name}')
