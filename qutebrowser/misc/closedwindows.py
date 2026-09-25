# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Closing windows, and bringing closed windows back.

Closing one window of a session that has several asks whether to close just
that window or the whole session.

Windows closed that way are kept, newest first, in their session's
closed_windows, which is saved with the session file.
"""

import datetime
import enum
from typing import Any

from qutebrowser.api import cmdutils
from qutebrowser.config import config
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import sessionfile
from qutebrowser.utils import message, objreg, usertypes


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


def close_choice(window: Any, preset: CloseChoice | None = None) -> CloseChoice:
    """Decide how a window closes, asking in that window when it has to.

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
    return _ask(window)


def _ask(window: Any) -> CloseChoice:
    session = window.session
    count = len(session.windows)
    if session.private:
        this = "The private session keeps its other windows"
        whole = (f"Close all {count} windows and discard private session "
                 f"{session.name}")
    else:
        this = "It can be brought back with :undo --window"
        whole = (f"Close all {count} windows; :session-open {session.name} "
                 "brings them back")
    answer = message.ask(
        title="Close window?",
        text=f"Session {session.name} has {count} windows.",
        mode=usertypes.PromptMode.select,
        win_id=window.win_id,
        options=[
            ('window', "Close this window", this),
            ('session', "Close the whole session", whole),
            ('cancel', "Cancel", "Keep this window open"),
        ])
    if answer is None:
        return CloseChoice.cancel
    return CloseChoice(answer)


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
    session.closed_windows.insert(0, {
        'closed_at': datetime.datetime.now(datetime.UTC).isoformat(
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
