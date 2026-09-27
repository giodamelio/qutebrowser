## Subagent model tiers (overrides superpowers Model Selection)
- cheap tier = sonnet
- standard tier = sonnet
- most capable tier = opus
- Never dispatch a subagent on Fable, until the final whole-branch review

## Running tests
- Tests must run headless. Gio's session is Wayland, so Qt ignores pytest-xvfb's virtual X display unless forced, and test windows steal focus on the real screen.
- Always run pytest inside the devenv shell with the Wayland socket hidden and Qt forced onto X11, so pytest-xvfb's Xvfb gets every window:
  `devenv shell -- env -u WAYLAND_DISPLAY QT_QPA_PLATFORM=xcb python -m pytest ...`
- With pytest-xdist (`-n auto`), also pass `--benchmark-disable`, or pytest-benchmark's warning becomes an internal error under `filterwarnings = error`.
- Never run any test that opens a window (unit or end-to-end) without that headless setup. If headless is not possible, ask Gio for explicit permission first.
- In a sandbox with a read-only home and no GL driver, also point the home and XDG directories at a writable directory and use software rendering, or devenv can't start and QtWebEngine aborts ("GLX is not present"):
  `XDG_DATA_HOME=$D/data XDG_CACHE_HOME=$D/cache XDG_STATE_HOME=$D/state devenv shell -- env -u WAYLAND_DISPLAY HOME=$D/home QT_QPA_PLATFORM=xcb QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 python -m pytest ...`
- Some failures there are the sandbox, not the code: pakjoy's "Couldn't find webengine resources dir", the D-Bus notification tests, and the dark mode tests. Compare against the parent branch in the same environment before calling a failure new.

## Linting
- Run mypy like tox's `mypy-pyqt6` environment, plus `--no-native-parser` because the native parser can't load in the devenv:
  `python -m mypy --no-native-parser --always-true=USE_PYQT6 --always-false=USE_PYQT5 --always-false=USE_PYSIDE6 --always-false=IS_QT5 --always-true=IS_QT6 --always-true=IS_PYQT --always-false=IS_PYSIDE`
- The devenv lacks PyQt's type stubs, so mypy reports about 1400 errors on upstream code too (e.g. `Module "qutebrowser.qt.core" has no attribute "QUrl"`, "unfollowed import", "untyped decorator"). Compare against the parent branch and look only at new errors.
