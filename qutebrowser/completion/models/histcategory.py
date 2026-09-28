# SPDX-FileCopyrightText: Ryan Roden-Corrent (rcorre) <ryan@rcorre.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A completion category listing web history from an in-memory index."""

import time

from qutebrowser.qt.core import QAbstractTableModel, QModelIndex, QObject, Qt

from qutebrowser.completion import historyindex
from qutebrowser.completion.models import util, BaseCategory
from qutebrowser.config import config


# Paging granularity for fetchMore, matching QSqlQueryModel's default so an
# empty pattern over a huge history stays cheap to display.
_PAGE_SIZE = 256

_URL_COLUMN = 0
_TITLE_COLUMN = 1
_TIME_COLUMN = 2


class HistoryCategory(QAbstractTableModel, BaseCategory):

    """A completion category backed by the in-memory HistoryIndex."""

    def __init__(self, *, delete_func: util.DeleteFuncType | None = None,
                parent: QObject | None = None) -> None:
        """Create a new History completion category."""
        super().__init__(parent=parent)
        self.name = "History"
        # advertise that this model filters by URL and title
        self.columns_to_filter = [_URL_COLUMN, _TITLE_COLUMN]
        self.delete_func = delete_func
        self._pattern = ''
        self._slots: list[int] = []
        # Revealed rows as (slot, url, title, atime). Copied when revealed
        # because the index drops or replaces entries whenever a page loads,
        # including while this completion is open.
        self._rows: list[tuple[int, str, str, int]] = []
        self._cursor = 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else 3

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        return not parent.isValid() and self._cursor < len(self._slots)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        """Reveal up to one more page of already-ranked results."""
        if parent.isValid():
            return
        page = self._next_page()
        if not page:
            return
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(page) - 1)
        self._rows.extend(page)
        self.endInsertRows()

    def _next_page(self) -> list[tuple[int, str, str, int]]:
        """Snapshot the next page of ranked slots still in the index."""
        index = historyindex.get()
        page: list[tuple[int, str, str, int]] = []
        while self._cursor < len(self._slots) and len(page) < _PAGE_SIZE:
            slot = self._slots[self._cursor]
            self._cursor += 1
            entry = index.entry(slot)
            if entry is not None:
                page.append((slot, *entry))
        return page

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
        """Implement abstract method in QAbstractTableModel."""
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        _slot, url, title, atime = self._rows[index.row()]
        if index.column() == _URL_COLUMN:
            return url
        if index.column() == _TITLE_COLUMN:
            return title
        assert index.column() == _TIME_COLUMN, index.column()
        fmt = config.val.completion.timestamp_format
        return time.strftime(fmt, time.localtime(atime)) if fmt else ''

    def set_pattern(self, pattern: str) -> None:
        """Set the pattern used to filter and rank results."""
        self._pattern = pattern
        self.beginResetModel()
        self._slots = historyindex.get().match(pattern)
        self._cursor = 0
        self._rows = self._next_page()
        self.endResetModel()

    def removeRows(self, row: int, _count: int, _parent: QModelIndex = QModelIndex()) -> bool:
        """Override QAbstractItemModel::removeRows to re-run the pattern.

        The deletion itself goes through delete_func -> web_history.delete_url,
        which removes the entry from HistoryIndex via completion_removed.
        """
        self.set_pattern(self._pattern)
        while self.canFetchMore() and self.rowCount() < row:
            self.fetchMore()
        return True

    def match_positions(self, row: int, column: int) -> "list[int] | None":
        return historyindex.get().positions(self._rows[row][0], column)
