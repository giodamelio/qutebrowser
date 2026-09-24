# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live QWebEngineProfiles, keyed by profile key.

A profile key is a container name for normal sessions, and the session name
for private sessions.
"""

import dataclasses
import functools
from collections.abc import Callable, Iterator

from qutebrowser.qt.core import QObject
from qutebrowser.qt.webenginecore import QWebEngineProfile

from qutebrowser.utils import log, qtutils


DEFAULT_KEY = 'default'

Teardown = Callable[[], None]
Initializer = Callable[[QWebEngineProfile], Teardown]
Factory = Callable[[str, bool], QWebEngineProfile]


class PrivateUnavailableError(Exception):

    """A private profile was requested in single-process mode."""

    def __init__(self) -> None:
        super().__init__("Private windows are unavailable with the "
                         "single-process process model.")


@dataclasses.dataclass(eq=False)
class _Entry:

    key: str
    profile: QWebEngineProfile
    private: bool
    teardown: Teardown
    refcount: int = 0
    pages: set[int] = dataclasses.field(default_factory=set)


class ProfileRegistry:

    """Creates, shares, and deletes QWebEngineProfiles by key."""

    def __init__(self, *, factory: Factory, initializer: Initializer) -> None:
        self._factory = factory
        self._initializer = initializer
        self._live: dict[str, _Entry] = {}
        self._dying: list[_Entry] = []

    def acquire(self, key: str, *, private: bool) -> QWebEngineProfile:
        """Get the profile for key, creating it on first use."""
        entry = self._live.get(key)
        if entry is None:
            if private and qtutils.is_single_process():
                raise PrivateUnavailableError()
            log.misc.debug(f"Creating profile {key!r} (private: {private})")
            profile = self._factory(key, private)
            entry = _Entry(key=key, profile=profile, private=private,
                           teardown=self._initializer(profile))
            self._live[key] = entry
        elif entry.private != private:
            raise ValueError(f"Profile {key!r} exists with private={entry.private}, "
                             f"requested private={private}")
        entry.refcount += 1
        return entry.profile

    def release(self, key: str) -> None:
        """Drop one reference to key's profile."""
        entry = self._live[key]
        entry.refcount -= 1
        if entry.refcount > 0:
            return

        del self._live[key]
        entry.teardown()
        if entry.pages:
            # Deleting a profile before its pages makes Qt warn and can keep
            # the profile alive.
            log.misc.debug(f"Profile {key!r} released, waiting for "
                           f"{len(entry.pages)} pages")
            self._dying.append(entry)
        else:
            self._delete(entry)

    def get(self, key: str) -> QWebEngineProfile | None:
        entry = self._live.get(key)
        return None if entry is None else entry.profile

    def __iter__(self) -> Iterator[QWebEngineProfile]:
        return iter([entry.profile for entry in self._live.values()])

    def track_page(self, key: str, page: QObject) -> None:
        """Delay deleting key's profile until page is destroyed."""
        entry = self._live[key]
        page_id = id(page)
        entry.pages.add(page_id)
        page.destroyed.connect(
            functools.partial(self._on_page_destroyed, entry, page_id))

    def _on_page_destroyed(self, entry: _Entry, page_id: int,
                           _obj: QObject | None = None) -> None:
        entry.pages.discard(page_id)
        if not entry.pages and any(dying is entry for dying in self._dying):
            self._dying = [dying for dying in self._dying if dying is not entry]
            self._delete(entry)

    def _delete(self, entry: _Entry) -> None:
        log.misc.debug(f"Deleting profile {entry.key!r}")
        entry.profile.deleteLater()


registry: ProfileRegistry | None = None


def get_registry() -> ProfileRegistry:
    """Get the registry, failing loudly before webenginesettings.init()."""
    if registry is None:
        raise RuntimeError("Profile registry used before webenginesettings.init()")
    return registry


def default_profile() -> QWebEngineProfile | None:
    """Get the default profile, or None before profiles are initialized."""
    if registry is None:
        return None
    return registry.get(DEFAULT_KEY)
