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
from qutebrowser.qt.webenginecore import QWebEngineProfile, QWebEngineDownloadRequest

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
    holds: set[int] = dataclasses.field(default_factory=set)


class ProfileRegistry:

    """Creates, shares, and deletes QWebEngineProfiles by key."""

    def __init__(self, *, factory: Factory, initializer: Initializer) -> None:
        self._factory = factory
        self._initializer = initializer
        self._live: dict[str, _Entry] = {}
        self._dying: list[_Entry] = []

    def acquire(self, key: str, *, private: bool) -> QWebEngineProfile:
        """Get the profile for key, creating it on first use."""
        entry = self._live.get(key) or self._revive(key)
        if entry is None:
            if private and qtutils.is_single_process():
                raise PrivateUnavailableError()
            log.misc.debug(f"Creating profile {key!r} (private: {private})")
            profile = self._factory(key, private)
            entry = _Entry(key=key, profile=profile, private=private,
                           teardown=lambda: None)
            # The initializer applies global settings by iterating over the
            # registry, so the new profile must already be listed.
            self._live[key] = entry
            try:
                entry.teardown = self._initializer(profile)
            except BaseException:
                del self._live[key]
                raise
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
        if entry.holds:
            # Deleting a profile before its pages makes Qt warn and can keep
            # the profile alive, and it cancels the profile's downloads.
            log.misc.debug(f"Profile {key!r} released, waiting for "
                           f"{len(entry.holds)} pages and downloads")
            self._dying.append(entry)
        else:
            self._delete(entry)

    def _revive(self, key: str) -> _Entry | None:
        """Reuse a released profile that is still waiting for its pages.

        Creating a second profile on the same storage path instead would make
        both use the same files at once.
        """
        for entry in self._dying:
            if entry.key == key:
                self._dying = [dying for dying in self._dying
                               if dying is not entry]
                self._live[key] = entry
                log.misc.debug(f"Reviving profile {key!r}")
                return entry
        return None

    def is_loaded(self, key: str) -> bool:
        """Whether key has a profile, in use or waiting for its pages."""
        return key in self._live or any(entry.key == key
                                        for entry in self._dying)

    def get(self, key: str) -> QWebEngineProfile | None:
        entry = self._live.get(key)
        return None if entry is None else entry.profile

    def __iter__(self) -> Iterator[QWebEngineProfile]:
        return iter([entry.profile for entry in self._live.values()])

    def track_page(self, key: str, page: QObject) -> None:
        """Delay deleting key's profile until page is destroyed."""
        self._hold(self._live[key], page)

    def track_download(self, profile: QWebEngineProfile,
                       download: QWebEngineDownloadRequest) -> None:
        """Delay deleting profile until download is finished or destroyed."""
        entry = self._entry_for(profile)
        download_id = self._hold(entry, download)

        def on_finished_changed() -> None:
            if download.isFinished():
                self._drop(entry, download_id)

        download.isFinishedChanged.connect(on_finished_changed)

    def _entry_for(self, profile: QWebEngineProfile) -> _Entry:
        for entry in self._live.values():
            if entry.profile is profile:
                return entry
        raise KeyError(f"Profile {profile!r} is not in the registry")

    def _hold(self, entry: _Entry, obj: QObject) -> int:
        obj_id = id(obj)
        entry.holds.add(obj_id)
        obj.destroyed.connect(functools.partial(self._drop, entry, obj_id))
        return obj_id

    def _drop(self, entry: _Entry, obj_id: int,
              _obj: QObject | None = None) -> None:
        entry.holds.discard(obj_id)
        if not entry.holds and any(dying is entry for dying in self._dying):
            self._dying = [dying for dying in self._dying if dying is not entry]
            self._delete(entry)

    def _delete(self, entry: _Entry) -> None:
        log.misc.debug(f"Deleting profile {entry.key!r}")
        entry.teardown()
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
