# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

pytest.importorskip('qutebrowser.qt.webenginecore')

from qutebrowser.qt import sip
from qutebrowser.qt.core import QObject

from qutebrowser.browser.webengine import profiles
from qutebrowser.utils import qtutils


class FakeProfile:

    def __init__(self, key, private):
        self.key = key
        self.private = private
        self.deleted = False

    def deleteLater(self):
        self.deleted = True


@pytest.fixture
def teardowns():
    return []


@pytest.fixture
def registry(monkeypatch, teardowns):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: False)

    def initializer(profile):
        return lambda: teardowns.append(profile.key)

    return profiles.ProfileRegistry(factory=FakeProfile, initializer=initializer)


def test_acquire_creates_once(registry):
    first = registry.acquire('default', private=False)
    second = registry.acquire('default', private=False)
    assert first is second
    assert not first.private


def test_acquire_private(registry):
    profile = registry.acquire('private-1', private=True)
    assert profile.private
    assert registry.get('private-1') is profile


def test_acquire_private_single_process(registry, monkeypatch):
    monkeypatch.setattr(qtutils, 'is_single_process', lambda: True)
    with pytest.raises(profiles.PrivateUnavailableError,
                       match='single-process process model'):
        registry.acquire('private-1', private=True)
    assert registry.get('private-1') is None


def test_acquire_privacy_mismatch(registry):
    registry.acquire('default', private=False)
    with pytest.raises(ValueError):
        registry.acquire('default', private=True)


def test_release_keeps_referenced_profile(registry, teardowns):
    profile = registry.acquire('default', private=False)
    registry.acquire('default', private=False)
    registry.release('default')
    assert registry.get('default') is profile
    assert not profile.deleted
    assert teardowns == []


def test_release_last_reference_deletes(registry, teardowns):
    profile = registry.acquire('private-1', private=True)
    registry.release('private-1')
    assert registry.get('private-1') is None
    assert profile.deleted
    assert teardowns == ['private-1']


def test_release_waits_for_pages(registry, teardowns):
    profile = registry.acquire('private-1', private=True)
    page = QObject()
    registry.track_page('private-1', page)

    registry.release('private-1')
    assert registry.get('private-1') is None
    assert teardowns == ['private-1']
    assert not profile.deleted

    sip.delete(page)
    assert profile.deleted


def test_page_destroyed_before_release(registry):
    profile = registry.acquire('private-1', private=True)
    page = QObject()
    registry.track_page('private-1', page)
    sip.delete(page)

    registry.release('private-1')
    assert profile.deleted


def test_iter_live_profiles(registry):
    default = registry.acquire('default', private=False)
    private = registry.acquire('private-1', private=True)
    assert list(registry) == [default, private]
    registry.release('private-1')
    assert list(registry) == [default]


def test_get_registry_uninitialized(monkeypatch):
    monkeypatch.setattr(profiles, 'registry', None)
    with pytest.raises(RuntimeError):
        profiles.get_registry()


def test_default_profile(monkeypatch, registry):
    monkeypatch.setattr(profiles, 'registry', None)
    assert profiles.default_profile() is None

    monkeypatch.setattr(profiles, 'registry', registry)
    assert profiles.default_profile() is None
    default = registry.acquire(profiles.DEFAULT_KEY, private=False)
    assert profiles.default_profile() is default
