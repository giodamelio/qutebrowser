# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Debug commands for the end-to-end test harness.

app.py imports this module only with --debug, so these commands don't exist
in a normal run.
"""

from typing import Any

from qutebrowser.api import cmdutils
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import sessionfile, sessioncommands
from qutebrowser.qt import sip
from qutebrowser.utils import message, objreg, utils


@cmdutils.register(debug=True)
def debug_dump_windows(path: str) -> None:
    """Write every open window, including private ones, to a YAML file.

    The file has upstream's single-file session shape, plus the session each
    window belongs to, its id and its title.

    Args:
        path: The file to write.
    """
    data: dict[str, Any] = {'windows': []}
    for win_id in sorted(objreg.window_registry):
        window = objreg.window_registry[win_id]
        if sip.isdeleted(window):
            continue
        win_data = sessionfile.serialize_window(window)
        win_data['session'] = window.session.name
        win_data['win_id'] = win_id
        win_data['title'] = window.windowTitle()
        if window.session.private:
            win_data['private'] = True
        data['windows'].append(win_data)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            utils.yaml_dump(data, f)
    except OSError as e:
        raise cmdutils.CommandError(f"Failed to write {path}: {e}")
    message.info(f"Dumped windows to {path}.")


@cmdutils.register(debug=True)
def debug_flush_sessions() -> None:
    """Save every open session now instead of waiting for autosave."""
    manager = windowsessions.manager
    for session in manager.sessions():
        if session.is_open and not session.private:
            try:
                manager.save(session)
            except sessionfile.SessionFileError as e:
                raise cmdutils.CommandError(str(e))
    message.info("Flushed sessions.")


@cmdutils.register(debug=True)
def debug_close_other_sessions() -> None:
    """Close every window outside the default session.

    A default window is opened first if the default session has none.
    """
    manager = windowsessions.manager
    if not manager.default.is_open:
        sessioncommands.open_session(manager.default)
    for win_id in sorted(objreg.window_registry):
        window = objreg.window_registry[win_id]
        if not sip.isdeleted(window) and window.session is not manager.default:
            window.close()
