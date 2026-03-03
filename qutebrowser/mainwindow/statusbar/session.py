# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Session name displayed in the statusbar."""

from qutebrowser.qt.core import Qt

from qutebrowser.mainwindow.statusbar import textbase


class Session(textbase.TextBase):

    """Shows the current window's session name in the statusbar."""

    def __init__(self, parent=None):
        super().__init__(parent, elidemode=Qt.TextElideMode.ElideNone)

    def set_session_name(self, name):
        """Update displayed session name.

        Args:
            name: The session name string.
        """
        if not name:
            name = 'default'
        self.setText('[{}]'.format(name))
        self.show()
