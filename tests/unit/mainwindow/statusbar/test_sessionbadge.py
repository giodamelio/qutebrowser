# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the session and container badges."""

import re

import pytest

from qutebrowser.mainwindow import windowsessions
from qutebrowser.mainwindow.statusbar import sessionbadge


def badge_colors(badge):
    qss = badge.styleSheet()
    background = re.search(r'background-color: ([^;]+);', qss)
    text = re.search(r'(?<!-)color: ([^;]+);', qss)
    assert background is not None and text is not None, qss
    return background.group(1), text.group(1)


@pytest.mark.parametrize('badge_class, expected', [
    (sessionbadge.SessionName, 'work'),
    (sessionbadge.ContainerName, 'shop'),
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
    (sessionbadge.SessionName, 'private-1'),
    (sessionbadge.ContainerName, '(private)'),
])
def test_private_badges(qtbot, container_registry, badge_class, expected):
    badge = badge_class()
    qtbot.add_widget(badge)
    badge.set_session(windowsessions.Session('private-1', private=True))
    assert badge.text() == expected
    assert badge_colors(badge) == ('#666666', '#ffffff')
