# SPDX-FileCopyrightText: Ryan Roden-Corrent (rcorre) <ryan@rcorre.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Completion category that uses a list of tuples as a data source."""

from collections.abc import Iterable, Sequence

from qutebrowser.qt.core import QSortFilterProxyModel
from qutebrowser.qt.gui import QStandardItem, QStandardItemModel
from qutebrowser.qt.widgets import QWidget

from qutebrowser.completion import fuzzy
from qutebrowser.completion.models import util, BaseCategory
from qutebrowser.utils import qtutils, log, utils


_DEFAULT_WIDTH = 3


def _default_columns(rows: "list[tuple[str, ...]]") -> "list[fuzzy.Column]":
    """Score columns 0-2, or fewer if the rows are narrower (Decision 5)."""
    width = len(rows[0]) if rows else _DEFAULT_WIDTH
    return [fuzzy.Column(i) for i in range(min(_DEFAULT_WIDTH, width))]


class ListCategory(QSortFilterProxyModel, BaseCategory):

    """Expose a list of items as a category for the CompletionModel."""

    def __init__(self,
                 name: str,
                 items: Iterable[tuple[str, ...]],
                 sort: bool = True,
                 delete_func: util.DeleteFuncType | None = None,
                 parent: QWidget | None = None,
                 columns: Sequence[fuzzy.Column] | None = None,
                 rank: bool = True):
        super().__init__(parent)
        self.name = name
        self.srcmodel = QStandardItemModel(parent=self)
        self._pattern = ''
        rows = list(items)
        self._columns = list(columns) if columns is not None else _default_columns(rows)
        self.columns_to_filter = [c.index for c in self._columns]
        self._scorer = fuzzy.Scorer(rows, self._columns)
        self._rank = rank
        for item in rows:
            self.srcmodel.appendRow([QStandardItem(x) for x in item])
        self.setSourceModel(self.srcmodel)
        self.delete_func = delete_func
        self._sort = sort

    def set_pattern(self, val):
        """Setter for pattern.

        Args:
            val: The value to set.
        """
        if len(val) > 5000:  # avoid crash on huge search terms (#5973)
            log.completion.warning(f"Trimming {len(val)}-char pattern to 5000")
            val = val[:5000]
        self._pattern = val
        self._scorer.set_pattern(val)
        self.invalidate()
        self.sort(0)

    def filterAcceptsRow(self, source_row, source_parent):
        """Keep rows the fuzzy scorer matched (always true for an empty pattern)."""
        utils.unused(source_parent)
        return self._scorer.score(source_row) is not None

    def lessThan(self, lindex, rindex):
        """Rank by fuzzy score, falling back to the category's own order.

        Args:
            lindex: The QModelIndex of the left item (*left* < right), in the
                    source model.
            rindex: The QModelIndex of the right item (left < *right*), in
                    the source model.

        Return:
            True if left < right, else False
        """
        qtutils.ensure_valid(lindex)
        qtutils.ensure_valid(rindex)

        if self._pattern and self._rank:
            left_score = self._scorer.score(lindex.row())
            right_score = self._scorer.score(rindex.row())
            if left_score is None or right_score is None:  # pragma: no cover
                log.completion.warning(
                    "Got no score for a filtered-in row, "
                    "left={!r} right={!r} lindex={!r} rindex={!r}"
                    .format(left_score, right_score, lindex, rindex))
            elif left_score != right_score:
                return left_score > right_score

        if not self._sort:
            return False

        left = self.srcmodel.data(lindex)
        right = self.srcmodel.data(rindex)

        if left is None or right is None:  # pragma: no cover
            log.completion.warning("Got unexpected None value, "
                                   "left={!r} right={!r} "
                                   "lindex={!r} rindex={!r}"
                                   .format(left, right, lindex, rindex))
            return False

        return left < right

    def match_positions(self, row: int, column: int) -> "list[int] | None":
        """Positions the fuzzy scorer matched in this cell.

        Empty for an unscored column, never None: ListCategory always knows.
        """
        source_row = self.mapToSource(self.index(row, column)).row()
        return self._scorer.positions(source_row, column)
