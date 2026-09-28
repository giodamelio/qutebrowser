# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""fzf-style fuzzy matching over table rows, with per-column priorities."""

import dataclasses
import math
from collections.abc import Iterable, Sequence

import frizbee

from qutebrowser.utils import log


MAX_TYPOS = 2


@dataclasses.dataclass(frozen=True)
class Column:

    """How one column of a row takes part in matching.

    Attributes:
        index: The column's index in the row.
        weight: Multiplies this column's score.
        matching: A frizbee matching mode that overrides fuzzy terms for this
                  column, e.g. "prefix" for tab indices; None keeps each
                  term's own mode.
    """

    index: int
    weight: float = 1.0
    matching: "frizbee.Matching | None" = None


def _typos(pattern: "frizbee.Pattern") -> int:
    """Typo budget for one pattern (Decision 3: len(needle) // 4, capped).

    Literal modes (prefix/suffix/substring/exact) and negated terms match
    exactly; only plain fuzzy terms get typo tolerance.
    """
    if pattern.negated or pattern.matching not in (None, 'fuzzy'):
        return 0
    return min(len(pattern.needle) // 4, MAX_TYPOS)


def _utf16_offsets(text: str, byte_offsets: "Iterable[int]") -> "list[int]":
    """Convert frizbee's UTF-8 byte offsets to Qt's UTF-16 offsets.

    Args:
        text: The haystack the offsets were matched against.
        byte_offsets: UTF-8 byte offsets, as returned by frizbee.

    Return: The corresponding UTF-16 code unit offsets, in text order.
    """
    wanted = set(byte_offsets)
    result = []
    byte_pos = 0
    utf16_pos = 0
    for char in text:
        if byte_pos in wanted:
            result.append(utf16_pos)
        byte_pos += len(char.encode('utf-8'))
        utf16_pos += 2 if ord(char) > 0xFFFF else 1
    return result


class Scorer:

    """Ranks and highlights table rows against an fzf-style query.

    Each Column names a row index to match against; a row survives when
    every non-negated term of the pattern matched in at least one scored
    column and no negated term matched in any of them (Decision 4). The
    row's score is the sum, over positive terms, of the best
    ``column.weight * match.score`` among the columns that term matched.
    """

    def __init__(self, rows: "Sequence[tuple[str, ...]]", columns: "Sequence[Column]"):
        self._rows: "list[tuple[str, ...]]" = list(rows)
        self._columns = list(columns)
        self._haystacks: "dict[int, frizbee.Haystacks]" = {
            column.index: frizbee.Haystacks(row[column.index] for row in self._rows)
            for column in self._columns
        }
        self._pattern = ''
        self._patterns: "list[frizbee.Pattern]" = []
        self._scores: "dict[int, float]" = {}
        self._positions_cache: "dict[tuple[int, int], list[int]]" = {}

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def append(self, row: "tuple[str, ...]") -> None:
        """Add one row at the end (its index is the previous row count).

        This updates every scored column's Haystacks but does not rescore.
        With an empty pattern the new row appears in ranked() right away,
        since that path always lists every current row. With a non-empty
        pattern, call set_pattern() again to have the new row considered;
        HistoryIndex's match() does this on every keystroke anyway.
        """
        self._rows.append(row)
        for column in self._columns:
            self._haystacks[column.index].append(row[column.index])

    def clear(self) -> None:
        """Empty all rows, their haystacks and any scores from set_pattern."""
        self._rows.clear()
        for haystack in self._haystacks.values():
            haystack.clear()
        self._scores = {}
        self._positions_cache = {}

    def set_pattern(self, pattern: str) -> None:
        """Parse the query and score every row against it.

        Args:
            pattern: An fzf-style query, see frizbee.Pattern.from_query.
        """
        self._pattern = pattern
        self._positions_cache = {}
        self._patterns = self._parse(pattern)

        if not pattern:
            self._scores = {}
            return

        positive = [p for p in self._patterns if not p.negated]
        negative = [
            frizbee.Pattern(p.needle, negated=False, matching=p.matching, max_typos=p.max_typos)
            for p in self._patterns if p.negated
        ]
        scores = self._score_positive(positive) if positive else {
            row: 0.0 for row in range(len(self._rows))
        }
        self._exclude_negative(scores, negative)
        self._scores = scores

    def _parse(self, pattern: str) -> "list[frizbee.Pattern]":
        """Parse a query, dropping terms too long for frizbee to score."""
        patterns = []
        for candidate in frizbee.Pattern.from_query(pattern):
            if len(candidate.needle) > frizbee.max_needle_len():
                log.completion.warning(
                    f"Dropping {len(candidate.needle)}-char term, "
                    f"longer than frizbee.max_needle_len()")
                continue
            candidate.max_typos = _typos(candidate)
            patterns.append(candidate)
        return patterns

    def _score_positive(self, positive: "list[frizbee.Pattern]") -> "dict[int, float]":
        """Best weighted score per row, kept only if every term hit somewhere."""
        totals: "dict[int, float]" = {}
        hit_counts: "dict[int, int]" = {}
        for term in positive:
            term_best: "dict[int, float]" = {}
            for column in self._columns:
                for match in self._matches(term, column):
                    weighted = column.weight * match.score
                    if weighted > term_best.get(match.index, -math.inf):
                        term_best[match.index] = weighted
            for row, score in term_best.items():
                totals[row] = totals.get(row, 0.0) + score
                hit_counts[row] = hit_counts.get(row, 0) + 1
        return {
            row: score for row, score in totals.items()
            if hit_counts[row] == len(positive)
        }

    def _exclude_negative(
            self, scores: "dict[int, float]", negative: "list[frizbee.Pattern]") -> None:
        """Drop any row a negated term (checked positively) matches."""
        for term in negative:
            for column in self._columns:
                for match in self._matches(term, column):
                    scores.pop(match.index, None)

    def _matches(self, pattern: "frizbee.Pattern", column: Column) -> "list[frizbee.Match]":
        return self._matcher(pattern, column).match_list(self._haystacks[column.index])

    def _matcher(self, pattern: "frizbee.Pattern", column: Column) -> "frizbee.Matcher":
        matching = (column.matching
                    if column.matching and pattern.matching in (None, 'fuzzy')
                    else None)
        return frizbee.Matcher(pattern, casing='smart', matching=matching)

    def ranked(self) -> "list[int]":
        """Row indices to show, best match first.

        With an empty pattern, all rows in source order. Otherwise the
        surviving rows sorted by score descending, ties broken by row index
        (source order).
        """
        if not self._pattern:
            return list(range(len(self._rows)))
        return sorted(self._scores, key=lambda row: (-self._scores[row], row))

    def score(self, row: int) -> "float | None":
        """The row's score, or None if it didn't survive the pattern."""
        if not self._pattern:
            return 0.0
        return self._scores.get(row)

    def positions(self, row: int, column: int) -> "list[int]":
        """Character offsets (UTF-16 code units) the pattern matched.

        Empty for a row that didn't survive the pattern, or a column that
        isn't scored. Cached per (row, column) until the next set_pattern.
        """
        if self.score(row) is None or column not in self._haystacks:
            return []
        cached = self._positions_cache.get((row, column))
        if cached is not None:
            return cached

        text = self._rows[row][column]
        col_spec = next(c for c in self._columns if c.index == column)
        byte_offsets: "set[int]" = set()
        for term in self._patterns:
            if term.negated:
                continue
            matcher = self._matcher(term, col_spec)
            result = matcher.match_one_indices(text, row)
            if result is not None:
                byte_offsets.update(result.indices)

        offsets = sorted(_utf16_offsets(text, byte_offsets))
        self._positions_cache[(row, column)] = offsets
        return offsets
