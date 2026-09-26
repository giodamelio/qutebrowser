# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The on-disk format of one session, data/sessions/<name>/session.yml.

The tab serialization is upstream's, moved here from misc/sessions.py.
"""

import copy
import dataclasses
import datetime
import itertools
import pathlib
import struct
import urllib.parse
from typing import Any, TypeAlias
from collections.abc import MutableMapping, MutableSequence

import yaml
from qutebrowser.qt.core import Qt, QUrl, QPoint, QTimer, QDateTime, QByteArray

from qutebrowser.config import config
from qutebrowser.misc import historystore, objects
from qutebrowser.utils import log, message, objreg, qtutils, utils


JsonType: TypeAlias = MutableMapping[str, Any]
DEFAULT_CONTAINER = 'default'
_NO_ID = "no usable tab id"


class SessionFileError(Exception):

    """A session file could not be read, written, or restored."""


class TabHistoryItem:

    """A single item in the tab history.

    Attributes:
        url: The QUrl of this item.
        original_url: The QUrl of this item which was originally requested.
        title: The title as string of this item.
        active: Whether this item is the item currently navigated to.
        user_data: The user data for this item.
    """

    def __init__(self, url, title, *, original_url=None, active=False,
                 user_data=None, last_visited=None):
        self.url = url
        if original_url is None:
            self.original_url = url
        else:
            self.original_url = original_url
        self.title = title
        self.active = active
        self.user_data = user_data
        self.last_visited = last_visited

    def __repr__(self):
        return utils.get_repr(self, constructor=True, url=self.url,
                              original_url=self.original_url, title=self.title,
                              active=self.active, user_data=self.user_data,
                              last_visited=self.last_visited)


def _active_entry(items: list[JsonType]) -> JsonType | None:
    """Get the history entry a tab shows: the active one, else the last."""
    return next((item for item in items if item.get('active')),
                items[-1] if items else None)


def _entry_url(entry: JsonType | None) -> QUrl:
    if entry is None:
        return QUrl()
    return QUrl.fromEncoded(entry['url'].encode('ascii'))


@dataclasses.dataclass
class LazyHistory:

    """A restored tab's saved history, kept until the tab is first shown."""

    data: JsonType
    history: bytes

    @property
    def url(self) -> QUrl:
        return _entry_url(_active_entry(self.data['history']))

    @property
    def title(self) -> str:
        """The saved active entry's title, else the last entry's, else ''."""
        entry = _active_entry(self.data['history'])
        return '' if entry is None else entry['title']


@dataclasses.dataclass
class SessionData:

    """The contents of one session file."""

    container: str = DEFAULT_CONTAINER
    windows: list[JsonType] = dataclasses.field(default_factory=list)
    closed_windows: list[JsonType] = dataclasses.field(default_factory=list)


def read(path: pathlib.Path) -> SessionData:
    """Read a session file, also accepting upstream's format."""
    try:
        with path.open(encoding='utf-8') as f:
            raw = utils.yaml_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise SessionFileError(f"{path}: {e}")

    if not isinstance(raw, dict):
        raise SessionFileError(
            f"{path}: expected a mapping, got {type(raw).__name__}")

    container = raw.get('container', DEFAULT_CONTAINER)
    windows = raw.get('windows', [])
    closed_windows = raw.get('closed_windows', [])
    if not isinstance(container, str):
        raise SessionFileError(f"{path}: 'container' must be a string")
    for key, value in [('windows', windows), ('closed_windows', closed_windows)]:
        if (not isinstance(value, list) or
                not all(isinstance(win, dict) for win in value)):
            raise SessionFileError(f"{path}: '{key}' must be a list of mappings")

    kept = []
    for win in windows:
        if win.get('private'):
            # Written by upstream's :session-save --with-private. Private data
            # never belongs on disk, so it is not restored either.
            log.sessions.warning(f"{path}: skipping a private window")
            continue
        kept.append(win)
    return SessionData(container=container, windows=kept,
                       closed_windows=closed_windows)


def write(path: pathlib.Path, data: SessionData) -> None:
    """Atomically write a session file."""
    out: JsonType = {'container': data.container, 'windows': data.windows}
    if data.closed_windows:
        out['closed_windows'] = data.closed_windows
    try:
        with qtutils.savefile_open(str(path)) as f:
            utils.yaml_dump(out, f)
    except (OSError, UnicodeEncodeError, yaml.YAMLError) as e:
        raise SessionFileError(f"{path}: {e}")


def _serialize_tab_item(tab, idx, item):
    """Serialize a single history item of a tab."""
    data: JsonType = {
        'url': bytes(item.url().toEncoded()).decode('ascii'),
    }

    if item.title():
        data['title'] = item.title()
    elif tab.history.current_idx() == idx:
        # https://github.com/qutebrowser/qutebrowser/issues/879
        data['title'] = tab.title()
    else:
        data['title'] = data['url']

    if item.originalUrl() != item.url():
        encoded = item.originalUrl().toEncoded()
        data['original-url'] = bytes(encoded).decode('ascii')

    if tab.history.current_idx() == idx:
        data['active'] = True

    try:
        user_data = item.userData()
    except AttributeError:
        # QtWebEngine
        user_data = None

    data['last_visited'] = item.lastVisited().toString(Qt.DateFormat.ISODate)

    if tab.history.current_idx() == idx:
        pos = tab.scroller.pos_px()
        data['zoom'] = tab.zoom.factor()
        data['scroll-pos'] = {'x': pos.x(), 'y': pos.y()}
    elif user_data is not None:
        if 'zoom' in user_data:
            data['zoom'] = user_data['zoom']
        if 'scroll-pos' in user_data:
            pos = user_data['scroll-pos']
            data['scroll-pos'] = {'x': pos.x(), 'y': pos.y()}

    data['pinned'] = tab.data.pinned

    return data


def _serialize_history(tab) -> list[JsonType]:
    history: list[JsonType] = []
    for idx, item in enumerate(tab.history):
        qtutils.ensure_valid(item)
        item_data = _serialize_tab_item(tab, idx, item)

        if not item.url().isValid():
            # WORKAROUND Qt 6.5 regression
            # https://github.com/qutebrowser/qutebrowser/issues/7696
            log.sessions.debug(f"Skipping invalid history item: {item}")
            continue

        if item.url().scheme() == 'qute' and item.url().host() == 'back':
            # don't add qute://back to the session file
            if item_data.get('active', False) and history:
                # mark entry before qute://back as active
                history[-1]['active'] = True
        else:
            history.append(item_data)
    return history


def serialize_tab(tab, active):
    """Serialize a single tab with its history."""
    lazy = tab.data.lazy_history
    if lazy is None:
        data: JsonType = {'history': _serialize_history(tab)}
    else:
        # Never shown since it was restored, so what was saved is still
        # its history (§21.5).
        data = {key: value for key, value in lazy.data.items()
                if key not in ['active', 'id']}
        data['history'] = [dict(item, pinned=tab.data.pinned)
                           for item in lazy.data['history']]
    if active:
        data['active'] = True
    data['id'] = tab.data.persistent_id
    return data


def _serialize_closed_tabs(undo_stack) -> list[list[JsonType]]:
    """Serialize a window's :undo stack, newest first (§21.3).

    The stack is a deque capped by tabs.undo_stack_size, so it already
    holds only what that setting keeps.
    """
    return [
        [{
            'id': entry.tab_id,
            'index': entry.index,
            'pinned': entry.pinned,
            'closed_at': entry.created_at.astimezone(
                datetime.timezone.utc).isoformat(timespec='milliseconds'),
            'tab': entry.tab,
        } for entry in group]
        for group in reversed(undo_stack)
    ]


def serialize_window(window) -> JsonType:
    """Serialize a live MainWindow into the session file's window format."""
    tabbed_browser = window.tabbed_browser
    data: JsonType = {}
    active_window = objects.qapp.activeWindow()
    if getattr(active_window, 'win_id', None) == window.win_id:
        data['active'] = True
    data['geometry'] = bytes(window.saveGeometry())
    data['tabs'] = [
        serialize_tab(tab, i == tabbed_browser.widget.currentIndex())
        for i, tab in enumerate(tabbed_browser.widgets())
    ]
    closed_tabs = _serialize_closed_tabs(tabbed_browser.undo_stack)
    if closed_tabs:
        data['closed_tabs'] = closed_tabs
    return data


def tab_history(tab) -> bytes:
    """Get a tab's history in QtWebEngine's own format."""
    lazy = tab.data.lazy_history
    if lazy is not None:
        return lazy.history
    return bytes(tab.history.private_api.serialize())


def tab_url(tab) -> QUrl:
    """Get a tab's URL, the saved one while it waits to be shown (§21.5)."""
    lazy = tab.data.lazy_history
    return tab.url() if lazy is None else lazy.url


def tab_title(tab) -> str:
    """Get a tab's title, the saved one while it waits to be shown (§21.5)."""
    lazy = tab.data.lazy_history
    return tab.title() if lazy is None else lazy.title


def deserialize_tab(tab, history: bytes) -> None:
    """Replace a tab's history with saved bytes, loading its current page."""
    tab.history.private_api.deserialize(QByteArray(history))


def load_lazy_history(tab) -> None:
    """Load a lazily restored tab's history the first time it is shown."""
    lazy = tab.data.lazy_history
    if lazy is None:
        return
    tab.data.lazy_history = None
    try:
        deserialize_tab(tab, lazy.history)
    except OSError as e:
        # The window is open already, so unlike in restore_window it can't
        # fail as a whole.
        message.error(f"Failed to restore the history of "
                      f"{lazy.url.toDisplayString()}: {e}")
        _restore_tab(tab, lazy.data)


def take_tab_history(new_tab, tab, history: bytes) -> None:
    """Load the history bytes tab_history got from another tab."""
    lazy = tab.data.lazy_history
    if lazy is None:
        deserialize_tab(new_tab, history)
        return
    # Copied, as the fallback marks entries inactive and a kept tab still
    # needs its own.
    new_tab.data.lazy_history = LazyHistory(data=copy.deepcopy(lazy.data),
                                            history=history)
    load_lazy_history(new_tab)


def restore_tab_history(tab, data: JsonType) -> None:
    """Load only a tab's current page from its readable session data."""
    _restore_tab(tab, data)


def _closed_time(value: Any) -> datetime.datetime:
    """Turn a saved closed_at into the naive local time :undo uses."""
    if not isinstance(value, datetime.datetime):
        # fromisoformat only takes a trailing 'Z' from Python 3.11 on.
        value = datetime.datetime.fromisoformat(
            str(value).replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone().replace(tzinfo=None)


def _restore_closed_tabs(data: JsonType, session, used_ids: set[str],
                         problems: list[str]) -> list[list[Any]]:
    """Rebuild a window's :undo stack, oldest first, from closed_tabs."""
    # tabbedbrowser imports this module.
    from qutebrowser.mainwindow import tabbedbrowser
    groups = []
    for group in data.get('closed_tabs', []):
        entries = []
        for item in group:
            index, pinned = item['index'], item['pinned']
            if not isinstance(index, int) or not isinstance(pinned, bool):
                raise TypeError(f"invalid closed tab {item!r}")
            # Checked now, as restore_window checks open tabs: :undo can't
            # fail the window any more.
            for histentry in item['tab']['history']:
                if (not isinstance(histentry['url'], str) or
                        not isinstance(histentry['title'], str)):
                    raise TypeError(f"invalid closed tab {item!r}")
            tab_id = _claim_id(item.get('id'), used_ids)
            history = None
            tab_problems: list[str] = []
            if tab_id is None:
                tab_id = historystore.new_id()
                tab_problems.append(_NO_ID)
            else:
                history = _read_history(session, tab_id, tab_problems)
            tab_data = dict(item['tab'], id=tab_id)
            # As for open tabs in restore_window.
            if len(tab_data['history']) > 1:
                problems += tab_problems
            entries.append(tabbedbrowser._UndoEntry(  # pylint: disable=protected-access
                url=_entry_url(_active_entry(tab_data['history'])),
                history=(None if history is None
                         else historystore.Snapshot(history)),
                index=index,
                pinned=pinned,
                created_at=_closed_time(item['closed_at']),
                tab_id=tab_id,
                tab=tab_data))
        groups.append(entries)
    groups.reverse()
    return groups


_HISTORY_HEADER = struct.Struct('>IIi')


def has_history(data: bytes) -> bool:
    """Check whether serialized tab history bytes describe any entries.

    WebEngineHistoryPrivate.serialize() can return a canonical count-0 stub
    (QTBUG-117489) if Qt is caught mid-load; writing that over a good
    history file would silently erase a tab's history on the next
    autosave, so it must never be treated as real data. Anything too short
    to hold a header can't describe an entry either.
    """
    if len(data) < _HISTORY_HEADER.size:
        return False
    _version, count, _current = _HISTORY_HEADER.unpack_from(data)
    return count > 0


def window_history(window) -> dict[str, bytes]:
    """Get the history bytes of a window's open and closed tabs, by tab id.

    A tab whose history can't be serialized (e.g. an internal page) keeps
    its readable history but gets no file. So does a tab whose bytes
    describe no entries (see `has_history`).
    """
    from qutebrowser.browser import browsertab
    tabbed_browser = window.tabbed_browser
    history = {}
    for group in tabbed_browser.undo_stack:
        for entry in group:
            # None for a tab restored without its history file.
            if entry.history is not None and has_history(entry.history):
                history[entry.tab_id] = entry.history
    for tab in tabbed_browser.widgets():
        try:
            data = tab_history(tab)
        except browsertab.WebTabError:
            continue
        if has_history(data):
            history[tab.data.persistent_id] = data
    return history


def _window_ids(window: Any, strict: bool) -> set[str]:
    def items(value: Any, kind: type) -> list[Any]:
        if isinstance(value, list) and all(isinstance(item, kind)
                                           for item in value):
            return value
        if strict:
            raise ValueError(f"expected a list of {kind.__name__}, got "
                             f"{type(value).__name__}")
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, kind)]

    if not isinstance(window, dict):
        if strict:
            raise ValueError(
                f"expected a window mapping, got {type(window).__name__}")
        return set()
    tabs = items(window.get('tabs'), dict)
    groups = items(window.get('closed_tabs', []), list)
    closed = [item for group in groups for item in items(group, dict)]
    return {tab.get('id') for tab in tabs + closed}


def referenced_ids(data: SessionData, *, strict: bool = False) -> set[str]:
    """Get the tab ids whose history files a session file refers to.

    Hand edits can break the shape; whatever doesn't parse refers to
    nothing, and invalid ids are never file names.

    Args:
        data: The session file's contents.
        strict: Raise ValueError instead for a window whose tab ids can't
                all be found, as its files may still be wanted.
    """
    ids = set()
    for window in data.windows:
        ids |= _window_ids(window, strict)
    for entry in data.closed_windows:
        ids |= _window_ids(
            entry.get('window') if isinstance(entry, dict) else None, strict)
    return {tab_id for tab_id in ids if historystore.is_valid_id(tab_id)}


def _claim_id(value: Any, used_ids: set[str]) -> str | None:
    """Get a saved tab id, unless it is invalid or another tab has it.

    Two tabs with one id would share, and overwrite, one history file.
    """
    if not historystore.is_valid_id(value) or value in used_ids:
        return None
    used_ids.add(value)
    return value


def _live_ids(session) -> set[str]:
    """Get the ids of the open and closed tabs of a session's open windows."""
    ids = set()
    for win_id in session.windows:
        tabbed_browser = objreg.window_registry[win_id].tabbed_browser
        ids.update(tab.data.persistent_id for tab in tabbed_browser.widgets())
        ids.update(entry.tab_id for group in tabbed_browser.undo_stack
                   for entry in group)
    return ids


def _read_history(session, tab_id: str, problems: list[str]) -> bytes | None:
    """Get a restored tab's history bytes, noting why when there are none."""
    # mainwindow imports windowsessions, which imports this module.
    from qutebrowser.mainwindow import windowsessions
    try:
        return windowsessions.manager.read_history(session, tab_id)
    except historystore.UnusableHistoryError as e:
        problems.append(e.reason)
        return None


def _warn_without_history(problems: list[str]) -> None:
    count = len(problems)
    tabs = 'tab' if count == 1 else 'tabs'
    reasons = '; '.join(dict.fromkeys(problems))
    message.warning(f"{count} {tabs} restored without back history: {reasons}")


def _restore_tab(new_tab, data,  # noqa: C901
                 history: bytes | None = None, *, lazy: bool = False):
    """Load saved tab data into a newly opened tab.

    Args:
        new_tab: The tab.
        data: The tab's readable data from the session file.
        history: The tab's history bytes, or None to load only its current
                 page from data, as upstream does.
        lazy: Keep the history bytes until the tab is first shown.
    """
    entries = []
    lazy_load: MutableSequence[JsonType] = []
    # use len(data['history'])
    # -> dropwhile empty if not session.lazy_session
    lazy_index = len(data['history'])
    gen = itertools.chain(
        itertools.takewhile(lambda _: not lazy_load,
                            enumerate(data['history'])),
        enumerate(lazy_load),
        itertools.dropwhile(lambda i: i[0] < lazy_index,
                            enumerate(data['history'])))

    for i, histentry in gen:
        user_data = {}

        if 'zoom' in data:
            # The zoom was accidentally stored in 'data' instead of per-tab
            # earlier.
            # See https://github.com/qutebrowser/qutebrowser/issues/728
            user_data['zoom'] = data['zoom']
        elif 'zoom' in histentry:
            user_data['zoom'] = histentry['zoom']

        if 'scroll-pos' in data:
            # The scroll position was accidentally stored in 'data' instead
            # of per-tab earlier.
            # See https://github.com/qutebrowser/qutebrowser/issues/728
            pos = data['scroll-pos']
            user_data['scroll-pos'] = QPoint(pos['x'], pos['y'])
        elif 'scroll-pos' in histentry:
            pos = histentry['scroll-pos']
            user_data['scroll-pos'] = QPoint(pos['x'], pos['y'])

        if 'pinned' in histentry:
            new_tab.data.pinned = histentry['pinned']

        if (config.val.session.lazy_restore and history is None and
                histentry.get('active', False) and
                not histentry['url'].startswith('qute://back')):
            # remove "active" mark and insert back page marked as active
            lazy_index = i + 1
            lazy_load.append({
                'title': histentry['title'],
                'url':
                    'qute://back#' +
                    urllib.parse.quote(histentry['title']),
                'active': True
            })
            histentry['active'] = False

        active = histentry.get('active', False)
        url = QUrl.fromEncoded(histentry['url'].encode('ascii'))

        if 'original-url' in histentry:
            orig_url = QUrl.fromEncoded(
                histentry['original-url'].encode('ascii'))
        else:
            orig_url = url

        if histentry.get("last_visited"):
            last_visited: QDateTime | None = QDateTime.fromString(
                histentry.get("last_visited"),
                Qt.DateFormat.ISODate,
            )
        else:
            last_visited = None

        entry = TabHistoryItem(url=url, original_url=orig_url,
                               title=histentry['title'], active=active,
                               user_data=user_data,
                               last_visited=last_visited)
        entries.append(entry)
        if active:
            new_tab.title_changed.emit(histentry['title'])

    if history is None:
        new_tab.history.private_api.load_items(entries)
    elif lazy:
        new_tab.data.lazy_history = LazyHistory(data=data, history=history)
    else:
        deserialize_tab(new_tab, history)


def restore_window(data: JsonType, session, *,  # noqa: C901
                   show: bool = True):
    """Create a MainWindow in session from saved window data."""
    # mainwindow imports windowsessions, which imports this module.
    from qutebrowser.mainwindow import mainwindow, windowsessions
    geometry = data.get('geometry')
    if geometry is not None and not isinstance(geometry, bytes):
        raise SessionFileError(
            f"Session {session.name} has a window with invalid geometry")
    used_ids = _live_ids(session)
    window = mainwindow.MainWindow(geometry=geometry, session=session)
    tabbed_browser = window.tabbed_browser
    tab_to_focus = None
    problems: list[str] = []
    # With tabs_are_windows every tab gets its own window, where it would
    # never be shown in this window's tab bar.
    lazy = (config.val.session.lazy_restore and
            not config.val.tabs.tabs_are_windows)
    try:
        tabs = data['tabs']
        # Without an active tab, the last one opened stays current; with
        # several (hand edits), the last one is focused below.
        shown = max((i for i, tab in enumerate(tabs)
                     if tab.get('active', False)), default=len(tabs) - 1)
        for i, tab in enumerate(tabs):
            new_tab = tabbed_browser.tabopen(background=False)
            tab_id = _claim_id(tab.get('id'), used_ids)
            history = None
            tab_problems: list[str] = []
            if tab_id is None:
                tab_problems.append(_NO_ID)
            else:
                new_tab.data.persistent_id = tab_id
                history = _read_history(session, tab_id, tab_problems)
            _restore_tab(new_tab, tab, history, lazy=lazy and i != shown)
            # A single entry has no back history to lose, and its file may
            # never have been written (see window_history).
            if len(tab['history']) > 1:
                problems += tab_problems
            if tab.get('active', False):
                tab_to_focus = i
            if new_tab.data.pinned:
                new_tab.set_pinned(True)
        tabbed_browser.undo_stack.extend(
            _restore_closed_tabs(data, session, used_ids, problems))
    # OSError: QtWebEngine refused bytes of its own version, which only
    # a corrupt file can explain (§21.5).
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as e:
        # Not close(): that saves the session with this half-built window
        # before the caller can move the session file aside.
        windowsessions.manager.remove_window(session, window.win_id)
        tabbed_browser.shutdown()
        window.deleteLater()
        raise SessionFileError(
            f"Session {session.name} has an invalid window: "
            f"{type(e).__name__}: {e}")
    if tab_to_focus is not None:
        tabbed_browser.widget.setCurrentIndex(tab_to_focus)
    if lazy:
        tabbed_browser.current_tab_changed.connect(load_lazy_history)
    if problems:
        _warn_without_history(problems)

    if show:
        window.show()
        if data.get('active', False):
            QTimer.singleShot(0, tabbed_browser.widget.activateWindow)
    return window
