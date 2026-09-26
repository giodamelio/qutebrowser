# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Closing windows, and bringing closed windows back.

Closing one window of a session that has several asks whether to close just
that window or the whole session.

Windows closed that way are kept, newest first, in their session's
closed_windows, which is saved with the session file.

Quitting while downloads are running asks first, since it ends them.
"""

import datetime
import enum
from typing import Any
from collections.abc import Collection

from qutebrowser.api import cmdutils
from qutebrowser.completion.models import miscmodels
from qutebrowser.config import config
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import sessionfile
from qutebrowser.utils import log, message, objreg, usertypes


class CloseChoice(enum.Enum):

    """How a window closes.

    window: Close only this window, keeping it in the closed-window history.
    session: Close every window of its session instead.
    cancel: Keep the window open.
    plain: Close without asking and without recording it, as when quitting.
    """

    window = 'window'
    session = 'session'
    cancel = 'cancel'
    plain = 'plain'


class Error(Exception):

    """A closed window could not be restored."""


class NothingToRestoreError(Error):

    """No session has a closed window."""


def close_choice(window: Any, preset: CloseChoice | None = None) -> CloseChoice:
    """Decide how a window closes, asking in that window when it has to.

    A "close this window" answer that leaves this the browser's last window
    also asks about running downloads, since closing it now quits.

    Args:
        window: The closing window.
        preset: A choice made before the close started, e.g. by a command.
    """
    if windowsessions.manager.shutting_down:
        return CloseChoice.plain
    if preset in [CloseChoice.plain, CloseChoice.session]:
        return preset
    if len(window.session.windows) <= 1:
        return CloseChoice.plain
    if preset is CloseChoice.window:
        return preset
    answer = _ask(window)
    if answer is CloseChoice.window:
        # _ask() blocks; another window of this session may have closed in
        # the meantime, so the table is re-run to catch a session that's
        # now down to just this one.
        answer = close_choice(window, preset=answer)
        # Before the prompt this session had another window, so this can
        # only just have become the browser's last one.
        if not confirm_close(window.win_id, {window.win_id}):
            return CloseChoice.cancel
    return answer


def _ask(window: Any) -> CloseChoice:
    session = window.session
    # No window count: another window may close while this question shows.
    if session.private:
        this = "The private session keeps its other windows"
        whole = f"Private session {session.name} is discarded"
    else:
        this = "It can be brought back with :undo --window"
        whole = f":session-open {session.name} brings them back"
    answer = message.ask(
        title="Close window?",
        text=f"Session {session.name}",
        mode=usertypes.PromptMode.select,
        win_id=window.win_id,
        options=[
            ('window', "Close this window", this),
            ('session', "Close the whole session (all its windows)", whole),
            ('cancel', "Cancel", "Keep this window open"),
        ])
    if answer is None:
        return CloseChoice.cancel
    return CloseChoice(answer)


def running_downloads() -> int:
    """Count the downloads that quitting would end."""
    return sum(not download.done
               for name in ['qtnetwork-download-manager',
                            'webengine-download-manager']
               for download in objreg.get(name).downloads)


def confirm_quit(win_id: int) -> bool:
    """Ask in a window whether to quit while downloads are running.

    Return:
        Whether quitting may go ahead.
    """
    count = running_downloads()
    if not count:
        return True
    noun = 'download' if count == 1 else 'downloads'
    answer = message.ask(
        title=f"{count} {noun} still running. Close anyway?",
        mode=usertypes.PromptMode.yesno, default=False, win_id=win_id)
    return bool(answer)


def confirm_close(win_id: int, closing: Collection[int]) -> bool:
    """Ask before closing windows quits with downloads running.

    Closing other windows never asks: a container profile released with its
    session outlives its downloads (ProfileRegistry holds it until they
    finish), so only quitting ends them.

    Args:
        win_id: The window to ask in.
        closing: The windows about to close.

    Return:
        Whether closing may go ahead.
    """
    if windowsessions.manager.shutting_down:
        return True
    if not set(objreg.window_registry) <= set(closing):
        return True
    return confirm_quit(win_id)


@cmdutils.register()
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def window_close(*, no_prompt: bool = False, win_id: int | None = None) -> None:
    """Close the current window, asking first if its session has others.

    Args:
        no_prompt: Don't ask; close only this window and keep it in the
                   session's closed-window history.
    """
    assert win_id is not None
    window = objreg.window_registry[win_id]
    if no_prompt:
        window.close_choice = CloseChoice.window
    window.close()


def record(window: Any) -> None:
    """Add a closing window to its session's closed-window history.

    Must be called while the window still has its tabs. Private sessions keep
    no history.
    """
    session = window.session
    if session.private:
        return
    session.held_history.update(windowsessions.manager.window_history(window))
    session.closed_windows.insert(0, {
        'closed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec='milliseconds'),
        'window': sessionfile.serialize_window(window),
    })
    _trim(session)
    windowsessions.manager.mark_dirty(session)


def _trim(session: windowsessions.Session) -> bool:
    limit = config.val.session.closed_windows_max
    if len(session.closed_windows) <= limit:
        return False
    del session.closed_windows[limit:]
    return True


@config.change_filter('session.closed_windows_max', function=True)
def _on_config_changed() -> None:
    for session in windowsessions.manager.sessions():
        if _trim(session):
            windowsessions.manager.mark_dirty(session)


def init() -> None:
    """Keep every session's history within session.closed_windows_max."""
    config.instance.changed.connect(_on_config_changed)


_OLDEST = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _closed_at(entry: sessionfile.JsonType) -> datetime.datetime:
    value = entry.get('closed_at')
    if isinstance(value, datetime.datetime):
        closed_at = value
    else:
        try:
            closed_at = datetime.datetime.fromisoformat(
                str(value).replace('Z', '+00:00'))
        except ValueError:
            log.sessions.warning(
                f"Closed window with unreadable time {value!r} counts as the "
                "oldest")
            return _OLDEST
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=datetime.timezone.utc)
    return closed_at


def newest() -> tuple[windowsessions.Session, int]:
    """Find the most recently closed window across all sessions.

    Return:
        The session and the entry's index in its history.
    """
    candidates = [
        (_closed_at(entry), session, index)
        for session in windowsessions.manager.sessions() if not session.private
        for index, entry in enumerate(session.closed_windows)
    ]
    if not candidates:
        raise NothingToRestoreError("Nothing to undo")
    _closed, session, index = max(candidates,
                                  key=lambda candidate: candidate[0])
    return session, index


def restore(session: windowsessions.Session, index: int) -> Any:
    """Reopen a session's closed window in that session.

    A closed session with saved windows is opened first, so the restored
    window doesn't replace them in its file.

    Args:
        session: The session the window belongs to.
        index: The history entry, 0 being the most recently closed.
    """
    entry = session.closed_windows[index]
    data = entry.get('window')
    if not isinstance(data, dict):
        raise Error(f"Closed window {index + 1} of session {session.name} "
                    "has no window data")
    # sessioncommands imports this module.
    from qutebrowser.misc import sessioncommands
    sessioncommands.open_before_joining(session)
    try:
        window = sessionfile.restore_window(data, session)
    except sessionfile.SessionFileError as e:
        # The half-built window that failed listed session on opening; undo
        # that if it left the session listed with nothing to show for it.
        windowsessions.manager.unlist(session)
        raise Error(str(e))
    del session.closed_windows[index]
    windowsessions.manager.mark_dirty(session)
    return window


def undo_newest() -> Any:
    """Reopen the most recently closed window, in its own session."""
    session, index = newest()
    return restore(session, index)


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def session_restore_window(name: str | None = None, index: int = 1, *,
                           win_id: int | None = None) -> None:
    """Reopen a window from a session's closed-window history.

    The session is opened first if it is closed.

    Args:
        name: The session. Defaults to the current window's.
        index: Which closed window to reopen, 1 being the most recently
               closed.
    """
    if name is None:
        assert win_id is not None
        session = objreg.window_registry[win_id].session
    else:
        try:
            session = windowsessions.manager.get(name)
        except windowsessions.UnknownSessionError as e:
            raise cmdutils.CommandError(str(e))
    if session.private:
        raise cmdutils.CommandError(
            f"Private session {session.name} keeps no closed windows")
    if not 1 <= index <= len(session.closed_windows):
        raise cmdutils.CommandError(
            f"Session {session.name} has no closed window {index}")
    try:
        restore(session, index - 1)
    except Error as e:
        raise cmdutils.CommandError(str(e))
