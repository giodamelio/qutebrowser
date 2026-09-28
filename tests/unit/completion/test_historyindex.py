# SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the in-memory HistoryIndex."""

from qutebrowser.qt.core import QUrl

from qutebrowser.completion import historyindex


def _counting_load(monkeypatch):
    """Wrap HistoryIndex._load to count how often it actually loads."""
    calls = []
    orig_load = historyindex.HistoryIndex._load

    def wrapper(self):
        calls.append(1)
        orig_load(self)

    monkeypatch.setattr(historyindex.HistoryIndex, '_load', wrapper)
    return calls


def test_lazy_load_happens_once(web_history, monkeypatch):
    calls = _counting_load(monkeypatch)
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')
    idx.match('')
    idx.match('anything')
    assert len(calls) == 1


def test_load_orders_newest_first_and_honours_max_items(web_history, config_stub):
    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A')
    web_history.add_url(QUrl('https://b.example/'), atime=3, title='B')
    web_history.add_url(QUrl('https://c.example/'), atime=2, title='C')
    config_stub.val.completion.web_history.max_items = 2

    idx = historyindex.HistoryIndex(web_history)
    urls = [idx.entry(slot)[0] for slot in idx.match('')]
    assert urls == ['https://b.example/', 'https://c.example/']


def test_completion_added_makes_a_new_url_match(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')  # force the (empty) initial load

    web_history.add_url(QUrl('https://example.com/wombat'), atime=1, title='Wombat')

    assert [idx.entry(slot)[0] for slot in idx.match('wombat')] == [
        'https://example.com/wombat']


def test_completion_added_moves_url_to_newest(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A1')
    web_history.add_url(QUrl('https://b.example/'), atime=2, title='B')
    web_history.add_url(QUrl('https://a.example/'), atime=3, title='A2')

    entries = [idx.entry(slot) for slot in idx.match('')]
    assert entries == [
        ('https://a.example/', 'A2', 3),
        ('https://b.example/', 'B', 2),
    ]
    # The old slot's title never shows up again.
    assert idx.match('A1') == []


def test_completion_removed_drops_the_entry(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A')
    web_history.delete_url(QUrl('https://a.example/'))

    assert idx.match('') == []


def test_history_cleared_empties_the_index(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A')
    web_history.clear()

    assert idx.match('') == []


def test_max_items_change_forces_a_reload(web_history, config_stub, monkeypatch):
    calls = _counting_load(monkeypatch)
    idx = historyindex.HistoryIndex(web_history)

    idx.match('')
    assert len(calls) == 1
    idx.match('')
    assert len(calls) == 1

    config_stub.val.completion.web_history.max_items = 5
    idx.match('')
    assert len(calls) == 2


def test_compaction_keeps_results_identical(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://keep.example/'), atime=0, title='Keep')
    for i in range(1, 20):
        web_history.add_url(QUrl('https://repeat.example/'), atime=i,
                            title=f'Repeat {i}')

    # Enough replacements of the same URL must have triggered a compaction.
    assert len(idx._urls) == 2

    entries = sorted(idx.entry(slot) for slot in idx.match(''))
    assert entries == sorted([
        ('https://keep.example/', 'Keep', 0),
        ('https://repeat.example/', 'Repeat 19', 19),
    ])


def test_empty_pattern_lists_newest_first(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A')
    web_history.add_url(QUrl('https://b.example/'), atime=2, title='B')

    urls = [idx.entry(slot)[0] for slot in idx.match('')]
    assert urls == ['https://b.example/', 'https://a.example/']


def test_equal_matches_rank_newer_first(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://old.example/'), atime=1, title='wombat')
    web_history.add_url(QUrl('https://new.example/'), atime=2, title='wombat')

    urls = [idx.entry(slot)[0] for slot in idx.match('wombat')]
    assert urls == ['https://new.example/', 'https://old.example/']


def test_a_better_match_beats_a_newer_weak_one(web_history):
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    web_history.add_url(QUrl('https://a.example/'), atime=1, title='wombat')
    web_history.add_url(
        QUrl('https://b.example/'), atime=2,
        title='a very long page title that mentions wom somewhere inside it')

    urls = [idx.entry(slot)[0] for slot in idx.match('wom')]
    assert urls[0] == 'https://a.example/'


class TestSingleton:

    def test_get_creates_and_reuses_an_instance(self, web_history):
        historyindex.instance = None
        idx = historyindex.get()
        assert historyindex.instance is idx
        assert historyindex.get() is idx

    def test_web_history_fixture_resets_the_instance(self, web_history):
        idx = historyindex.get()
        assert idx._web_history is web_history

    def test_web_history_fixture_resets_the_instance_again(self, web_history):
        # If the previous test's teardown hadn't reset historyindex.instance,
        # get() here would return an index still wired to the previous test's
        # (by-now torn down) web_history.
        idx = historyindex.get()
        assert idx._web_history is web_history


def test_entry_of_a_replaced_slot_is_none(web_history):
    idx = historyindex.HistoryIndex(web_history)
    web_history.add_url(QUrl('https://a.example/'), atime=1, title='A1')
    [old_slot] = idx.match('')

    web_history.add_url(QUrl('https://a.example/'), atime=2, title='A2')

    assert idx.entry(old_slot) is None
    assert idx.positions(old_slot, 0) == []


def test_added_entries_respect_max_items(web_history, config_stub):
    config_stub.val.completion.web_history.max_items = 2
    idx = historyindex.HistoryIndex(web_history)
    idx.match('')

    for atime, name in enumerate(['a', 'b', 'c'], start=1):
        web_history.add_url(QUrl(f'https://{name}.example/'), atime=atime, title=name)

    assert [idx.entry(slot)[0] for slot in idx.match('')] == [
        'https://c.example/', 'https://b.example/']


def test_positions_survive_compaction(web_history):
    idx = historyindex.HistoryIndex(web_history)
    web_history.add_url(QUrl('https://wombat.example/'), atime=1, title='')
    web_history.add_url(QUrl('https://other.example/'), atime=2, title='')
    [slot] = idx.match('wombat')

    # Replacing the other entry twice leaves 2 of 4 positions dead, which
    # compacts.
    web_history.add_url(QUrl('https://other.example/'), atime=3, title='')
    web_history.add_url(QUrl('https://other.example/'), atime=4, title='')

    assert idx.positions(slot, 0) == list(range(8, 14))
