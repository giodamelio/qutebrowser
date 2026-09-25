# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Session commands, and opening sessions at startup."""

from qutebrowser.api import cmdutils
from qutebrowser.completion.models import miscmodels
from qutebrowser.config import config
from qutebrowser.mainwindow import mainwindow, prompt, windowsessions
from qutebrowser.misc import closedwindows, sessionfile
from qutebrowser.utils import message, objreg


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
            message.error(f"Failed to restore a window: {e}")
            windowsessions.manager.move_aside(session)
    if not windows:
        window = mainwindow.MainWindow(session=session)
        if fill_start_pages:
            for url in config.val.url.start_pages:
                window.tabbed_browser.tabopen(url)
        if show:
            window.show()
        windows.append(window)
    return windows


def open_before_joining(session: windowsessions.Session) -> None:
    """Open a closed session with saved windows before a new window joins it.

    Autosave writes only live windows, so a new window joining the closed
    session on its own would replace the saved ones in its file.
    """
    if not session.private and not session.is_open and session.saved_windows:
        open_session(session)


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


def close_session(session: windowsessions.Session) -> None:
    """Save a session and close all of its windows without asking."""
    windowsessions.manager.begin_close(session)
    for window_id in sorted(session.windows):
        window = objreg.window_registry[window_id]
        window.close_choice = closedwindows.CloseChoice.plain
        # A window mid-prompt for its own close can't be closed again -
        # Qt swallows that reentrant close() call. Cancel its prompt so its
        # closeEvent unblocks and picks up close_choice above instead of
        # the now-moot prompt answer.
        prompt.prompt_queue.abort_window(window_id)
        window.close()


def _session(name: str) -> windowsessions.Session:
    try:
        return windowsessions.manager.get(name)
    except windowsessions.UnknownSessionError as e:
        raise cmdutils.CommandError(str(e))


@cmdutils.register()
@cmdutils.argument('private', flag='p')
@cmdutils.argument('container', completion=miscmodels.container)
def session_new(name: str | None = None, *, container: str | None = None,
                private: bool = False) -> None:
    """Create a session and open it in a new window.

    Args:
        name: The name of the new session.
        container: The container the session uses. Defaults to `default`.
        private: Open a new private session with an automatic name instead.
    """
    manager = windowsessions.manager
    if private:
        if name is not None or container is not None:
            raise cmdutils.CommandError(
                "--private can't be combined with a name or --container")
        try:
            session = manager.new_private()
        except windowsessions.PrivateUnavailableError as e:
            raise cmdutils.CommandError(str(e))
    else:
        if name is None:
            raise cmdutils.CommandError("Give a session name, or use --private")
        try:
            session = manager.new_session(
                name, container=container or windowsessions.DEFAULT_CONTAINER)
        except (windowsessions.Error, sessionfile.SessionFileError) as e:
            raise cmdutils.CommandError(str(e))
    open_session(session)


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
def session_open(name: str) -> None:
    """Open a session, or focus it if it is already open.

    Args:
        name: The name of the session.
    """
    session = _session(name)
    if not session.is_open:
        open_session(session)
        return
    win_id = session.last_focused
    if win_id not in session.windows:
        win_id = max(session.windows)
    mainwindow.raise_window(objreg.window_registry[win_id])


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def session_close(name: str | None = None, *,
                  win_id: int | None = None) -> None:
    """Save a session and close all of its windows.

    Args:
        name: The name of the session. Defaults to the current window's.
    """
    if name is None:
        assert win_id is not None
        session = objreg.window_registry[win_id].session
    else:
        session = _session(name)
    if not session.is_open:
        raise cmdutils.CommandError(f"Session {session.name} is not open")
    close_session(session)


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
def session_delete(name: str) -> None:
    """Delete a closed session and its file.

    Args:
        name: The name of the session.
    """
    try:
        windowsessions.manager.delete_session(name)
    except windowsessions.Error as e:
        raise cmdutils.CommandError(str(e))


@cmdutils.register()
@cmdutils.argument('old', completion=miscmodels.session)
def session_rename(old: str, new: str) -> None:
    """Rename a session and its file.

    Args:
        old: The current name of the session.
        new: The new name.
    """
    try:
        windowsessions.manager.rename_session(old, new)
    except windowsessions.Error as e:
        raise cmdutils.CommandError(str(e))


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def session_move_window(name: str, *, win_id: int | None = None) -> None:
    """Move the current window into another session.

    Args:
        name: The session to move the window into.
    """
    assert win_id is not None
    window = objreg.window_registry[win_id]
    source = window.session
    target = _session(name)
    if target is source:
        raise cmdutils.CommandError(f"This window is already in session {name}")
    if source.private or target.private:
        raise cmdutils.CommandError(
            "Can't move windows into or out of private sessions")
    if target.container != source.container:
        raise cmdutils.CommandError(
            f"Session {name} uses container {target.container}, but this "
            f"window uses {source.container}")
    open_before_joining(target)
    try:
        windowsessions.manager.move_window(window, target)
    except sessionfile.SessionFileError as e:
        raise cmdutils.CommandError(
            f"Failed to save session {source.name}: {e}")
