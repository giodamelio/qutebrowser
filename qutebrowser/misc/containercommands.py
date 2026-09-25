# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Commands to create, delete and rename runtime containers."""

from qutebrowser.api import cmdutils
from qutebrowser.browser import sessionpages
from qutebrowser.browser.webengine import profiles
from qutebrowser.completion.models import miscmodels
from qutebrowser.mainwindow import windowsessions
from qutebrowser.misc import containers


def _runtime_container(name: str, action: str) -> None:
    """Check that name is a runtime container, the only kind commands change."""
    if name == containers.DEFAULT:
        raise cmdutils.CommandError(f"Container {name} can't be {action}d")
    try:
        container = containers.registry.get(name)
    except containers.UnknownContainerError as e:
        raise cmdutils.CommandError(str(e))
    if container.source == 'declared':
        raise cmdutils.CommandError(
            f"Container {name} is declared in config.py, {action} it there")


def _describe_failures(failed: list[tuple[str, str]]) -> str:
    return ', '.join(f'{name} ({error})' for name, error in failed)


def _check_unloaded(name: str) -> None:
    if profiles.get_registry().is_loaded(name):
        raise cmdutils.CommandError(
            f"Container {name} is still loaded; close its sessions and wait "
            "for its downloads to finish")


@cmdutils.register()
def container_new(name: str, *, color: str = containers.FALLBACK_COLOR) -> None:
    """Create a runtime container.

    Args:
        name: The name of the new container.
        color: The container's color.
    """
    try:
        windowsessions.validate_new_name(name)
        containers.registry.add(name, color)
    except (windowsessions.Error, containers.Error) as e:
        raise cmdutils.CommandError(str(e))


@cmdutils.register()
@cmdutils.argument('name', completion=miscmodels.runtime_container)
def container_delete(name: str) -> None:
    """Delete a runtime container and its data.

    Args:
        name: The name of the container.
    """
    _runtime_container(name, 'delete')
    using = [s.name for s in windowsessions.manager.sessions_using(name)]
    if using:
        listing = ', '.join(using[:3])
        if len(using) > 3:
            listing += f' and {len(using) - 3} more'
        raise cmdutils.CommandError(
            f"Container {name} is used by sessions: {listing}")
    _check_unloaded(name)
    try:
        containers.registry.remove(name)
        containers.registry.delete_storage(name)
    except containers.Error as e:
        raise cmdutils.CommandError(str(e))


@cmdutils.register()
@cmdutils.argument('old', completion=miscmodels.runtime_container)
def container_rename(old: str, new: str) -> None:
    """Rename a runtime container, its data, and the sessions using it.

    Args:
        old: The current name of the container.
        new: The new name.
    """
    _runtime_container(old, 'rename')
    try:
        windowsessions.validate_new_name(new)
    except windowsessions.Error as e:
        raise cmdutils.CommandError(str(e))
    if new in containers.registry:
        raise cmdutils.CommandError(f"Container {new} already exists!")
    _check_unloaded(old)
    try:
        containers.registry.check_writable()
        containers.registry.move_storage(old, new)
    except containers.Error as e:
        raise cmdutils.CommandError(str(e))
    failed = windowsessions.manager.rename_container(old, new)
    try:
        # Sessions that kept old get it back as a runtime container through
        # adoption, which the merged signal triggers.
        containers.registry.rename(old, new)
    except containers.Error as e:
        # The sessions and the directories already moved to new, but the
        # registry itself didn't, so put everything back on old rather than
        # leave sessions pointing at a container that doesn't exist.
        stuck = windowsessions.manager.rename_container(old=new, new=old)
        if stuck:
            # Moving the directories back would leave these sessions without
            # their data, so it stays with them and new is kept defined.
            windowsessions.manager.adopt_containers()
            raise cmdutils.CommandError(
                f"{e} (and rolling back to {old} failed for these sessions, "
                f"which stay on container {new} with its data, while "
                f"container {old} is left empty: {_describe_failures(stuck)})")
        try:
            containers.registry.move_storage(old=new, new=old)
        except containers.Error as rollback_e:
            raise cmdutils.CommandError(
                f"{e} (and rolling back to {old} also failed: {rollback_e})")
        raise cmdutils.CommandError(str(e))
    if failed:
        raise cmdutils.CommandError(
            f"Renamed container {old} to {new}, but these sessions still use "
            f"{old}, and their data now lives under container {new}: "
            f"{_describe_failures(failed)}")


@cmdutils.register()
@cmdutils.argument('win_id', value=cmdutils.Value.win_id)
def container_list(tab: bool = False, bg: bool = False, window: bool = False,
                   *, win_id: int | None = None) -> None:
    """Show every container on qute://containers.

    Args:
        tab: Open in a new tab.
        bg: Open in a background tab.
        window: Open in a new window.
    """
    assert win_id is not None
    sessionpages.open_page('qute://containers/', win_id, tab=tab, bg=bg,
                           window=window)
