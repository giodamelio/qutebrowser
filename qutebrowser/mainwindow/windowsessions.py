# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Window-grouped sessions.

Every window belongs to exactly one session, and the session decides which
QWebEngineProfile the window's pages use.
"""

import itertools
from typing import cast

from qutebrowser.browser.webengine import profiles
from qutebrowser.utils import log, utils


DEFAULT_NAME = 'default'
PRIVATE_PREFIX = 'private-'

PrivateUnavailableError = profiles.PrivateUnavailableError


class Session:

    """A named group of windows sharing one profile."""

    def __init__(self, name: str, *, private: bool) -> None:
        self.name = name
        self.private = private
        self.windows: set[int] = set()

    def __repr__(self) -> str:
        return utils.get_repr(self, name=self.name, private=self.private)

    @property
    def profile_key(self) -> str:
        """The profile registry key: the session name if private, else the default."""
        if self.private:
            return self.name
        return profiles.DEFAULT_KEY


class SessionManager:

    """Owns the default session and all open private sessions."""

    def __init__(self) -> None:
        self.default = Session(DEFAULT_NAME, private=False)
        self._private: dict[str, Session] = {}
        self._private_ids = itertools.count(1)

    def new_private(self) -> Session:
        """Open a new private session with its own off-the-record profile."""
        name = f'{PRIVATE_PREFIX}{next(self._private_ids)}'
        profiles.get_registry().acquire(name, private=True)
        session = Session(name, private=True)
        self._private[name] = session
        log.misc.debug(f"Opened private session {name}")
        return session

    def private_sessions(self) -> list[Session]:
        return list(self._private.values())

    def add_window(self, session: Session, win_id: int) -> None:
        session.windows.add(win_id)

    def remove_window(self, session: Session, win_id: int) -> None:
        """Remove a window, closing a private session with its last window."""
        session.windows.discard(win_id)
        if (session.private and not session.windows
                and session.name in self._private):
            del self._private[session.name]
            profiles.get_registry().release(session.profile_key)
            log.misc.debug(f"Closed private session {session.name}")


manager = cast(SessionManager, None)


def init() -> None:
    global manager
    manager = SessionManager()
