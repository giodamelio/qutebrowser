# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the session and container badges."""

import re

import pytest

from qutebrowser.qt.widgets import QWidget

from qutebrowser.mainwindow import windowsessions
from qutebrowser.mainwindow.statusbar import sessionbadge


def badge_colors(badge):
    qss = badge.styleSheet()
    background = re.search(r'background-color: ([^;]+);', qss)
    text = re.search(r'(?<!-)color: ([^;]+);', qss)
    assert background is not None and text is not None, qss
    return background.group(1), text.group(1)


@pytest.mark.parametrize('badge_class, expected', [
    (sessionbadge.SessionName, 'session: work'),
    (sessionbadge.ContainerName, 'container: shop'),
])
def test_badge(qtbot, container_registry, badge_class, expected):
    container_registry.add('shop', '#2e7d32')
    badge = badge_class()
    qtbot.add_widget(badge)
    badge.set_session(windowsessions.Session('work', private=False,
                                             container='shop'))
    assert badge.text() == expected
    assert badge_colors(badge) == ('#2e7d32', '#ffffff')


@pytest.mark.parametrize('badge_class, expected', [
    (sessionbadge.SessionName, 'session: private-1'),
    (sessionbadge.ContainerName, 'container: (private)'),
])
def test_private_badges(qtbot, container_registry, badge_class, expected):
    badge = badge_class()
    qtbot.add_widget(badge)
    badge.set_session(windowsessions.Session('private-1', private=True))
    assert badge.text() == expected
    assert badge_colors(badge) == ('#666666', '#ffffff')


@pytest.fixture
def container_badge(qtbot):
    # A parent keeps show() from opening the badge as a window of its own.
    # qtbot.add_widget() only weakly references it, so this stays a
    # generator: the paused frame is what keeps parent alive.
    parent = QWidget()
    qtbot.add_widget(parent)
    yield sessionbadge.ContainerName(parent)


def test_container_badge_hidden_for_default(container_badge,
                                            container_registry):
    container_registry.add('shop', '#2e7d32')
    container_badge.enabled = True

    container_badge.set_session(windowsessions.Session(
        'work', private=False, container='shop'))
    assert not container_badge.isHidden()

    container_badge.set_session(windowsessions.Session('work', private=False))
    assert container_badge.text() == 'container: default'
    assert container_badge.isHidden()

    container_badge.set_session(windowsessions.Session('private-1',
                                                       private=True))
    assert not container_badge.isHidden()


def test_container_badge_hidden_while_disabled(container_badge,
                                               container_registry):
    container_registry.add('shop', '#2e7d32')
    container_badge.set_session(windowsessions.Session(
        'work', private=False, container='shop'))
    assert container_badge.isHidden()
