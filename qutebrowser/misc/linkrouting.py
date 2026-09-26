# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Choosing the window for links that arrive from outside qutebrowser.

With sessions on more than one profile open, a link from another
application could land in the wrong container, so a picker in the
last-focused window asks which window it goes to.
"""

import functools
import html
from typing import Any

from qutebrowser.qt.core import QUrl

from qutebrowser.browser.webengine import notification
from qutebrowser.mainwindow import mainwindow, windowsessions
from qutebrowser.misc import quitter
from qutebrowser.utils import log, message, objreg, usertypes


# Pickers not answered, cancelled or aborted yet, with their links.
_pending: dict[usertypes.Question, list[QUrl]] = {}
# Links dropped once quitting started, reported together as it ends.
_dropped_on_quit: list[QUrl] = []
_hooked_quitter: Any = None


def distinct_profile_keys() -> set[str]:
    """Get the profile keys of open sessions; each private one is its own."""
    return {session.profile_key for session in windowsessions.manager.sessions()
            if session.is_open}


def needs_picker(target: str) -> bool:
    """Check whether links opened with the given target need the picker.

    A private-window target creates a new private session, so there is
    nothing to choose.
    """
    return target != 'private-window' and len(distinct_profile_keys()) > 1


def window_options() -> list[tuple[str, str, str]]:
    """Get the picker's rows: every open window, grouped by session."""
    rows = []
    for session in windowsessions.manager.sessions():
        profile = 'private' if session.private else session.container
        for win_id in sorted(session.windows):
            tab = objreg.window_registry[win_id].tabbed_browser.widget.currentWidget()
            title = '' if tab is None else tab.title()
            rows.append((str(win_id), f'{session.name} / {profile}', title))
    return rows


def ask_and_open(urls: list[QUrl], *, target: str) -> None:
    """Ask in the last-focused window where links go, then open them there."""
    window = objreg.last_focused_window()
    listing = '<br/>'.join(html.escape(url.toDisplayString()) for url in urls)
    question = message.ask_async(
        'Open in which window?', usertypes.PromptMode.select,
        functools.partial(_open_in_chosen, urls, target),
        text=listing, win_id=window.win_id, options=window_options())
    _pending[question] = urls
    _hook_quit()

    def discard_once() -> None:
        # cancelled and aborted can both reach this handler for the same
        # question, e.g. cancelling one whose window is also closing, and
        # quitting may already have taken the links.
        if _pending.pop(question, None) is None:
            return
        if windowsessions.manager.shutting_down:
            _dropped_on_quit.extend(urls)
            return
        if question.is_aborted:
            message.error(f"Window {question.win_id} closed before it "
                          "could ask where to open the links")
        discard(urls)

    question.cancelled.connect(discard_once)
    question.aborted.connect(discard_once)
    question.completed.connect(lambda: _pending.pop(question, None))


def _hook_quit() -> None:
    global _hooked_quitter
    if _hooked_quitter is quitter.instance:
        return
    # Quitting never aborts a non-blocking question before the application
    # exits, so an open or queued picker would lose its links silently.
    quitter.instance.shutting_down.connect(_discard_on_quit)
    _hooked_quitter = quitter.instance


def _discard_on_quit() -> None:
    """Report every link no picker could open, in one notification."""
    for urls in _pending.values():
        _dropped_on_quit.extend(urls)
    _pending.clear()
    if not _dropped_on_quit:
        return
    shown = [url.toDisplayString() for url in _dropped_on_quit]
    _dropped_on_quit.clear()
    # No window is left to show a message in, and with the qt or messages
    # presenter no notification goes out, so the log is the only trace.
    log.misc.info(f"Links not opened: {', '.join(shown)}")
    notification.notify('Links not opened', '\n'.join(shown))


def _open_in_chosen(urls: list[QUrl], target: str, key: str) -> None:
    if windowsessions.manager.shutting_down:
        # The sessions are saved already, so tabs opened now would be lost.
        _dropped_on_quit.extend(urls)
        return
    window = objreg.window_registry.get(int(key))
    if window is None or window.tabbed_browser.is_shutting_down:
        message.error(f"Window {key} closed before the links could open")
        discard(urls)
        return
    open_in(window, urls, target=target)


def open_in(window: Any, urls: list[QUrl], *, target: str) -> None:
    """Open links relative to a chosen window, as new_instance_open_target says."""
    assert target != 'private-window', target
    if target == 'window':
        # One window per link, as without the picker.
        for url in urls:
            new_window = mainwindow.MainWindow(session=window.session)
            new_window.tabbed_browser.tabopen(url, background=False,
                                              related=False)
            new_window.should_raise = True
            new_window.show()
            new_window.maybe_raise()
        return

    background = target in {'tab-bg', 'tab-bg-silent'}
    for url in urls:
        window.tabbed_browser.tabopen(url, background=background, related=False)
    window.should_raise = target not in {'tab-silent', 'tab-bg-silent'}
    window.show()
    window.maybe_raise()


def discard(urls: list[QUrl]) -> None:
    """Drop links whose picker was cancelled, saying which ones they were."""
    shown = [url.toDisplayString() for url in urls]
    message.info(f"Links not opened: {', '.join(shown)}")
    notification.notify('Links not opened', '\n'.join(shown))
