# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the status bar's session colors."""

import re
import types

import pytest

from qutebrowser.mainwindow import windowsessions
from qutebrowser.mainwindow.statusbar import bar
from qutebrowser.utils import objreg, usertypes


@pytest.fixture
def window(win_registry, container_registry):
    window = types.SimpleNamespace(
        session=windowsessions.Session('default', private=False))
    tabbed_browser = types.SimpleNamespace(
        widget=types.SimpleNamespace(currentWidget=lambda: None))
    objreg.register('main-window', window, scope='window', window=0)
    objreg.register('tabbed-browser', tabbed_browser, scope='window',
                    window=0)
    return window


@pytest.fixture
def make_statusbar(qtbot, window, config_stub):
    config_stub.val.fonts.statusbar = '8pt Monospace'

    def make(session):
        window.session = session
        # private only picks the command history; True keeps the global
        # one, which these tests don't set up, out of the way.
        statusbar = bar.StatusBar(win_id=0, private=True)
        qtbot.add_widget(statusbar)
        return statusbar

    return make


def session_rules(statusbar):
    """Get the background and text color of the stylesheet's base rules."""
    qss = statusbar.styleSheet()
    text = re.search(
        r'QWidget#StatusBar QLineEdit \{\s*font: [^;]*;\s*color: ([^;]+);',
        qss)
    background = re.search(
        r'QWidget#StatusBar \{\s*background-color: ([^;]+);', qss)
    assert text is not None and background is not None, qss
    return background.group(1), text.group(1)


def test_default_session_colors(make_statusbar):
    statusbar = make_statusbar(
        windowsessions.Session('default', private=False))
    assert session_rules(statusbar) == ('#3b4252', '#ffffff')


def test_mode_rules_kept(make_statusbar):
    statusbar = make_statusbar(
        windowsessions.Session('default', private=False))
    qss = statusbar.styleSheet()
    assert re.search(
        r'\[color_flags~="insert"\] \{\s*background-color: darkgreen;', qss)
    assert re.search(
        r'\[color_flags~="command"\] \{\s*background-color: black;', qss)
    assert 'color_flags~="private' not in qss


def test_mode_change_keeps_session_colors(make_statusbar, container_registry):
    container_registry.add('shop', 'yellow')
    statusbar = make_statusbar(
        windowsessions.Session('work', private=False, container='shop'))

    statusbar.set_mode_active(usertypes.KeyMode.insert, True)
    assert statusbar.color_flags == ['insert']
    assert session_rules(statusbar) == ('#ffff00', '#000000')

    statusbar.set_mode_active(usertypes.KeyMode.insert, False)
    assert statusbar.color_flags == []
    assert session_rules(statusbar) == ('#ffff00', '#000000')


def test_private_session(make_statusbar):
    statusbar = make_statusbar(windowsessions.Session('private-1',
                                                      private=True))
    assert session_rules(statusbar) == ('#666666', '#ffffff')
    statusbar.set_mode_active(usertypes.KeyMode.command, True)
    assert statusbar.color_flags == ['command']


def test_session_changed(make_statusbar, window, container_registry):
    statusbar = make_statusbar(
        windowsessions.Session('default', private=False))
    container_registry.add('shop', '#2e7d32')
    window.session = windowsessions.Session('work', private=False,
                                            container='shop')
    statusbar.on_session_changed()
    assert session_rules(statusbar) == ('#2e7d32', '#ffffff')


def test_badges_follow_session(make_statusbar, window, container_registry):
    statusbar = make_statusbar(
        windowsessions.Session('default', private=False))
    assert statusbar.session_name.text() == 'session: default'
    assert statusbar.container_name.text() == 'container: default'
    assert not statusbar.session_name.isHidden()
    assert statusbar.container_name.isHidden()

    container_registry.add('shop', 'yellow')
    window.session = windowsessions.Session('work', private=False,
                                            container='shop')
    statusbar.on_session_changed()
    assert statusbar.session_name.text() == 'session: work'
    assert statusbar.container_name.text() == 'container: shop'
    assert not statusbar.container_name.isHidden()
    assert 'background-color: #ffff00;' in statusbar.container_name.styleSheet()

    window.session = windowsessions.Session('back', private=False)
    statusbar.on_session_changed()
    assert statusbar.container_name.isHidden()


def test_badges_in_widgets(make_statusbar, config_stub):
    statusbar = make_statusbar(
        windowsessions.Session('default', private=False))
    hbox = statusbar._hbox
    assert [hbox.indexOf(widget) for widget in [
        statusbar.session_name, statusbar.container_name,
        statusbar.keystring]] == [1, 2, 3]

    config_stub.val.statusbar.widgets = ['url']
    assert hbox.indexOf(statusbar.session_name) == -1
    assert hbox.indexOf(statusbar.container_name) == -1
    assert statusbar.session_name.isHidden()


def test_default_container_badge_stays_hidden_on_redraw(make_statusbar,
                                                        config_stub):
    statusbar = make_statusbar(
        windowsessions.Session('default', private=False))
    config_stub.val.statusbar.widgets = ['container', 'url']
    # The badge hides itself via setVisible(), not by leaving the hbox, so
    # visibility is what the brief's hiding code actually controls.
    assert not statusbar.container_name.isVisible()
    assert statusbar.container_name.isHidden()


def test_container_badge_redraw(make_statusbar, config_stub,
                                container_registry):
    container_registry.add('shop', 'yellow')
    statusbar = make_statusbar(
        windowsessions.Session('work', private=False, container='shop'))
    config_stub.val.statusbar.widgets = ['container', 'url']
    assert not statusbar.container_name.isHidden()

    config_stub.val.statusbar.widgets = ['url']
    assert statusbar.container_name.isHidden()
    assert not statusbar.container_name.enabled
