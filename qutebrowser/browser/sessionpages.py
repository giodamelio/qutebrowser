# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The read-only qute://containers and qute://sessions pages."""

import dataclasses

from qutebrowser.qt.core import QUrl

from qutebrowser.browser import qutescheme
from qutebrowser.browser.webengine import profiles
from qutebrowser.config import configtypes
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import containers
from qutebrowser.utils import jinja


_SOURCES = {'builtin': 'built-in', 'runtime': 'runtime', 'declared': 'declared'}


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
