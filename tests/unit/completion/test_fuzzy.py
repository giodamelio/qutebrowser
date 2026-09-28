# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for qutebrowser.completion.fuzzy."""

import logging

import pytest

from qutebrowser.completion import fuzzy


def _ranked(rows, pattern, columns=None):
    scorer = fuzzy.Scorer(rows, columns or [fuzzy.Column(i) for i in range(len(rows[0]))])
    scorer.set_pattern(pattern)
    return [rows[i] for i in scorer.ranked()]


def test_empty_pattern_keeps_everything_in_order():
    rows = [('b',), ('a',)]
    assert _ranked(rows, '') == rows


@pytest.mark.parametrize('pattern, row', [
    ('qtb', ('qutebrowser',)),
    ('ghub', ('github.com/qutebrowser',)),
    ('hn', ('Hacker News',)),
])
def test_abbreviations_match(pattern, row):
    assert _ranked([row, ('unrelated',)], pattern) == [row]


@pytest.mark.parametrize('pattern', ['qutebrwoser', 'qutbrowser'])
def test_typos_match_long_terms(pattern):
    assert _ranked([('qutebrowser',)], pattern) == [('qutebrowser',)]


def test_short_terms_get_no_typos():
    rows = [('https://example.com',), ('github',)]
    assert _ranked(rows, 'gh') == [('github',)]


def test_terms_match_across_columns():
    rows = [('https://python.org', 'Wikipedia'), ('https://python.org', 'Docs')]
    assert _ranked(rows, 'wiki py') == [rows[0]]


def test_term_scores_add_up():
    scorer = fuzzy.Scorer([('foo', 'bar')], [fuzzy.Column(0), fuzzy.Column(1)])
    scorer.set_pattern('foo')
    foo = scorer.score(0)
    scorer.set_pattern('bar')
    bar = scorer.score(0)
    scorer.set_pattern('foo bar')
    assert scorer.score(0) == foo + bar


def test_negated_term_checks_every_column():
    rows = [('a', 'foo'), ('a', 'bar')]
    assert _ranked(rows, 'a !foo') == [rows[1]]


def test_weights_order_rows():
    rows = [('x', 'github'), ('github', 'x')]
    columns = [fuzzy.Column(0, weight=1.0), fuzzy.Column(1, weight=0.5)]
    assert _ranked(rows, 'github', columns) == [rows[1], rows[0]]


def test_fixed_matching_overrides_fuzzy():
    rows = [('0/3', 'x'), ('0/13', 'x'), ('0/31', 'x')]
    columns = [fuzzy.Column(0, matching='suffix'), fuzzy.Column(1)]
    assert set(_ranked(rows, '3', columns)) == {rows[0], rows[1]}


def test_ties_keep_source_order():
    rows = [('foo b',), ('foo a',)]
    assert _ranked(rows, 'foo') == rows


def test_unscored_columns_are_ignored():
    rows = [('a', 'secret')]
    assert _ranked(rows, 'secret', [fuzzy.Column(0)]) == []


def test_positions_are_utf16_offsets():
    scorer = fuzzy.Scorer([('😀 résumé',)], [fuzzy.Column(0)])
    scorer.set_pattern('résumé')
    # The emoji is 2 UTF-16 code units, the space 1.
    assert scorer.positions(0, 0) == [3, 4, 5, 6, 7, 8]


def test_positions_for_an_unmatched_cell_are_empty():
    scorer = fuzzy.Scorer([('foo', 'bar')], [fuzzy.Column(0), fuzzy.Column(1)])
    scorer.set_pattern('foo')
    assert scorer.positions(0, 1) == []


def test_positions_cover_every_needle_char():
    scorer = fuzzy.Scorer([('GitHub - qutebrowser/qutebrowser',)], [fuzzy.Column(0)])
    scorer.set_pattern('qutebrowser')
    assert len(scorer.positions(0, 0)) == len('qutebrowser')


def test_overlong_term_is_dropped(caplog):
    scorer = fuzzy.Scorer([('a',)], [fuzzy.Column(0)])
    with caplog.at_level(logging.WARNING):
        scorer.set_pattern('a ' + 'x' * 5000)
    assert scorer.ranked() == [0]


def test_row_count():
    scorer = fuzzy.Scorer([('a',), ('b',)], [fuzzy.Column(0)])
    assert scorer.row_count == 2
    assert len(scorer) == 2


def test_append_increases_row_count():
    scorer = fuzzy.Scorer([('a',)], [fuzzy.Column(0)])
    scorer.append(('b',))
    assert scorer.row_count == 2
    assert len(scorer) == 2


def test_append_is_visible_with_empty_pattern():
    scorer = fuzzy.Scorer([('a',)], [fuzzy.Column(0)])
    scorer.set_pattern('')
    scorer.append(('b',))
    assert scorer.ranked() == [0, 1]


def test_append_needs_set_pattern_rerun_to_be_scored():
    scorer = fuzzy.Scorer([('foo',)], [fuzzy.Column(0)])
    scorer.set_pattern('bar')
    assert scorer.ranked() == []
    scorer.append(('bar',))
    scorer.set_pattern('bar')
    assert scorer.ranked() == [1]


def test_clear_empties_the_scorer():
    scorer = fuzzy.Scorer([('a',), ('b',)], [fuzzy.Column(0)])
    scorer.set_pattern('a')
    scorer.clear()
    assert scorer.row_count == 0
    assert len(scorer) == 0
    scorer.set_pattern('a')
    assert scorer.ranked() == []


def test_clear_then_append_scores_only_the_new_row():
    scorer = fuzzy.Scorer([('a',), ('foo',)], [fuzzy.Column(0)])
    scorer.clear()
    scorer.append(('foo',))
    scorer.set_pattern('foo')
    assert scorer.ranked() == [0]
