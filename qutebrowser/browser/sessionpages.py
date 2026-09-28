# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The read-only qute://containers and qute://sessions pages."""

import dataclasses
import datetime
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from qutebrowser.qt.core import QUrl

from qutebrowser.browser import qutescheme
from qutebrowser.browser.webengine import profiles
from qutebrowser.config import configtypes
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import containers
from qutebrowser.utils import jinja, objreg


_SOURCES = {'builtin': 'built-in', 'runtime': 'runtime', 'declared': 'declared'}
_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


@dataclasses.dataclass(frozen=True)
class ContainerRow:

    """One container on qute://containers."""

    name: str
    swatch: str
    source: str
    sessions: list[tuple[str, bool]]
    loaded: bool
    unused: bool


def _swatch(container: str) -> str:
    """Get a container's color as #rrggbb, whichever format defines it."""
    color = containers.registry.get(container).color
    qcolor = configtypes.QtColor().to_py(color)
    assert qcolor is not None, color
    return qcolor.name()


@qutescheme.add_handler('containers')
def qute_containers(_url: QUrl) -> tuple[str, str]:
    """Handler for qute://containers. Show every container."""
    rows = []
    for container in containers.registry.containers():
        using = windowsessions.manager.sessions_using(container.name)
        rows.append(ContainerRow(
            name=container.name,
            swatch=_swatch(container.name),
            source=_SOURCES[container.source],
            sessions=[(session.name, session.is_open) for session in using],
            loaded=profiles.get_registry().is_loaded(container.name),
            unused=container.source == 'runtime' and not using,
        ))
    return 'text/html', jinja.render('containers.html', title='Containers',
                                     rows=rows)


@dataclasses.dataclass(frozen=True)
class ClosedWindowRow:

    """One entry of a session's closed-window history."""

    index: int
    closed_at: str
    tabs: int
    title: str


@dataclasses.dataclass(frozen=True)
class SessionRow:

    """One session on qute://sessions: saved, or open and private."""

    name: str
    container: str | None
    swatch: str | None
    state: str
    windows: int
    tabs: int
    last_saved: datetime.datetime | None
    closed_windows: list[ClosedWindowRow]

    @property
    def last_saved_text(self) -> str:
        """Get the last save time as the page shows it."""
        if self.last_saved is None:
            return 'never'
        return self.last_saved.strftime(_TIME_FORMAT)


@dataclasses.dataclass(frozen=True)
class UnreadableRow:

    """A session directory whose session.yml couldn't be used.

    Its session can still be listed: a directory that couldn't be moved
    aside keeps the in-memory session that replaced it, which isn't saved.
    """

    path: pathlib.Path
    name: str
    listed: bool


def _live_counts(session: windowsessions.Session) -> tuple[int, int]:
    tabs = sum(len(objreg.window_registry[win_id].tabbed_browser.widgets())
               for win_id in session.windows)
    return len(session.windows), tabs


def _saved_counts(windows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    tabs = sum(len(window['tabs']) if isinstance(window.get('tabs'), list)
               else 0 for window in windows)
    return len(windows), tabs


def _last_saved(
        session: windowsessions.Session) -> datetime.datetime | None:
    if session.last_saved is not None:
        return session.last_saved
    # last_saved only covers saves in this run; the file's modification
    # time says when an earlier run saved it.
    try:
        mtime = windowsessions.manager.path_for(session).stat().st_mtime
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(mtime)


def _normalize_iso_z(value: str) -> str:
    """Rewrite a trailing 'Z' to '+00:00'.

    fromisoformat only accepts a bare 'Z' suffix from Python 3.11; setup.py
    allows 3.10, where it would otherwise raise ValueError.
    """
    return value[:-1] + '+00:00' if value.endswith('Z') else value


def _closed_at(value: Any) -> str:
    """Format a closed-window timestamp in local time.

    YAML reads an unquoted ISO 8601 timestamp back as a datetime and a quoted
    one as a string, so both occur. Anything else is shown as it is.
    """
    if value is None:
        return 'unknown'
    raw = value
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(_normalize_iso_z(value))
        except ValueError:
            return value
    if not isinstance(value, datetime.datetime):
        return str(value)
    if value.tzinfo is None:
        # closed_at is always UTC, even when it lost its offset.
        value = value.replace(tzinfo=datetime.timezone.utc)
    try:
        return value.astimezone().strftime(_TIME_FORMAT)
    except (OverflowError, ValueError):
        # A date too far from year 1 to convert into a timezone behind UTC.
        return str(raw)


def _first_title(window: Mapping[str, Any]) -> str:
    """Get the title of the page the window's first tab showed."""
    tabs = window.get('tabs', [])
    if not isinstance(tabs, list) or not tabs or not isinstance(tabs[0], Mapping):
        return ''
    history = tabs[0].get('history', [])
    items = [item for item in history if isinstance(item, Mapping)] \
        if isinstance(history, list) else []
    if not items:
        return ''
    item = next((item for item in items if item.get('active')), items[-1])
    return item.get('title') or item.get('url', '')


def _closed_windows(session: windowsessions.Session) -> list[ClosedWindowRow]:
    rows = []
    # Numbered from 1, newest first, as :session-restore-window counts them.
    for index, entry in enumerate(session.closed_windows, start=1):
        # closed_windows comes from hand-editable session files (or direct
        # mutation), so entry and window shapes aren't guaranteed.
        if not isinstance(entry, Mapping):
            entry = {}
        window = entry.get('window', {})
        if not isinstance(window, Mapping):
            window = {}
        tabs = window.get('tabs', [])
        rows.append(ClosedWindowRow(
            index=index,
            closed_at=_closed_at(entry.get('closed_at', 'unknown')),
            tabs=len(tabs) if isinstance(tabs, list) else 0,
            title=_first_title(window),
        ))
    return rows


@qutescheme.add_handler('sessions')
def qute_sessions(_url: QUrl) -> tuple[str, str]:
    """Handler for qute://sessions. Show every session."""
    manager = windowsessions.manager
    saved = [
        SessionRow(
            name=session.name,
            container=session.container,
            swatch=_swatch(session.container),
            state='open' if session.is_open else 'closed',
            windows=windows,
            tabs=tabs,
            last_saved=_last_saved(session),
            closed_windows=_closed_windows(session),
        )
        for session in manager.sessions() if not session.private
        for windows, tabs in [_live_counts(session) if session.is_open
                              else _saved_counts(session.saved_windows)]
    ]
    private = [
        SessionRow(name=session.name, container=None, swatch=None,
                   state='private', windows=windows, tabs=tabs,
                   last_saved=None, closed_windows=[])
        for session in manager.private_sessions()
        for windows, tabs in [_live_counts(session)]
    ]
    listed = {row.name for row in saved}
    unreadable = [UnreadableRow(path=path, name=path.name,
                                listed=path.name in listed)
                  for path in manager.unreadable_paths()]
    # Stable, so sessions saved at the same time (or never) keep name order.
    rows = sorted(saved + private, reverse=True,
                  key=lambda row: row.last_saved or datetime.datetime.min)
    return 'text/html', jinja.render(
        'sessions.html', title='Sessions', rows=rows, unreadable=unreadable,
        set_aside=manager.set_aside_paths())


def open_page(url: str, win_id: int, *, tab: bool, bg: bool,
              window: bool) -> None:
    """Open one of these pages from a command, the way :open does."""
    dispatcher = objreg.get('command-dispatcher', scope='window',
                            window=win_id, from_command=True)
    dispatcher.openurl(url, tab=tab, bg=bg, window=window)
