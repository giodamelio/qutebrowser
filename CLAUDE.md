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
