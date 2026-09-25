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

    """One saved session on qute://sessions."""

    name: str
    container: str
    swatch: str
    is_open: bool
    counts: str
    last_saved: str
    closed_windows: list[ClosedWindowRow]


@dataclasses.dataclass(frozen=True)
class PrivateRow:

    """One open private session on qute://sessions."""

    name: str
    counts: str


def _plural(number: int, noun: str) -> str:
    return f'{number} {noun}' if number == 1 else f'{number} {noun}s'


def _counts(windows: int, tabs: int) -> str:
    return f"{_plural(windows, 'window')}, {_plural(tabs, 'tab')}"


def _live_counts(session: windowsessions.Session) -> str:
    tabs = sum(len(objreg.window_registry[win_id].tabbed_browser.widgets())
               for win_id in session.windows)
    return _counts(len(session.windows), tabs)


def _saved_counts(windows: Sequence[Mapping[str, Any]]) -> str:
    tabs = sum(len(window['tabs']) if isinstance(window.get('tabs'), list)
               else 0 for window in windows)
    return _counts(len(windows), tabs)


def _last_saved(session: windowsessions.Session) -> str:
    when = session.last_saved
    if when is None:
        # last_saved only covers saves in this run; the file's modification
        # time says when an earlier run saved it.
        try:
            mtime = windowsessions.manager.path_for(session).stat().st_mtime
        except OSError:
            return 'never'
        when = datetime.datetime.fromtimestamp(mtime)
    return when.strftime(_TIME_FORMAT)


def _closed_at(value: Any) -> str:
    """Format a closed-window timestamp in local time.

    YAML reads an unquoted ISO 8601 timestamp back as a datetime and a quoted
    one as a string, so both occur. Anything else is shown as it is.
    """
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, datetime.datetime):
        return str(value)
    if value.tzinfo is None:
        # closed_at is always UTC, even when it lost its offset.
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone().strftime(_TIME_FORMAT)


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


def _unreadable_paths(
        manager: windowsessions.SessionManager) -> list[pathlib.Path]:
    # The manager has no public query for these. E and F change
    # windowsessions.py in parallel with G, so adding one waits for the merge.
    # pylint: disable=protected-access
    return sorted(manager._base_path / f'{name}.yml'
                  for name in manager._unreadable)


@qutescheme.add_handler('sessions')
def qute_sessions(_url: QUrl) -> tuple[str, str]:
    """Handler for qute://sessions. Show every session."""
    manager = windowsessions.manager
    saved = [
        SessionRow(
            name=session.name,
            container=session.container,
            swatch=_swatch(session.container),
            is_open=session.is_open,
            counts=(_live_counts(session) if session.is_open
                    else _saved_counts(session.saved_windows)),
            last_saved=_last_saved(session),
            closed_windows=_closed_windows(session),
        )
        for session in manager.sessions() if not session.private
    ]
    private = [PrivateRow(name=session.name, counts=_live_counts(session))
               for session in manager.private_sessions()]
    return 'text/html', jinja.render(
        'sessions.html', title='Sessions', saved=saved, private=private,
        unreadable=_unreadable_paths(manager))
