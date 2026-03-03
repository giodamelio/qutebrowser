# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Management of sessions - saved tabs/windows."""

import os
import os.path
import itertools
import urllib
import shutil
import pathlib
from typing import Any, Optional, Union, cast
from collections.abc import Iterable, MutableMapping, MutableSequence

from qutebrowser.qt.core import Qt, QUrl, QObject, QPoint, QTimer, QDateTime
import yaml

from qutebrowser.utils import (standarddir, objreg, qtutils, log, message,
                               utils, usertypes)
from qutebrowser.api import cmdutils
from qutebrowser.config import config, configfiles
from qutebrowser.completion.models import miscmodels
from qutebrowser.mainwindow import mainwindow
from qutebrowser.qt import sip
from qutebrowser.misc import objects, throttle


_JsonType = MutableMapping[str, Any]


class Sentinel:

    """Sentinel value for default argument."""


default = Sentinel()
session_manager = cast('SessionManager', None)

ArgType = Union[str, Sentinel]


def init(parent=None):
    """Initialize sessions.

    Args:
        parent: The parent to use for the SessionManager.
    """
    base_path = pathlib.Path(standarddir.data()) / 'sessions'

    # WORKAROUND for https://github.com/qutebrowser/qutebrowser/issues/5359
    backup_path = base_path / 'before-qt-515'
    do_backup = objects.backend == usertypes.Backend.QtWebEngine

    if base_path.exists() and not backup_path.exists() and do_backup:
        backup_path.mkdir()
        for path in base_path.glob('*.yml'):
            shutil.copy(path, backup_path)

    base_path.mkdir(exist_ok=True)

    global session_manager
    session_manager = SessionManager(str(base_path), parent)


def shutdown(session: Optional[ArgType], last_window: bool) -> None:
    """Handle a shutdown by saving sessions and removing the autosave file.

    With per-window sessions, each active session is saved separately and
    untagged windows are saved to _autosave.
    """
    if session_manager is None:
        return  # type: ignore[unreachable]

    try:
        if session is not None:
            session_manager.save(session, last_window=last_window,
                                 load_next_time=True,
                                 session_name=str(session)
                                 if not isinstance(session, Sentinel)
                                 else None)
        elif config.val.auto_save.session:
            session_manager.save_all_sessions(last_window=last_window,
                                              load_next_time=True)
    except SessionError as e:
        log.sessions.error("Failed to save session: {}".format(e))

    session_manager.delete_autosave()


class SessionError(Exception):

    """Exception raised when a session failed to load/save."""


class SessionNotFoundError(SessionError):

    """Exception raised when a session to be loaded was not found."""


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


class SessionManager(QObject):

    """Manager for sessions.

    Attributes:
        _base_path: The path to store sessions under.
        _last_window_session: The session data of the last window which was
                              closed.
        did_load: Set when a session was loaded.
    """

    def __init__(self, base_path, parent=None):
        super().__init__(parent)
        self._base_path = base_path
        self._last_window_session = None
        self.did_load = False
        # throttle autosaves to one minute apart
        self.save_autosave = throttle.Throttle(self._save_autosave, 60 * 1000)

    def _get_session_path(self, name, check_exists=False):
        """Get the session path based on a session name or absolute path.

        Args:
            name: The name of the session.
            check_exists: Whether it should also be checked if the session
                          exists.
        """
        path = os.path.expanduser(name)
        if os.path.isabs(path) and ((not check_exists) or
                                    os.path.exists(path)):
            return path
        else:
            path = os.path.join(self._base_path, name + '.yml')
            if check_exists and not os.path.exists(path):
                raise SessionNotFoundError(path)
            return path

    def exists(self, name):
        """Check if a named session exists."""
        try:
            self._get_session_path(name, check_exists=True)
        except SessionNotFoundError:
            return False
        else:
            return True

    def _save_tab_item(self, tab, idx, item):
        """Save a single history item in a tab.

        Args:
            tab: The tab to save.
            idx: The index of the current history item.
            item: The history item.

        Return:
            A dict with the saved data for this item.
        """
        data: _JsonType = {
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

    def _save_tab(self, tab, active, with_history=True):
        """Get a dict with data for a single tab.

        Args:
            tab: The WebView to save.
            active: Whether the tab is currently active.
            with_history: Include the tab's history.
        """
        data: _JsonType = {'history': []}
        if active:
            data['active'] = True

        history = tab.history if with_history else [tab.history.current_item()]

        for idx, item in enumerate(history):
            qtutils.ensure_valid(item)
            item_data = self._save_tab_item(tab, idx, item)

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
        return data

    def _save_all(self, *, only_window=None, session_name=None,
                   with_private=False, with_history=True):
        """Get a dict with data for all windows/tabs.

        Args:
            only_window: If set, only save the specified window.
            session_name: Filter windows by session name.
                None means no filter (save all windows).
                A string means only windows with that session_name.
            with_private: Include private windows.
            with_history: Include tab history.
        """
        data: _JsonType = {'windows': []}
        if only_window is not None:
            winlist: Iterable[int] = [only_window]
        else:
            winlist = objreg.window_registry

        for win_id in sorted(winlist):
            tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                        window=win_id)
            main_window = objreg.get('main-window', scope='window',
                                     window=win_id)

            # We could be in the middle of destroying a window here
            if sip.isdeleted(main_window):
                continue

            if tabbed_browser.is_private and not with_private:
                continue

            # Filter by session name if requested
            win_session = getattr(main_window, 'session_name', None)
            if session_name is not None and win_session != session_name:
                continue

            win_data: _JsonType = {}
            active_window = objects.qapp.activeWindow()
            if getattr(active_window, 'win_id', None) == win_id:
                win_data['active'] = True
            win_data['geometry'] = bytes(main_window.saveGeometry())
            win_data['tabs'] = []
            if tabbed_browser.is_private:
                win_data['private'] = True
            win_data['session'] = win_session
            for i, tab in enumerate(tabbed_browser.widgets()):
                active = i == tabbed_browser.widget.currentIndex()
                win_data['tabs'].append(self._save_tab(tab, active,
                                                       with_history=with_history))
            data['windows'].append(win_data)
        return data

    def _get_session_name(self, name, win_id=None):
        """Helper for save to get the name to save the session to.

        Args:
            name: The name of the session to save, or the 'default' sentinel
                  object.
            win_id: The window ID to get the session name from, if name is
                    the default sentinel.
        """
        if name is default:
            name = config.val.session.default_name
            if name is None and win_id is not None:
                main_window = objreg.get('main-window', scope='window',
                                         window=win_id)
                name = getattr(main_window, 'session_name', None)
            if name is None:
                name = 'default'
        return name

    def save(self, name, last_window=False, load_next_time=False,
             only_window=None, with_private=False, with_history=True,
             session_name=None):
        """Save a named session.

        Args:
            name: The name of the session to save, or the 'default' sentinel
                  object.
            last_window: If set, saves the saved self._last_window_session
                         instead of the currently open state.
            load_next_time: If set, prepares this session to be load next time.
            only_window: If set, only tabs in the specified window is saved.
            with_private: Include private windows.
            with_history: Include tab history.
            session_name: Filter by session name (None=no filter,
                          string=that session).

        Return:
            The name of the saved session.
        """
        name = self._get_session_name(name)
        path = self._get_session_path(name)

        log.sessions.debug("Saving session {} to {}...".format(name, path))
        if last_window:
            data = self._last_window_session
            if data is None:
                log.sessions.error("last_window_session is None while saving!")
                return None
        else:
            data = self._save_all(only_window=only_window,
                                  session_name=session_name,
                                  with_private=with_private,
                                  with_history=with_history)
        log.sessions.vdebug(  # type: ignore[attr-defined]
            "Saving data: {}".format(data))
        try:
            with qtutils.savefile_open(path) as f:
                utils.yaml_dump(data, f)
        except (OSError, UnicodeEncodeError, yaml.YAMLError) as e:
            raise SessionError(e)

        if load_next_time:
            configfiles.state['general']['session'] = name
        return name

    def _save_autosave(self):
        """Save the current state for crash recovery."""
        try:
            self.save('_autosave')
        except SessionError as e:
            log.sessions.error(
                "Failed to save autosave session: {}".format(e))

    def delete_autosave(self):
        """Delete the autosave session."""
        # cancel any in-flight saves
        self.save_autosave.cancel()
        try:
            self.delete('_autosave')
        except SessionNotFoundError:
            # Exiting before the first load finished
            pass
        except SessionError as e:
            log.sessions.error("Failed to delete autosave session: {}"
                               .format(e))

    def save_last_window_session(self):
        """Temporarily save the session for the last closed window."""
        self._last_window_session = self._save_all()


    def save_all_sessions(self, last_window=False, load_next_time=False):
        """Save all active sessions.

        Args:
            last_window: If set, use the saved _last_window_session data.
            load_next_time: If set, store session list for restore on startup.
        """
        if last_window:
            data = self._last_window_session
            if data is None:
                log.sessions.error("last_window_session is None!")
                return
            # Split saved data by session
            session_groups: dict[str, list] = {}
            for win in data['windows']:
                sname = win.get('session', 'default')
                session_groups.setdefault(sname, []).append(win)

            for sname, windows in session_groups.items():
                file_name = sname
                session_data: _JsonType = {'windows': windows}
                path = self._get_session_path(file_name)
                try:
                    with qtutils.savefile_open(path) as f:
                        utils.yaml_dump(session_data, f)
                except (OSError, UnicodeEncodeError, yaml.YAMLError) as e:
                    log.sessions.error(
                        "Failed to save session {}: {}".format(
                            file_name, e))
        else:
            # Save from live windows
            session_names = set()
            for win_id in objreg.window_registry:
                try:
                    main_window = objreg.get('main-window', scope='window',
                                             window=win_id)
                except KeyError:
                    continue
                if not sip.isdeleted(main_window):
                    sname = getattr(main_window, 'session_name', 'default')
                    session_names.add(sname)

            for sname in session_names:
                try:
                    self.save(sname, session_name=sname)
                except SessionError as e:
                    log.sessions.error(
                        "Failed to save session {}: {}".format(sname, e))

        if load_next_time:
            named = set()
            if last_window and data is not None:
                named = set(session_groups)
            else:
                for win_id in objreg.window_registry:
                    try:
                        main_window = objreg.get('main-window',
                                                 scope='window',
                                                 window=win_id)
                    except KeyError:
                        continue
                    if not sip.isdeleted(main_window):
                        sname = getattr(main_window, 'session_name', 'default')
                        named.add(sname)
            if named:
                configfiles.state['general']['sessions'] = (
                    ','.join(sorted(named)))

    def _load_tab(self, new_tab, data):  # noqa: C901
        """Load yaml data into a newly opened tab."""
        entries = []
        lazy_load: MutableSequence[_JsonType] = []
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

            if (config.val.session.lazy_restore and
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
                last_visited: Optional[QDateTime] = QDateTime.fromString(
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

        try:
            new_tab.history.private_api.load_items(entries)
        except ValueError as e:
            raise SessionError(e)

    def _load_window(self, win, session_name=None):
        """Turn yaml data into windows.

        Args:
            win: The YAML data for the window.
            session_name: Session name to set on the window. If the YAML data
                          has a 'session' key, that takes precedence.
        """
        effective_session = win.get('session', session_name) or 'default'
        window = mainwindow.MainWindow(geometry=win['geometry'],
                                       private=win.get('private', None),
                                       session_name=effective_session)
        tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                    window=window.win_id)
        tab_to_focus = None
        for i, tab in enumerate(win['tabs']):
            new_tab = tabbed_browser.tabopen(background=False)
            self._load_tab(new_tab, tab)
            if tab.get('active', False):
                tab_to_focus = i
            if new_tab.data.pinned:
                new_tab.set_pinned(True)
        if tab_to_focus is not None:
            tabbed_browser.widget.setCurrentIndex(tab_to_focus)

        window.show()
        if win.get('active', False):
            QTimer.singleShot(0, tabbed_browser.widget.activateWindow)

    def load(self, name, temp=False):
        """Load a named session.

        Args:
            name: The name of the session to load.
            temp: If given, don't tag loaded windows with the session name.
        """
        path = self._get_session_path(name, check_exists=True)
        try:
            with open(path, encoding='utf-8') as f:
                data = utils.yaml_load(f)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            raise SessionError(e)

        log.sessions.debug("Loading session {} from {}...".format(name, path))
        if data is None:
            raise SessionError("Got empty session file")

        if qtutils.is_single_process():
            if any(win.get('private') for win in data['windows']):
                raise SessionError("Can't load a session with private windows "
                                   "in single process mode.")

        # Determine session name for loaded windows
        # Internal sessions (starting with _) and temp loads don't tag windows
        effective_session = (name if (not name.startswith('_') and not temp)
                            else 'default')

        for win in data['windows']:
            self._load_window(win, session_name=effective_session)

        if data['windows']:
            self.did_load = True

    def delete(self, name):
        """Delete a session."""
        path = self._get_session_path(name, check_exists=True)
        try:
            os.remove(path)
        except OSError as e:
            raise SessionError(e)

    def list_sessions(self):
        """Get a list of all session names."""
        sessions = []
        for filename in os.listdir(self._base_path):
            base, ext = os.path.splitext(filename)
            if ext == '.yml':
                sessions.append(base)
        return sorted(sessions)


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
def session_load(name: str, *,
                 clear: bool = False,
                 temp: bool = False,
                 force: bool = False,
                 delete: bool = False) -> None:
    """Load a session.

    Args:
        name: The name of the session.
        clear: Close all existing windows.
        temp: Don't tag loaded windows with the session name.
        force: Force loading internal sessions (starting with an underline).
        delete: Delete the saved session once it has loaded.
    """
    if name.startswith('_') and not force:
        raise cmdutils.CommandError("{} is an internal session, use --force "
                                    "to load anyways.".format(name))
    old_windows = list(objreg.window_registry.values())
    try:
        session_manager.load(name, temp=temp)
    except SessionNotFoundError:
        raise cmdutils.CommandError("Session {} not found!".format(name))
    except SessionError as e:
        raise cmdutils.CommandError("Error while loading session: {}"
                                    .format(e))
    if clear:
        for win in old_windows:
            win.close()
    if delete:
        try:
            session_manager.delete(name)
        except SessionError as e:
            log.sessions.exception("Error while deleting session!")
            raise cmdutils.CommandError("Error while deleting session: {}"
                                        .format(e))
        log.sessions.debug("Loaded & deleted session {}.".format(name))


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def session_assign(name: str = None, *, win_id: int = None) -> None:
    """Assign the current window to a session.

    Args:
        name: The session name. If not given, assigns to 'default'.
    """
    if name is not None and name.startswith('_'):
        raise cmdutils.CommandError(
            "{} is an internal session name.".format(name))
    main_window = objreg.get('main-window', scope='window', window=win_id)
    main_window.session_name = name if name is not None else 'default'
    if name:
        message.info("Window assigned to session '{}'.".format(name))
    else:
        message.info("Window reassigned to default session.")


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def session_close(name: str = None, *, force: bool = False,
                  win_id: int = None) -> None:
    """Save a session and close all windows belonging to it.

    Args:
        name: The session name. If not given, uses the current window's
              session.
        force: Close without saving.
    """
    if name is None:
        main_window = objreg.get('main-window', scope='window',
                                 window=win_id)
        name = main_window.session_name

    if name.startswith('_'):
        raise cmdutils.CommandError(
            "{} is an internal session.".format(name))

    # Save before closing
    if not force:
        try:
            session_manager.save(name, session_name=name)
        except SessionError as e:
            raise cmdutils.CommandError(
                "Error saving session before close: {}".format(e))

    # Collect windows to close
    windows_to_close = []
    for wid in list(objreg.window_registry):
        try:
            win = objreg.get('main-window', scope='window', window=wid)
        except KeyError:
            continue
        if sip.isdeleted(win):
            continue
        if getattr(win, 'session_name', 'default') == name:
            windows_to_close.append(win)

    if not windows_to_close:
        raise cmdutils.CommandError(
            "No windows found in session '{}'.".format(name))

    for win in windows_to_close:
        win.close()

    message.info("Closed session '{}' ({} window{}).".format(
        name, len(windows_to_close),
        's' if len(windows_to_close) != 1 else ''))
@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
@cmdutils.argument('with_private', flag='p')
@cmdutils.argument('no_history', flag='n')
def session_save(name: ArgType = default, *,
                 current: bool = False,
                 quiet: bool = False,
                 force: bool = False,
                 only_active_window: bool = False,
                 with_private: bool = False,
                 no_history: bool = False,
                 win_id: int = None) -> None:
    """Save a session.

    Args:
        name: The name of the session. If not given, the session configured in
              session.default_name is saved.
        current: Save the current window's session.
        quiet: Don't show confirmation message.
        force: Force saving internal sessions (starting with an underline).
        only_active_window: Saves only tabs of the currently active window.
        with_private: Include private windows.
        no_history: Don't store tab history.
    """
    if not isinstance(name, Sentinel) and name.startswith('_') and not force:
        raise cmdutils.CommandError("{} is an internal session, use --force "
                                    "to save anyways.".format(name))
    if current:
        main_window = objreg.get('main-window', scope='window',
                                  window=win_id)
        current_session = getattr(main_window, 'session_name', None)
        if current_session is None:
            raise cmdutils.CommandError("Current window has no session!")
        name = current_session
        assert not name.startswith('_')

    resolved_name = session_manager._get_session_name(name, win_id=win_id)

    try:
        if only_active_window:
            name = session_manager.save(resolved_name, only_window=win_id,
                                        with_private=True,
                                        with_history=not no_history)
        else:
            name = session_manager.save(resolved_name,
                                        with_private=with_private,
                                        with_history=not no_history,
                                        session_name=resolved_name)
    except SessionError as e:
        raise cmdutils.CommandError("Error while saving session: {}".format(e))
    if quiet:
        log.sessions.debug("Saved session {}.".format(name))
    else:
        message.info("Saved session {}.".format(name))


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.session)
def session_delete(name: str, *, force: bool = False) -> None:
    """Delete a session.

    Args:
        name: The name of the session.
        force: Force deleting internal sessions (starting with an underline).
    """
    if name.startswith('_') and not force:
        raise cmdutils.CommandError("{} is an internal session, use --force "
                                    "to delete anyways.".format(name))
    try:
        session_manager.delete(name)
    except SessionNotFoundError:
        raise cmdutils.CommandError("Session {} not found!".format(name))
    except SessionError as e:
        log.sessions.exception("Error while deleting session!")
        raise cmdutils.CommandError("Error while deleting session: {}"
                                    .format(e))
    log.sessions.debug("Deleted session {}.".format(name))


def load_default(name):
    """Load the default session.

    With per-window sessions, this loads the _autosave session (untagged
    windows) plus each saved named session.

    Args:
        name: The name of the session to load, or None to read state file.
    """
    if name is None and session_manager.exists('_autosave'):
        # Load untagged windows from _autosave
        try:
            session_manager.load('_autosave', temp=True)
        except (SessionNotFoundError, SessionError) as e:
            message.error("Failed to load autosave: {}".format(e))

        # Load each saved named session
        try:
            sessions_str = configfiles.state['general']['sessions']
            session_names = [s.strip() for s in sessions_str.split(',')
                            if s.strip()]
        except KeyError:
            session_names = []

        for sname in session_names:
            try:
                session_manager.load(sname)
            except (SessionNotFoundError, SessionError) as e:
                message.error(
                    "Failed to load session {}: {}".format(sname, e))

        # Clean up state
        try:
            del configfiles.state['general']['sessions']
        except KeyError:
            pass
        return

    elif name is None:
        try:
            name = configfiles.state['general']['session']
        except KeyError:
            # No session given as argument and none in the session file ->
            # start without loading a session
            return

    try:
        session_manager.load(name)
    except SessionNotFoundError:
        message.error("Session {} not found!".format(name))
    except SessionError as e:
        message.error("Failed to load session {}: {}".format(name, e))
    try:
        del configfiles.state['general']['session']
    except KeyError:
        pass
    # If this was a _restart session, delete it.
    if name == '_restart':
        session_manager.delete('_restart')