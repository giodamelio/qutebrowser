# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Badges naming the session and container of a window."""

from qutebrowser.qt.core import Qt

from qutebrowser.mainwindow import windowsessions
from qutebrowser.mainwindow.statusbar import textbase


class _SessionBadge(textbase.TextBase):

    """Text on the colors of a session.

    The badge's own stylesheet wins over the status bar's mode rules, so it
    keeps the session colors in every mode. In normal mode the bar has the
    same colors, and the badge reads as plain text.
    """

    def __init__(self, parent=None):
        # Eliding paints the text itself and would ignore the padding.
        super().__init__(parent, elidemode=Qt.TextElideMode.ElideNone)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def _text(self, session: windowsessions.Session) -> str:
        raise NotImplementedError

    def set_session(self, session: windowsessions.Session) -> None:
        """Show a session on its colors."""
        background, foreground = windowsessions.session_colors(session)
        self.setStyleSheet(
            f"QLabel {{ background-color: {background}; color: {foreground}; "
            "padding: 0px 4px; }")
        self.setText(self._text(session))


class SessionName(_SessionBadge):

    """The name of the window's session."""

    def _text(self, session: windowsessions.Session) -> str:
        return f'session: {session.name}'


class ContainerName(_SessionBadge):

    """The window's container, or (private) for a private session.

    Hidden for `default`: most windows use it, and naming it on every window
    only adds noise.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Set by the status bar while `container` is in statusbar.widgets.
        self.enabled = False

    def _text(self, session: windowsessions.Session) -> str:
        # Parentheses can't be part of a container name, so this can't be
        # mistaken for a container called "private".
        name = '(private)' if session.private else session.container
        return f'container: {name}'

    def set_session(self, session: windowsessions.Session) -> None:
        super().set_session(session)
        # Private sessions keep the default container, so check them first.
        shown = (session.private or
                 session.container != windowsessions.DEFAULT_CONTAINER)
        self.setVisible(self.enabled and shown)
