# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Container definitions: declared in the config, or created at runtime.

A container names a persistent QWebEngineProfile with its own storage.
Declared containers come from the `containers` setting. Runtime ones are
created with :container-new and kept in data/containers.yml, which only the
browser writes.
"""

import dataclasses
import pathlib
import shutil
from typing import Any, cast

import yaml
from qutebrowser.qt.core import QObject, pyqtSignal

from qutebrowser.config import config, configdata, configexc, configtypes
from qutebrowser.utils import message, qtutils, standarddir, utils


DEFAULT = 'default'
DEFAULT_COLOR = '#3b4252'
FALLBACK_COLOR = '#808080'


class Error(Exception):

    """A container operation failed in a way to show to the user."""


class UnknownContainerError(Error):

    """No container has the given name."""


@dataclasses.dataclass(frozen=True)
class Container:

    """One container of the merged view."""

    name: str
    color: str
    source: str


class ContainerRegistry(QObject):

    """Merges declared and runtime containers, and owns the runtime file.

    Signals:
        merged: The merged view was recomputed.
    """

    merged = pyqtSignal()

    def __init__(self, data_dir: pathlib.Path, cache_dir: pathlib.Path,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._data_dir = data_dir
        self._cache_dir = cache_dir
        self._path = data_dir / 'containers.yml'
        self._runtime: dict[str, dict[str, str]] = {}
        self._unwritable_reason: str | None = None
        self._merged: dict[str, Container] = {}

    def load(self) -> None:
        """Read the runtime file, moving a broken one aside, then merge."""
        try:
            with self._path.open(encoding='utf-8') as f:
                raw = utils.yaml_load(f)
            self._runtime = self._validate(raw)
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError, yaml.YAMLError,
                configexc.ValidationError) as e:
            self._quarantine(e)
        self._merge()

    def _validate(self, raw: Any) -> dict[str, dict[str, str]]:
        if raw is None:
            return {}
        # The setting's type already knows the naming and color rules.
        value = configdata.DATA['containers'].typ.to_py(raw)
        return {name: dict(definition) for name, definition in value.items()}

    def _quarantine(self, error: Exception) -> None:
        """Keep a runtime file that can't be read from being overwritten."""
        broken = self._path.with_name(f'{self._path.name}.broken')
        reason = f"Failed to read {self._path}: {error}"
        if broken.exists():
            self._unwritable_reason = (
                f"{reason}. It could not be moved aside because {broken} "
                "already exists")
            message.error(f"{self._unwritable_reason}.")
            return
        try:
            self._path.rename(broken)
        except OSError as e:
            self._unwritable_reason = (
                f"{reason}. Moving it to {broken} failed: {e}")
            message.error(f"{self._unwritable_reason}.")
            return
        message.error(f"{reason}. Moved it to {broken}.")

    def on_config_changed(self, option: str) -> None:
        if option == 'containers':
            self._merge()

    def _merge(self) -> None:
        merged = {DEFAULT: Container(DEFAULT, DEFAULT_COLOR, 'builtin')}
        for name, definition in self._runtime.items():
            merged[name] = Container(name, definition['color'], 'runtime')
        for name, definition in config.val.containers.items():
            merged[name] = Container(name, definition['color'], 'declared')
        self._merged = merged
        self.merged.emit()

    def __contains__(self, name: str) -> bool:
        return name in self._merged

    def get(self, name: str) -> Container:
        """Get one container by name."""
        try:
            return self._merged[name]
        except KeyError:
            raise UnknownContainerError(f"Container {name} not found!")

    def containers(self) -> list[Container]:
        """Get every container, sorted by name."""
        return [self._merged[name] for name in sorted(self._merged)]

    def storage_paths(self, name: str) -> tuple[pathlib.Path, pathlib.Path]:
        """Get a container's persistent storage and cache directories."""
        assert name in self._merged, name
        return self._paths_for(name)

    def _paths_for(self, name: str) -> tuple[pathlib.Path, pathlib.Path]:
        if name == DEFAULT:
            return self._data_dir / 'webengine', self._cache_dir / 'webengine'
        # A caller-supplied name that isn't a valid container name (e.g. '',
        # '..' or something with a '/') must not turn into a path outside
        # data/containers or cache/containers.
        configdata.DATA['containers'].typ.keytype.to_py(name)
        return (self._data_dir / 'containers' / name,
                self._cache_dir / 'containers' / name)

    def check_writable(self) -> None:
        if self._unwritable_reason is not None:
            raise Error(
                f"Can't change runtime containers: {self._unwritable_reason}")

    def add(self, name: str, color: str) -> None:
        """Add a runtime container whose name is already validated."""
        if name in self._merged:
            raise Error(f"Container {name} already exists!")
        try:
            configtypes.QtColor().to_py(color)
        except configexc.ValidationError as e:
            raise Error(f"Invalid color {color!r}: {e}")
        self._write({**self._runtime, name: {'color': color}})
        self._merge()

    def remove(self, name: str) -> None:
        """Remove a runtime container."""
        assert name in self._runtime, name
        self._write({key: value for key, value in self._runtime.items()
                     if key != name})
        self._merge()

    def rename(self, old: str, new: str) -> None:
        """Rename a runtime container."""
        assert old in self._runtime and new not in self._merged, (old, new)
        self._write({(new if key == old else key): value
                     for key, value in self._runtime.items()})
        self._merge()

    def adopt(self, missing: dict[str, list[str]]) -> None:
        """Keep containers defined that sessions use but nothing defines."""
        assert not any(name in self._merged for name in missing), missing
        self._runtime = {**self._runtime,
                         **{name: {'color': FALLBACK_COLOR} for name in missing}}
        try:
            self._write(self._runtime)
        except Error as e:
            message.error(str(e))
        for name, sessions in sorted(missing.items()):
            message.info(
                f"Keeping container {name} as a runtime container, because "
                f"sessions use it: {', '.join(sorted(sessions))}")
        self._merge()

    def _write(self, runtime: dict[str, dict[str, str]]) -> None:
        self.check_writable()
        try:
            with qtutils.savefile_open(str(self._path)) as f:
                utils.yaml_dump(runtime, f)
        except (OSError, yaml.YAMLError) as e:
            raise Error(f"Failed to write {self._path}: {e}")
        self._runtime = runtime

    def delete_storage(self, name: str) -> None:
        """Delete a removed container's storage and cache directories."""
        assert name != DEFAULT, name
        errors = []
        for path in self._paths_for(name):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                errors.append(f"{path}: {e}")
        if errors:
            raise Error(f"Failed to delete container {name}'s data: "
                        f"{'; '.join(errors)}")

    def move_storage(self, old: str, new: str) -> None:
        """Move a container's directories, undoing a partial move on failure."""
        assert DEFAULT not in (old, new), (old, new)
        moves = list(zip(self._paths_for(old), self._paths_for(new)))
        for _source, target in moves:
            if target.exists():
                raise Error(f"Can't rename container {old}: {target} "
                            "already exists")
        done: list[tuple[pathlib.Path, pathlib.Path]] = []
        for source, target in moves:
            if not source.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
            except OSError as e:
                error = f"Failed to move {source} to {target}: {e}"
                stuck = []
                for moved_source, moved_target in reversed(done):
                    try:
                        moved_target.rename(moved_source)
                    except OSError as undo_e:
                        stuck.append(f"{moved_target} also failed ({undo_e})")
                if stuck:
                    error += (f"; moving back {', '.join(stuck)}, so it stays "
                              f"under container {new} and the rest is under "
                              f"container {old}")
                raise Error(error)
            done.append((source, target))


registry = cast(ContainerRegistry, None)


def all_storage_dirs() -> list[pathlib.Path]:
    """Get the storage directory of every container that has one on disk.

    Startup cleanups use this before any profile exists, so it looks at the
    disk instead of the registry.
    """
    data_dir = pathlib.Path(standarddir.data())
    container_dirs = sorted(path for path in (data_dir / 'containers').glob('*')
                            if path.is_dir())
    return [data_dir / 'webengine', *container_dirs]


def init(parent: QObject | None = None) -> None:
    """Create the module-level registry and read the runtime file."""
    global registry
    registry = ContainerRegistry(pathlib.Path(standarddir.data()),
                                 pathlib.Path(standarddir.cache()),
                                 parent=parent)
    registry.load()
    config.instance.changed.connect(registry.on_config_changed)
