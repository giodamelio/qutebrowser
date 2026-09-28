# SPDX-FileCopyrightText: Ryan Roden-Corrent (rcorre) <ryan@rcorre.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for CompletionFilterModel."""

import logging

import pytest

from qutebrowser.completion import fuzzy
from qutebrowser.completion.models import listcategory


@pytest.mark.parametrize('pattern, before, after, after_nosort', [
    ('foo',
     [('foo', ''), ('bar', '')],
     [('foo', '')],
     [('foo', '')]),

    # ranking beats alphabetical: foobar starts with "foo", barfoo doesn't
    ('foo',
     [('barfoo', ''), ('foobar', '')],
     [('foobar', ''), ('barfoo', '')],
     [('foobar', ''), ('barfoo', '')]),

    # ties keep the category's own order: alphabetical, or source order
    ('foo',
     [('foob', ''), ('fooc', ''), ('fooa', '')],
     [('fooa', ''), ('foob', ''), ('fooc', '')],
     [('foob', ''), ('fooc', ''), ('fooa', '')]),

    # a two-term query, either order
    ('foo bar',
     [('foobar', ''), ('barfoo', ''), ('foobaz', '')],
     [('barfoo', ''), ('foobar', '')],
     [('foobar', ''), ('barfoo', '')]),

    # abbreviation
    ('fb',
     [('foobar', ''), ('unrelated', '')],
     [('foobar', '')],
     [('foobar', '')]),

    # typo
    ('fooobar',
     [('foobar', ''), ('unrelated', '')],
     [('foobar', '')],
     [('foobar', '')]),
])
def test_set_pattern(pattern, before, after, after_nosort, model_validator):
    """Validate the filtering and sorting results of set_pattern."""
    cat = listcategory.ListCategory('Foo', before)
    model_validator.set_model(cat)
    cat.set_pattern(pattern)
    model_validator.validate(after)

    cat = listcategory.ListCategory('Foo', before, sort=False)
    model_validator.set_model(cat)
    cat.set_pattern(pattern)
    model_validator.validate(after_nosort)


def test_long_pattern(caplog, model_validator):
    """Validate that a huge pattern doesn't crash (#5973)."""
    with caplog.at_level(logging.WARNING):
        cat = listcategory.ListCategory('Foo', [('a' * 5000, '')])
        model_validator.set_model(cat)
        cat.set_pattern('a' * 50000)
        model_validator.validate([('a' * 5000, '')])


def test_rank_false_keeps_source_order(model_validator):
    """rank=False filters but never reorders, even with a clear best match."""
    before = [('foobaz', ''), ('foobar', ''), ('barfoo', '')]
    cat = listcategory.ListCategory('Foo', before, sort=False, rank=False)
    model_validator.set_model(cat)
    cat.set_pattern('foo')
    model_validator.validate([('foobaz', ''), ('foobar', ''), ('barfoo', '')])


def test_columns_weights(model_validator):
    """A match in a higher-weighted column outranks one in a lower one."""
    before = [('x', 'github'), ('github', 'x')]
    columns = [fuzzy.Column(0, weight=1.0), fuzzy.Column(1, weight=0.5)]
    cat = listcategory.ListCategory('Foo', before, columns=columns)
    model_validator.set_model(cat)
    cat.set_pattern('github')
    model_validator.validate([('github', 'x'), ('x', 'github')])


def test_match_positions(model_validator):
    cat = listcategory.ListCategory('Foo', [('foobar', '')])
    model_validator.set_model(cat)
    cat.set_pattern('foo')
    assert cat.match_positions(0, 0) == [0, 1, 2]
    assert cat.match_positions(0, 1) == []
