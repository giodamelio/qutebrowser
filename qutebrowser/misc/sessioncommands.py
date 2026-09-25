# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Session commands, and opening sessions at startup."""

from qutebrowser.config import config
from qutebrowser.mainwindow import mainwindow, windowsessions
from qutebrowser.misc import sessionfile
from qutebrowser.utils import message


def open_session(session: windowsessions.Session, *,
                 show: bool = True,
                 fill_start_pages: bool = True) -> list[mainwindow.MainWindow]:
    """Open a closed session, restoring its saved windows."""
    assert not session.is_open, session
    windows = []
    for data in session.saved_windows:
        try:
            windows.append(sessionfile.restore_window(data, session, show=show))
        except sessionfile.SessionFileError as e:
            message.error(f"Failed to restore a window of session "
                          f"{session.name}: {e}")
    if not windows:
        window = mainwindow.MainWindow(session=session)
        if fill_start_pages:
            for url in config.val.url.start_pages:
                window.tabbed_browser.tabopen(url)
        if show:
            window.show()
        windows.append(window)
    return windows


def open_startup_sessions(*, private: bool, show: bool) -> None:
    """Open the sessions that were open when qutebrowser last quit."""
    manager = windowsessions.manager
    opened = False
    for name in manager.saved_open_names():
        try:
            session = manager.get(name)
        except windowsessions.UnknownSessionError:
            message.error(f"Can't reopen session {name}: it no longer exists")
            continue
        if session.is_open:
            continue
        open_session(session, show=show, fill_start_pages=False)
        opened = True

    if not opened and not private:
        open_session(manager.default, show=show, fill_start_pages=False)

    if private:
        window = mainwindow.MainWindow(session=manager.new_private())
        if show:
            window.show()
