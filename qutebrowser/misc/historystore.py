# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tab history files, one per tab, in a session's history/ directory.

A file holds the bytes QtWebEngine's own history serialization produced for
one tab, compressed, behind a header naming this file format's version and
the QtWebEngine version that wrote them. Chromium's format changes between
releases, so bytes from another version are never loaded.

The layout is big-endian: the magic b'QUTEHIST', the format version
(uint16), the length of the QtWebEngine version (uint16), that version in
ASCII, then the zlib-compressed bytes.
"""

import functools
import hashlib
import os
import pathlib
import re
import struct
import uuid
import zlib
from collections.abc import Mapping, MutableMapping

from qutebrowser.utils import log


MAGIC = b'QUTEHIST'
FORMAT_VERSION = 1
SUFFIX = '.bin'
_TMP_SUFFIX = '.tmp'
_HEADER = struct.Struct('>8sHH')
_ID_RE = re.compile(r'[0-9a-f]{32}')


class Error(Exception):

    """A history file could not be written."""


class UnusableHistoryError(Exception):

    """A tab's history file is missing, unreadable, or from another QtWebEngine.

    Attributes:
        reason: Why, worded for the warning about tabs restored without back
                history.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Snapshot(bytes):

    """History bytes that never change, such as a closed tab's."""

    @functools.cached_property
    def digest(self) -> str:
        """Hashed once, as every save of the session needs it."""
        return digest(self)


def new_id() -> str:
    """Get a new tab id."""
    return uuid.uuid4().hex


def is_valid_id(value: object) -> bool:
    """Check a tab id read from a session file.

    Ids name files, so anything else must never reach a path.
    """
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


@functools.cache
def running_version() -> str:
    """Get the version of the QtWebEngine library this process uses."""
    # Not version.qtwebengine_versions(): QUTE_QTWEBENGINE_VERSION_OVERRIDE
    # changes what that reports, but not the format of the bytes.
    from qutebrowser.qt.webenginecore import qWebEngineVersion
    version = qWebEngineVersion()
    assert version is not None
    return version


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(data: bytes, version: str) -> bytes:
    """Encode history bytes into the on-disk format for one version."""
    raw_version = version.encode('ascii')
    header = _HEADER.pack(MAGIC, FORMAT_VERSION, len(raw_version))
    return header + raw_version + zlib.compress(data)


def decode(raw: bytes) -> tuple[bytes, str]:
    """Get the history bytes and the QtWebEngine version from a file."""
    if len(raw) < _HEADER.size:
        raise UnusableHistoryError("history file unreadable")
    magic, file_format, version_length = _HEADER.unpack_from(raw)
    if magic != MAGIC or file_format != FORMAT_VERSION:
        raise UnusableHistoryError("history file unreadable")
    start = _HEADER.size + version_length
    try:
        version = raw[_HEADER.size:start].decode('ascii')
        data = zlib.decompress(raw[start:])
    except (UnicodeDecodeError, zlib.error):
        raise UnusableHistoryError("history file unreadable")
    return data, version


def _path(directory: pathlib.Path, tab_id: str) -> pathlib.Path:
    assert is_valid_id(tab_id), tab_id
    return directory / f'{tab_id}{SUFFIX}'


def read(directory: pathlib.Path, tab_id: str) -> bytes:
    """Read a tab's history bytes, if the running QtWebEngine wrote them."""
    path = _path(directory, tab_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise UnusableHistoryError("history file missing")
    except OSError as e:
        log.sessions.debug(f"Failed to read {path}: {e}")
        raise UnusableHistoryError("history file unreadable")
    data, version = decode(raw)
    running = running_version()
    if version != running:
        raise UnusableHistoryError(
            f"saved by QtWebEngine {version}, running {running}")
    return data


def _write(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    tmp.unlink(missing_ok=True)
    # Chromium's history includes form contents, even text typed and never
    # submitted.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(fd, 'wb') as f:
        f.write(encode(data, running_version()))
        f.flush()
        # session.yml, written after this, must never refer to a file whose
        # contents a power loss could still take back.
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_changed(directory: pathlib.Path, history: Mapping[str, bytes],
                  digests: MutableMapping[str, str]) -> None:
    """Write each tab's file unless it already holds exactly these bytes.

    Args:
        directory: The session's history directory, which must exist.
        history: History bytes by tab id. A Snapshot is hashed only once.
        digests: Digests of the files on disk by tab id, updated as files
                 are written.
    """
    for tab_id, data in history.items():
        path = _path(directory, tab_id)
        new_digest = (data.digest if isinstance(data, Snapshot)
                      else digest(data))
        # A file deleted behind our back is written again.
        if digests.get(tab_id) == new_digest and path.exists():
            continue
        try:
            _write(path, data)
        except OSError as e:
            raise Error(f"{path}: {e}")
        digests[tab_id] = new_digest


def remove_unreferenced(directory: pathlib.Path, referenced: set[str],
                        digests: MutableMapping[str, str]) -> None:
    """Delete the files of tabs nothing refers to, and leftover temp files."""
    try:
        paths = list(directory.iterdir())
    except FileNotFoundError:
        return
    except OSError as e:
        # Best-effort, like the per-file unlink below: harmless until the
        # next save, which tries again.
        log.sessions.warning(f"Failed to list {directory}: {e}")
        return
    for path in paths:
        tab_id = None
        if path.name.endswith(SUFFIX + _TMP_SUFFIX):
            pass
        elif (path.suffix == SUFFIX and is_valid_id(path.stem) and
                path.stem not in referenced):
            tab_id = path.stem
        else:
            continue
        try:
            path.unlink()
        except OSError as e:
            # Harmless until the next save, which tries again.
            log.sessions.warning(f"Failed to remove {path}: {e}")
            continue
        if tab_id is not None:
            digests.pop(tab_id, None)
