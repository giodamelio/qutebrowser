# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Window-grouped sessions.

Every window belongs to exactly one session, and the session decides which
QWebEngineProfile the window's pages use. Non-private sessions are saved to
data/sessions/<name>.yml, and the names of open ones are kept in the state
file so they come back at the next start.
"""

import datetime
import itertools
import pathlib
import re
from collections.abc import Callable
from typing import Any, cast

from qutebrowser.browser.webengine import profiles
from qutebrowser.config import configfiles
from qutebrowser.misc import sessionfile
from qutebrowser.utils import log, message, objreg, standarddir, usertypes, utils


DEFAULT_NAME = 'default'
PRIVATE_PREFIX = 'private-'
DEFAULT_CONTAINER = sessionfile.DEFAULT_CONTAINER
_NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]*')

PrivateUnavailableError = profiles.PrivateUnavailableError


class Error(Exception):

    """A session operation failed in a way to show to the user."""


class InvalidNameError(Error):

    """A session or container name breaks the naming rules."""


class UnknownSessionError(Error):

    """No session has the given name."""


class SessionExistsError(Error):

    """A session with the given name already exists."""


class UnknownContainerError(Error):

    """No container has the given name."""


class SessionStateError(Error):

    """The session is in the wrong state for the operation."""


def validate_name(name: str) -> None:
    """Check a session or container name, including `default`."""
    if not _NAME_RE.fullmatch(name):
        raise InvalidNameError(
            f"Invalid name {name!r}: use lowercase letters, digits, '_' and "
            "'-', starting with a letter or digit")
    if name.startswith(PRIVATE_PREFIX):
        raise InvalidNameError(
            f"Invalid name {name!r}: the {PRIVATE_PREFIX!r} prefix is "
            "reserved for private sessions")


def validate_new_name(name: str) -> None:
    """Check a name for a session or container that is being created."""
    validate_name(name)
    if name == DEFAULT_NAME:
        raise InvalidNameError(f"{DEFAULT_NAME!r} is reserved")


class Session:

    """A named group of windows sharing one profile."""

    def __init__(self, name: str, *, private: bool,
                 container: str = DEFAULT_CONTAINER) -> None:
        self.name = name
        self.private = private
        self.container = container
        self.windows: set[int] = set()
        self.saved_windows: list[sessionfile.JsonType] = []
        self.closed_windows: list[sessionfile.JsonType] = []
        self.last_saved: datetime.datetime | None = None
        self.last_focused: int | None = None
        self.dirty = False

    def __repr__(self) -> str:
        return utils.get_repr(self, name=self.name, private=self.private,
                              container=self.container)

    @property
    def profile_key(self) -> str:
        """The profile registry key: the session name if private, else the container."""
        if self.private:
            return self.name
        return self.container

    @property
    def is_open(self) -> bool:
        return bool(self.windows)


class _Debouncer:

    """Run a callback once changes pause, or after a maximum wait at the latest."""

    def __init__(self, callback: Callable[[], None], *,
                 quiet_ms: int, max_wait_ms: int) -> None:
        self._callback = callback
        self._quiet = usertypes.Timer(name='session-autosave-quiet')
        self._quiet.setSingleShot(True)
        self._quiet.setInterval(quiet_ms)
        self._quiet.timeout.connect(self._fire)
        self._max_wait = usertypes.Timer(name='session-autosave-max-wait')
        self._max_wait.setSingleShot(True)
        self._max_wait.setInterval(max_wait_ms)
        self._max_wait.timeout.connect(self._fire)

    def trigger(self) -> None:
        """Restart the quiet timer, starting the max-wait timer on the first call."""
        self._quiet.start()
        if not self._max_wait.isActive():
            self._max_wait.start()

    def cancel(self) -> None:
        self._quiet.stop()
        self._max_wait.stop()

    def _fire(self) -> None:
        self.cancel()
        self._callback()


class SessionManager:

    """Knows every session, which windows belong to it, and what is open."""

    def __init__(
            self,
            base_path: pathlib.Path,
            *,
            serialize_window: Callable[[Any], sessionfile.JsonType] = sessionfile.serialize_window,
    ) -> None:
        self._base_path = base_path
        self._serialize_window = serialize_window
        self._sessions: dict[str, Session] = {
            DEFAULT_NAME: Session(DEFAULT_NAME, private=False),
        }
        self._private: dict[str, Session] = {}
        self._private_ids = itertools.count(1)
        self._open_order: list[str] = []
        self._closing: set[str] = set()
        self._unreadable: set[str] = set()
        self._shutting_down = False
        self._autosave = _Debouncer(self.save_dirty, quiet_ms=1500,
                                    max_wait_ms=5000)

    @property
    def default(self) -> Session:
        return self._sessions[DEFAULT_NAME]

    def load_all(self) -> None:
        """Read every session file, skipping and reporting broken ones."""
        self._base_path.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._base_path.glob('*.yml')):
            name = path.stem
            try:
                validate_name(name)
            except InvalidNameError:
                log.sessions.debug(f"Ignoring {path}: not a valid session name")
                continue
            try:
                data = sessionfile.read(path)
            except sessionfile.SessionFileError as e:
                self._quarantine(name, path, e)
                continue
            session = Session(name, private=False, container=data.container)
            session.saved_windows = data.windows
            session.closed_windows = data.closed_windows
            self._sessions[name] = session

    def _quarantine(self, name: str, path: pathlib.Path,
                    error: sessionfile.SessionFileError) -> None:
        """Keep an unreadable session file from ever being overwritten."""
        if name != DEFAULT_NAME:
            self._unreadable.add(name)
            message.error(f"Skipping session {name}: {error}")
            return
        broken_path = path.parent / f'{path.name}.broken'
        if broken_path.exists():
            self._unreadable.add(name)
            message.error(
                f"Skipping session {name}: {error}. {path} could not be "
                f"moved aside because {broken_path} already exists.")
            return
        path.rename(broken_path)
        message.error(
            f"Skipping session {name}: {error}. Moved it to {broken_path}.")

    def saved_open_names(self) -> list[str]:
        """Get the sessions the state file lists as open."""
        value = configfiles.state['general'].get('open_sessions', '')
        return [name for name in value.split(',') if name]

    def get(self, name: str) -> Session:
        """Get a session by name, private or not."""
        if name in self._sessions:
            return self._sessions[name]
        if name in self._private:
            return self._private[name]
        raise UnknownSessionError(f"Session {name} not found!")

    def sessions(self) -> list[Session]:
        """Get all sessions: saved ones by name, then open private ones."""
        return ([self._sessions[name] for name in sorted(self._sessions)] +
                list(self._private.values()))

    def private_sessions(self) -> list[Session]:
        return list(self._private.values())

    def path_for(self, session: Session) -> pathlib.Path:
        assert not session.private, session
        return self._base_path / f'{session.name}.yml'

    def new_session(self, name: str, *,
                    container: str = DEFAULT_CONTAINER) -> Session:
        """Create a session and its file, without opening it."""
        validate_new_name(name)
        if name in self._sessions:
            raise SessionExistsError(f"Session {name} already exists!")
        if name in self._unreadable:
            raise SessionExistsError(
                f"Session file {self._base_path / f'{name}.yml'} exists "
                "but can't be read")
        if container != DEFAULT_CONTAINER:
            raise UnknownContainerError(f"Unknown container {container!r}")
        session = Session(name, private=False, container=container)
        self._write(session, [])
        self._sessions[name] = session
        return session

    def new_private(self) -> Session:
        """Open a new private session with its own off-the-record profile."""
        name = f'{PRIVATE_PREFIX}{next(self._private_ids)}'
        profiles.get_registry().acquire(name, private=True)
        session = Session(name, private=True)
        self._private[name] = session
        log.misc.debug(f"Opened private session {name}")
        return session

    def delete_session(self, name: str) -> None:
        """Forget a closed session and delete its file."""
        session = self.get(name)
        if session.private or name == DEFAULT_NAME:
            raise SessionStateError(f"Session {name} can't be deleted")
        if session.is_open:
            raise SessionStateError(f"Session {name} is open, close it first")
        self.path_for(session).unlink(missing_ok=True)
        del self._sessions[name]

    def rename_session(self, old: str, new: str) -> None:
        """Rename a session and its file."""
        session = self.get(old)
        if session.private or old == DEFAULT_NAME:
            raise SessionStateError(f"Session {old} can't be renamed")
        validate_new_name(new)
        if new in self._sessions:
            raise SessionExistsError(f"Session {new} already exists!")
        if new in self._unreadable:
            raise SessionExistsError(
                f"Session file {self._base_path / f'{new}.yml'} exists "
                "but can't be read")
        old_path = self.path_for(session)
        try:
            old_path.rename(self._base_path / f'{new}.yml')
        except OSError as e:
            raise Error(f"Failed to rename {old_path}: {e}")
        session.name = new
        del self._sessions[old]
        self._sessions[new] = session
        if old in self._open_order:
            self._open_order[self._open_order.index(old)] = new
            self._write_open_list()

    def add_window(self, session: Session, win_id: int) -> None:
        """Add a window to a session, opening it if it wasn't already."""
        session.windows.add(win_id)
        if not session.private and session.name not in self._open_order:
            self._open_order.append(session.name)
            self._write_open_list()
        self.mark_dirty(session)

    def remove_window(self, session: Session, win_id: int) -> None:
        """Remove a window, closing a private session with its last window."""
        session.windows.discard(win_id)
        if session.windows:
            return
        self._closing.discard(session.name)
        if session.private and session.name in self._private:
            del self._private[session.name]
            profiles.get_registry().release(session.profile_key)
            log.misc.debug(f"Closed private session {session.name}")

    def window_focused(self, session: Session, win_id: int) -> None:
        session.last_focused = win_id

    def window_closing(self, window: Any) -> None:
        """Save the session of a window that is about to close.

        Called while the window still has its tabs. Closing the browser's last
        window counts as quitting, so its session stays open.
        """
        session = window.session
        if (self._shutting_down or session.private or
                session.name in self._closing):
            return
        if sum(len(s.windows) for s in self.sessions()) == 1:
            self._save_reporting(session)
        elif session.windows == {window.win_id}:
            self._save_reporting(session)
            self._set_closed(session)
        else:
            self._save_reporting(session, exclude=window.win_id)

    def begin_close(self, session: Session) -> None:
        """Save and close a session whose windows are about to close."""
        if session.private:
            return
        self._save_reporting(session)
        self._set_closed(session)
        self._closing.add(session.name)

    def move_window(self, window: Any, target: Session) -> None:
        """Move a window into another session with the same profile."""
        source = window.session
        assert source is not target, source
        assert not source.private and not target.private, (source, target)
        assert source.profile_key == target.profile_key, (source, target)
        self._save_reporting(source, exclude=window.win_id)
        source.windows.discard(window.win_id)
        window.session = target
        window.tabbed_browser.session = target
        for tab in window.tabbed_browser.widgets():
            tab.session = target
        self.add_window(target, window.win_id)
        if not source.windows:
            self._set_closed(source)

    def mark_dirty(self, session: Session) -> None:
        """Mark a non-private session as needing a save."""
        if session.private or self._shutting_down:
            return
        session.dirty = True
        self._autosave.trigger()

    def save(self, session: Session, *, exclude: int | None = None) -> None:
        """Write a session's live windows to its file."""
        assert not session.private, session
        windows = [self._serialize_window(objreg.window_registry[win_id])
                   for win_id in sorted(session.windows) if win_id != exclude]
        self._write(session, windows)

    def save_dirty(self) -> None:
        """Save every open, non-private session marked dirty."""
        for session in self.sessions():
            if session.dirty and session.windows and not session.private:
                self._save_reporting(session)

    def shutdown(self) -> None:
        """Save every open session before quitting or restarting."""
        if self._shutting_down:
            return
        for session in self.sessions():
            if session.windows and not session.private:
                self._save_reporting(session)
        self._write_open_list()
        self._autosave.cancel()
        self._shutting_down = True

    def _save_reporting(self, session: Session, *,
                        exclude: int | None = None) -> None:
        try:
            self.save(session, exclude=exclude)
        except sessionfile.SessionFileError as e:
            message.error(f"Failed to save session {session.name}: {e}")

    def _write(self, session: Session,
               windows: list[sessionfile.JsonType]) -> None:
        if session.name in self._unreadable:
            raise sessionfile.SessionFileError(
                f"Refusing to overwrite unreadable session file "
                f"{self.path_for(session)}")
        sessionfile.write(self.path_for(session), sessionfile.SessionData(
            container=session.container, windows=windows,
            closed_windows=session.closed_windows))
        session.saved_windows = windows
        session.last_saved = datetime.datetime.now()
        session.dirty = False
        log.sessions.debug(f"Saved session {session.name}")

    def _set_closed(self, session: Session) -> None:
        if session.name in self._open_order:
            self._open_order.remove(session.name)
            self._write_open_list()

    def _write_open_list(self) -> None:
        configfiles.state['general']['open_sessions'] = ','.join(self._open_order)
        # Write right away, so a crash can't lose which sessions were open.
        objreg.get('save-manager').save('state-config', force=True, silent=True)


manager = cast(SessionManager, None)


def init() -> None:
    """Create the module-level manager and load its sessions."""
    global manager
    manager = SessionManager(pathlib.Path(standarddir.data()) / 'sessions')
    manager.load_all()
