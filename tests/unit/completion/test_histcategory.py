# SPDX-FileCopyrightText: Ryan Roden-Corrent (rcorre) <ryan@rcorre.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test the web history completion category."""

import datetime

import hypothesis
from hypothesis import strategies

from qutebrowser.qt.core import QUrl

from qutebrowser.completion.models import histcategory


def test_case_insensitive(web_history, model_validator):
    web_history.add_url(QUrl('https://example.com/FOO'), atime=0, title='')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('foo')
    model_validator.validate([('https://example.com/FOO', '')])


def test_percent_is_treated_literally(web_history, model_validator):
    web_history.add_url(QUrl('https://a.example/'), atime=0, title='100% done')
    web_history.add_url(QUrl('https://b.example/'), atime=0, title='normal')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('%')
    model_validator.validate([('https://a.example/', '100% done')])


def test_underscore_is_treated_literally(web_history, model_validator):
    web_history.add_url(QUrl('https://a.example/'), atime=0, title='a_b')
    web_history.add_url(QUrl('https://b.example/'), atime=0, title='abc')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('_')
    model_validator.validate([('https://a.example/', 'a_b')])


def test_max_items(web_history, config_stub, model_validator):
    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A')
    web_history.add_url(QUrl('https://b.example/'), atime=3, title='B')
    web_history.add_url(QUrl('https://c.example/'), atime=2, title='C')
    config_stub.val.completion.web_history.max_items = 2

    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('')
    model_validator.validate([
        ('https://b.example/', 'B'),
        ('https://c.example/', 'C'),
    ])


def test_timestamp_fmt(model_validator, config_stub, web_history):
    config_stub.val.completion.timestamp_format = '%Y-%m-%d'
    atime = datetime.datetime(2018, 2, 27, 8, 30)
    web_history.add_url(QUrl('https://example.com/foo'), atime=int(atime.timestamp()),
                        title='')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('')
    model_validator.validate([('https://example.com/foo', '', '2018-02-27')])


def test_remove_rows(web_history, model_validator):
    web_history.add_url(QUrl('https://foo.example/'), atime=0, title='Foo')
    web_history.add_url(QUrl('https://bar.example/'), atime=1, title='Bar')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('')

    web_history.delete_url(QUrl('https://foo.example/'))
    cat.removeRows(0, 1)

    model_validator.validate([('https://bar.example/', 'Bar')])


def test_remove_rows_fetch(web_history):
    """removeRows should fetch enough data to make the current index valid."""
    # we cannot use model_validator as it will fetch everything up front
    for i in range(300):
        web_history.add_url(QUrl(f'https://example.com/{i}'), atime=0, title=str(i))

    cat = histcategory.HistoryCategory()
    cat.set_pattern('')

    # sanity check that we didn't fetch everything up front
    assert cat.rowCount() < 300
    cat.fetchMore()
    assert cat.rowCount() == 300

    web_history.delete_url(QUrl('https://example.com/298'))
    cat.removeRows(297, 1)
    assert cat.rowCount() == 299


def test_live_update_shows_a_newly_visited_page(web_history, model_validator):
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('wombat')
    model_validator.validate([])

    web_history.add_url(QUrl('https://example.com/wombat'), atime=1, title='Wombat')
    cat.set_pattern('wombat')

    model_validator.validate([('https://example.com/wombat', 'Wombat')])


def test_live_update_hides_a_deleted_page(web_history, model_validator):
    web_history.add_url(QUrl('https://example.com/wombat'), atime=1, title='Wombat')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('wombat')
    model_validator.validate([('https://example.com/wombat', 'Wombat')])

    web_history.delete_url(QUrl('https://example.com/wombat'))
    cat.set_pattern('wombat')

    model_validator.validate([])


def test_live_update_history_clear_empties_it(web_history, model_validator):
    web_history.add_url(QUrl('https://example.com/wombat'), atime=1, title='Wombat')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('')
    model_validator.validate([('https://example.com/wombat', 'Wombat')])

    web_history.clear()
    cat.set_pattern('')

    model_validator.validate([])


def test_shown_row_survives_its_entry_being_replaced(web_history, model_validator):
    """A page loading while :open completion is open replaces its entry."""
    web_history.add_url(QUrl('https://example.com/wombat'), atime=1, title='Wombat')
    cat = histcategory.HistoryCategory()
    model_validator.set_model(cat)
    cat.set_pattern('wombat')

    web_history.add_url(QUrl('https://example.com/wombat'), atime=2, title='New')

    model_validator.validate([('https://example.com/wombat', 'Wombat')])
    assert cat.match_positions(0, 0) == []


def test_fetch_more_skips_removed_entries(web_history, config_stub):
    for i in range(histcategory._PAGE_SIZE + 2):
        web_history.add_url(QUrl(f'https://example.com/{i}'), atime=i, title='')
    cat = histcategory.HistoryCategory()
    cat.set_pattern('')

    web_history.delete_url(QUrl('https://example.com/0'))
    cat.fetchMore()

    assert cat.rowCount() == histcategory._PAGE_SIZE + 1
    assert not cat.canFetchMore()


@hypothesis.given(pat=strategies.text())
def test_set_pattern_hypothesis(web_history, pat):
    web_history.add_url(QUrl('https://example.com/foo'), atime=1, title='title1')
    cat = histcategory.HistoryCategory()
    cat.set_pattern(pat)
