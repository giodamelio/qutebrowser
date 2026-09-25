# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import pytest
from qutebrowser.qt.core import Qt

from qutebrowser.mainwindow import prompt as promptmod
from qutebrowser.utils import message, usertypes


class TestFileCompletion:

    @pytest.fixture
    def get_prompt(self, qtbot, config_stub, key_config_stub):
        """Get a function to display a prompt with a path."""
        config_stub.val.bindings.default = {}

        def _get_prompt_func(path):
            question = usertypes.Question()
            question.title = "test"
            question.default = path

            prompt = promptmod.DownloadFilenamePrompt(question)
            qtbot.add_widget(prompt)
            with qtbot.wait_signal(prompt._file_model.directoryLoaded):
                pass
            assert prompt._lineedit.text() == path

            return prompt
        return _get_prompt_func

    @pytest.mark.parametrize('steps, where, subfolder', [
        (1, 'next', 'a'),
        (1, 'prev', 'c'),
        (2, 'next', 'b'),
        (2, 'prev', 'b'),
    ])
    def test_simple_completion(self, tmp_path, get_prompt, steps, where,
                               subfolder):
        """Simply trying to tab through items."""
        testdir = tmp_path / 'test'
        for directory in 'abc':
            (testdir / directory).mkdir(parents=True)

        prompt = get_prompt(str(testdir) + os.sep)

        for _ in range(steps):
            prompt.item_focus(where)

        assert prompt._lineedit.text() == str((testdir / subfolder).resolve())

    def test_backspacing_path(self, qtbot, tmp_path, get_prompt):
        """When we start deleting a path we want to see the subdir."""
        testdir = tmp_path / 'test'

        for directory in ['bar', 'foo']:
            (testdir / directory).mkdir(parents=True)

        prompt = get_prompt(str(testdir / 'foo') + os.sep)

        # Deleting /f[oo/]
        with qtbot.wait_signal(prompt._file_model.directoryLoaded):
            for _ in range(3):
                qtbot.keyPress(prompt._lineedit, Qt.Key.Key_Backspace)

        # For some reason, this isn't always called when using qtbot.keyPress.
        prompt._set_fileview_root(prompt._lineedit.text())

        # 'foo' should get completed from 'f'
        prompt.item_focus('next')
        assert prompt._lineedit.text() == str(testdir / 'foo')

        # Deleting /[foo]
        for _ in range(3):
            qtbot.keyPress(prompt._lineedit, Qt.Key.Key_Backspace)

        # We should now show / again, so tabbing twice gives us bar -> foo
        prompt.item_focus('next')
        prompt.item_focus('next')
        assert prompt._lineedit.text() == str(testdir / 'foo')

    @pytest.mark.parametrize("keys, expected", [
        ([], ['bar', 'bat', 'foo']),
        ([Qt.Key.Key_F], ['foo']),
        ([Qt.Key.Key_A], ['bar', 'bat']),
    ])
    def test_filtering_path(self, qtbot, tmp_path, get_prompt, keys, expected):
        testdir = tmp_path / 'test'

        for directory in ['bar', 'foo', 'bat']:
            (testdir / directory).mkdir(parents=True)

        prompt = get_prompt(str(testdir) + os.sep)
        for key in keys:
            qtbot.keyPress(prompt._lineedit, key)
        prompt._set_fileview_root(prompt._lineedit.text())

        num_rows = prompt._file_model.rowCount(prompt._file_view.rootIndex())
        visible = []
        for row in range(num_rows):
            parent = prompt._file_model.index(
                os.path.dirname(prompt._lineedit.text()))
            index = prompt._file_model.index(row, 0, parent)
            if not prompt._file_view.isRowHidden(index.row(), index.parent()):
                visible.append(index.data())
        assert visible == expected

    @pytest.mark.linux
    def test_root_path(self, get_prompt):
        """With / as path, show root contents."""
        prompt = get_prompt('/')
        assert prompt._file_model.rootPath() == '/'


OPTIONS = [
    ('work', 'work / default', 'Work page'),
    ('play', 'play / games', 'Game page'),
    ('mail', 'mail / default', 'Inbox'),
]


class TestSelectPrompt:

    @pytest.fixture
    def get_prompt(self, qtbot, config_stub, key_config_stub):
        """Get a function to display a select prompt with some rows."""
        config_stub.val.bindings.default = {}

        def _get_prompt_func(options=OPTIONS):
            question = usertypes.Question()
            question.title = "Pick one"
            question.mode = usertypes.PromptMode.select
            question.options = options
            prompt = promptmod.SelectPrompt(question)
            qtbot.add_widget(prompt)
            return prompt
        return _get_prompt_func

    def _labels(self, prompt):
        model = prompt._model
        return [model.index(row, 1).data() for row in range(model.rowCount())]

    def test_rows_keep_their_order(self, get_prompt):
        prompt = get_prompt()
        assert self._labels(prompt) == [
            'work / default', 'play / games', 'mail / default']

    def test_accept_selects_first_row(self, get_prompt):
        prompt = get_prompt()
        assert prompt.accept()
        assert prompt.question.answer == 'work'

    @pytest.mark.parametrize('steps, which, key', [
        (1, 'next', 'play'),
        (2, 'next', 'mail'),
        (3, 'next', 'work'),
        (1, 'prev', 'mail'),
    ])
    def test_item_focus(self, get_prompt, steps, which, key):
        prompt = get_prompt()
        for _ in range(steps):
            prompt.item_focus(which)
        prompt.accept()
        assert prompt.question.answer == key

    def test_typing_filters(self, qtbot, get_prompt):
        prompt = get_prompt()
        qtbot.keyClicks(prompt._lineedit, 'default')
        assert self._labels(prompt) == ['work / default', 'mail / default']
        prompt.item_focus('next')
        prompt.accept()
        assert prompt.question.answer == 'mail'

    def test_nothing_matches(self, qtbot, get_prompt):
        prompt = get_prompt()
        qtbot.keyClicks(prompt._lineedit, 'zzz')
        assert self._labels(prompt) == []
        prompt.item_focus('next')
        with pytest.raises(promptmod.Error, match='No item matches'):
            prompt.accept()
        assert prompt.question.answer is None

    def test_accept_value(self, get_prompt):
        prompt = get_prompt()
        assert prompt.accept('mail')
        assert prompt.question.answer == 'mail'

    def test_accept_invalid_value(self, get_prompt):
        prompt = get_prompt()
        with pytest.raises(promptmod.Error,
                           match='expected one of: work, play, mail'):
            prompt.accept('nope')


def test_select_question_carries_options(message_mock):
    message.ask_async('Pick one', usertypes.PromptMode.select,
                      lambda _answer: None, options=OPTIONS)
    question = message_mock.get_question()
    assert question.options == OPTIONS


def test_select_question_needs_options():
    with pytest.raises(ValueError, match='needs options'):
        message.ask_async('Pick one', usertypes.PromptMode.select,
                          lambda _answer: None)


def test_options_only_for_select():
    with pytest.raises(ValueError, match="Can only give 'options'"):
        message.ask_async('Sure?', usertypes.PromptMode.yesno,
                          lambda _answer: None, options=OPTIONS)
