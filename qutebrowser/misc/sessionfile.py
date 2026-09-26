# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The on-disk format of one session, data/sessions/<name>/session.yml.

The tab serialization is upstream's, moved here from misc/sessions.py.
"""

import dataclasses
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


def serialize_tab(tab, active):
    """Serialize a single tab with its history."""
    data: JsonType = {'history': []}
    if active:
        data['active'] = True

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
            if item_data.get('active', False) and data['history']:
                # mark entry before qute://back as active
                data['history'][-1]['active'] = True
        else:
            data['history'].append(item_data)
    data['id'] = tab.data.persistent_id
    return data


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
    return data


def tab_history(tab) -> bytes:
    """Get a tab's history in QtWebEngine's own format."""
    return bytes(tab.history.private_api.serialize())


def deserialize_tab(tab, history: bytes) -> None:
    """Replace a tab's history with saved bytes, loading its current page."""
    tab.history.private_api.deserialize(QByteArray(history))


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
    """Get the history bytes of a window's tabs, by tab id.

    A tab whose history can't be serialized (e.g. an internal page) keeps
    its readable history but gets no file. So does a tab whose bytes
    describe no entries (see `has_history`).
    """
    from qutebrowser.browser import browsertab
    history = {}
    for tab in window.tabbed_browser.widgets():
        try:
            data = tab_history(tab)
        except browsertab.WebTabError:
            continue
        if has_history(data):
            history[tab.data.persistent_id] = data
    return history


def _window_ids(window: Any) -> set[str]:
    if not isinstance(window, dict):
        return set()
    ids = set()
    tabs = window.get('tabs')
    if isinstance(tabs, list):
        ids.update(tab.get('id') for tab in tabs if isinstance(tab, dict))
    groups = window.get('closed_tabs')
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, list):
                ids.update(item.get('id') for item in group
                           if isinstance(item, dict))
    return ids


def referenced_ids(data: SessionData) -> set[str]:
    """Get the tab ids whose history files a session file refers to.

    Hand edits can break the shape; whatever doesn't parse refers to
    nothing, and invalid ids are never file names.
    """
    ids = set()
    for window in data.windows:
        ids |= _window_ids(window)
    for entry in data.closed_windows:
        if isinstance(entry, dict):
            ids |= _window_ids(entry.get('window'))
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
    """Get the ids of the tabs in a session's open windows."""
    ids = set()
    for win_id in session.windows:
        tabbed_browser = objreg.window_registry[win_id].tabbed_browser
        ids.update(tab.data.persistent_id for tab in tabbed_browser.widgets())
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
                 history: bytes | None = None):
    """Load saved tab data into a newly opened tab.

    Args:
        new_tab: The tab.
        data: The tab's readable data from the session file.
        history: The tab's history bytes, or None to load only its current
                 page from data, as upstream does.
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
    try:
        for i, tab in enumerate(data['tabs']):
            new_tab = tabbed_browser.tabopen(background=False)
            tab_id = _claim_id(tab.get('id'), used_ids)
            history = None
            tab_problems: list[str] = []
            if tab_id is None:
                tab_problems.append(_NO_ID)
            else:
                new_tab.data.persistent_id = tab_id
                history = _read_history(session, tab_id, tab_problems)
            _restore_tab(new_tab, tab, history)
            # A single entry has no back history to lose, and its file may
            # never have been written (see window_history).
            if len(tab['history']) > 1:
                problems += tab_problems
            if tab.get('active', False):
                tab_to_focus = i
            if new_tab.data.pinned:
                new_tab.set_pinned(True)
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
    if problems:
        _warn_without_history(problems)

    if show:
        window.show()
        if data.get('active', False):
            QTimer.singleShot(0, tabbed_browser.widget.activateWindow)
    return window
