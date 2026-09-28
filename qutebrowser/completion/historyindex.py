# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""An in-memory index over CompletionHistory, kept current by WebHistory signals.

Loading 100k CompletionHistory rows through QtSql takes 240ms, too slow to redo
on every keystroke of `:open` completion. HistoryIndex loads once, lazily, and
after that stays in sync through WebHistory.completion_added,
completion_removed and history_cleared instead of re-querying.
"""

from qutebrowser.qt.core import QObject, pyqtSlot

from qutebrowser.browser import history
from qutebrowser.completion import fuzzy
from qutebrowser.config import config


# One matched character's contribution to frizbee's score (Decision 7): a
# clearly better match still beats a newer, weaker one.
RECENCY_BONUS = 12

# Scorer rows are (url, title); the third displayed column (time) isn't text
# to search, so it isn't scored.
_COLUMNS = [fuzzy.Column(0), fuzzy.Column(1)]


class HistoryIndex(QObject):

    """A ranked, in-memory view of WebHistory.completion.

    Slots are stable identifiers callers keep across calls (HistoryCategory
    caches the list match() returns until its next set_pattern). Internally,
    entries live in arrays ordered oldest first, so appends are cheap and an
    entry's position from the end gives its recency. Replacing or removing an
    entry tombstones its array position instead of shifting the arrays;
    tombstoned positions are skipped in match() and compacted away once they
    pile up, which renumbers array positions but never a slot's identity, so
    a slot a caller is still holding either still resolves or is gone.
    """

    # Compact once tombstones exceed this fraction of all positions.
    _COMPACT_FRACTION = 4

    def __init__(self, web_history: 'history.WebHistory',
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._web_history = web_history
        self._urls: list[str] = []
        self._titles: list[str] = []
        self._atimes: list[int] = []
        self._alive: list[bool] = []
        self._dead = 0
        # The first array position that may still be alive, so trimming to
        # max_items doesn't rescan tombstones at the front.
        self._oldest = 0
        # The slot stored at each array position, and the reverse lookup.
        self._slots: list[int] = []
        self._pos_of_slot: dict[int, int] = {}
        self._pos_of_url: dict[str, int] = {}
        self._next_slot = 0
        self._pattern = ''
        self._scorer = fuzzy.Scorer([], _COLUMNS)
        self._loaded = False

        web_history.completion_added.connect(self._on_completion_added)
        web_history.completion_removed.connect(self._on_completion_removed)
        web_history.history_cleared.connect(self._on_history_cleared)
        config.instance.changed.connect(self._on_config_changed)

    def match(self, pattern: str) -> "list[int]":
        """Live slots for pattern, best first (Decision 7).

        With an empty pattern, all live slots newest first.
        """
        self._ensure_loaded()
        self._pattern = pattern
        self._scorer.set_pattern(pattern)
        if not pattern:
            positions = [pos for pos in range(len(self._alive) - 1, -1, -1)
                         if self._alive[pos]]
        else:
            positions = sorted(
                (pos for pos in self._scorer.ranked() if self._alive[pos]),
                key=self._rank_key)
        return [self._slots[pos] for pos in positions]

    def entry(self, slot: int) -> "tuple[str, str, int] | None":
        """The (url, title, atime) at a slot from match(), or None once removed.

        Entries change under an open completion whenever a page loads, so a
        slot a caller holds can legitimately be gone.
        """
        pos = self._pos_of_slot.get(slot)
        if pos is None:
            return None
        return (self._urls[pos], self._titles[pos], self._atimes[pos])

    def positions(self, slot: int, column: int) -> "list[int]":
        """Match positions for a slot/column, as scored by the last match() call."""
        pos = self._pos_of_slot.get(slot)
        if pos is None:
            return []
        return self._scorer.positions(pos, column)

    def _rank_key(self, pos: int) -> "tuple[float, int]":
        score = self._scorer.score(pos)
        assert score is not None, pos
        # Recency by array position: tombstones make it approximate, but
        # compaction keeps them under a quarter of the positions, and
        # computing exact live ranks would cost O(n) per keystroke.
        recency = RECENCY_BONUS * (pos + 1) / len(self._alive)
        return (-(score + recency), -pos)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        """Load the newest completion.web_history.max_items rows from the database."""
        self._reset_state()

        max_items = config.val.completion.web_history.max_items
        # HistoryCategory isn't added to the completion at all in that case.
        assert max_items != 0

        querystr = ('SELECT url, title, last_atime FROM CompletionHistory '
                   'ORDER BY last_atime DESC')
        if max_items > 0:
            querystr += ' LIMIT :limit'
        query = self._web_history.database.query(querystr)
        query.run(**({'limit': max_items} if max_items > 0 else {}))

        # Newest first from SQL, stored oldest first. CompletionHistory's url
        # is unique, so there are no tombstones to track while loading.
        for entry in reversed(list(query)):
            pos = len(self._urls)
            self._urls.append(entry.url)
            self._titles.append(entry.title)
            self._atimes.append(entry.last_atime)
            self._pos_of_url[entry.url] = pos
        count = len(self._urls)
        self._alive = [True] * count
        self._slots = list(range(count))
        self._pos_of_slot = {slot: slot for slot in self._slots}
        self._next_slot = count
        self._scorer = fuzzy.Scorer(list(zip(self._urls, self._titles)), _COLUMNS)
        self._loaded = True

    def _reset_state(self) -> None:
        self._urls = []
        self._titles = []
        self._atimes = []
        self._alive = []
        self._dead = 0
        self._oldest = 0
        self._slots = []
        self._pos_of_slot = {}
        self._pos_of_url = {}
        self._scorer = fuzzy.Scorer([], _COLUMNS)

    def _append(self, url: str, title: str, atime: int) -> None:
        old_pos = self._pos_of_url.get(url)
        if old_pos is not None:
            self._tombstone(old_pos)
        slot = self._next_slot
        self._next_slot += 1
        pos = len(self._urls)
        self._pos_of_url[url] = pos
        self._pos_of_slot[slot] = pos
        self._urls.append(url)
        self._titles.append(title)
        self._atimes.append(atime)
        self._alive.append(True)
        self._slots.append(slot)
        self._scorer.append((url, title))
        self._trim_to_max_items()
        self._maybe_compact()

    def _trim_to_max_items(self) -> None:
        max_items = config.val.completion.web_history.max_items
        if max_items < 0:
            return
        while len(self._alive) - self._dead > max_items:
            while not self._alive[self._oldest]:
                self._oldest += 1
            del self._pos_of_url[self._urls[self._oldest]]
            self._tombstone(self._oldest)

    def _tombstone(self, pos: int) -> None:
        self._alive[pos] = False
        self._dead += 1
        del self._pos_of_slot[self._slots[pos]]

    def _maybe_compact(self) -> None:
        if self._dead * self._COMPACT_FRACTION > len(self._alive):
            self._compact()

    def _compact(self) -> None:
        live = [pos for pos, alive in enumerate(self._alive) if alive]
        self._urls = [self._urls[pos] for pos in live]
        self._titles = [self._titles[pos] for pos in live]
        self._atimes = [self._atimes[pos] for pos in live]
        self._slots = [self._slots[pos] for pos in live]
        self._alive = [True] * len(live)
        self._dead = 0
        self._oldest = 0
        self._pos_of_url = {url: pos for pos, url in enumerate(self._urls)}
        self._pos_of_slot = {slot: pos for pos, slot in enumerate(self._slots)}
        self._scorer = fuzzy.Scorer(list(zip(self._urls, self._titles)), _COLUMNS)
        # Keeps positions() working for rows an open completion still shows.
        self._scorer.set_pattern(self._pattern)

    @pyqtSlot(str, str, int)
    def _on_completion_added(self, url: str, title: str, atime: int) -> None:
        if not self._loaded:
            # Will be picked up by _load() the next time match() is called.
            return
        self._append(url, title, atime)

    @pyqtSlot(str)
    def _on_completion_removed(self, url: str) -> None:
        if not self._loaded:
            return
        pos = self._pos_of_url.pop(url, None)
        if pos is not None:
            self._tombstone(pos)
            self._maybe_compact()

    @pyqtSlot()
    def _on_history_cleared(self) -> None:
        self._reset_state()
        self._loaded = True

    @pyqtSlot(str)
    def _on_config_changed(self, option: str) -> None:
        if option == 'completion.web_history.max_items':
            self._loaded = False


instance: "HistoryIndex | None" = None


def get() -> HistoryIndex:
    """Return the module-level HistoryIndex, creating it against history.web_history."""
    global instance
    if instance is None:
        instance = HistoryIndex(history.web_history)
    return instance
