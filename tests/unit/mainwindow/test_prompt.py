# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os

import pytest
from qutebrowser.qt.core import Qt, QTimer
from qutebrowser.qt import sip

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


class FakeModeman:

    """Records mode changes instead of needing real windows."""

    def __init__(self):
        self.entered = []
        self.left = []

    def enter(self, win_id, mode, reason=None, only_if_normal=False):
        self.entered.append((win_id, mode))

    def leave(self, win_id, mode, reason=None, *, maybe=False):
        self.left.append((win_id, mode))
        # Real ModeManager.leave() always emits `left`, and mainwindow.py's
        # wiring always forwards it to the bridge; fake that too, so a
        # single leave() call cascades exactly like it does for real,
        # including into any other window's handlers.
        message.global_bridge.mode_left.emit(mode, win_id)


class TestTargetedQuestions:

    @pytest.fixture
    def modeman_fake(self, monkeypatch):
        fake = FakeModeman()
        monkeypatch.setattr(promptmod, 'modeman', fake)
        return fake

    @pytest.fixture
    def queue(self, monkeypatch):
        """A fresh PromptQueue, registered as promptmod.prompt_queue.

        PromptQueue has no parent and isn't a QWidget, so qtbot.add_widget
        can't clean it up; without an explicit disconnect here it (and its
        win_id bookkeeping) outlives the test and can answer a *later*
        test's mode_left signals too, since message.global_bridge is a
        session-wide singleton.
        """
        queue = promptmod.PromptQueue()
        monkeypatch.setattr(promptmod, 'prompt_queue', queue)
        yield queue
        message.global_bridge.mode_left.disconnect(queue._on_mode_left)

    @pytest.fixture
    def setup(self, qtbot, queue, config_stub, key_config_stub,
              modeman_fake):
        """Get the prompt queue and containers for windows 0 and 1.

        Deleted synchronously (sip.delete, not qtbot.add_widget) at
        teardown rather than left to deleteLater(): a container that's
        still _in_prompt_mode when its test ends (most of these tests
        never answer/cancel their question) stays connected to
        prompt_done - a session-wide signal - until Qt gets around to the
        deferred delete, and can otherwise react to a *later* test's
        prompt_accept() with its own now-stale win_id.
        """
        config_stub.val.bindings.default = {}
        containers = []
        for win_id in [0, 1]:
            container = promptmod.PromptContainer(win_id)
            containers.append(container)
        yield queue, containers
        for container in containers:
            sip.delete(container)

    def _question(self, win_id):
        question = usertypes.Question()
        question.title = 'Pick one'
        question.mode = usertypes.PromptMode.select
        question.options = OPTIONS
        question.win_id = win_id
        return question

    def _ask_with_timeout(self, queue, question, timeout=2000):
        """Ask `question` blocking, with a hard timeout.

        So a regression that reintroduces a hang fails this test instead of
        blocking the rest of the suite. The QTimer is explicitly stopped
        again once ask_question() returns, so it can never fire later and
        touch a question (or anything it references) from a since-finished
        test.
        """
        safety_timer = QTimer()
        safety_timer.setSingleShot(True)
        safety_timer.timeout.connect(question.abort)
        safety_timer.start(timeout)
        try:
            return queue.ask_question(question, blocking=True)
        finally:
            safety_timer.stop()

    def test_asking_logs_win_id(self, setup, modeman_fake, caplog):
        """The e2e "prompt window" steps (conftest._prompt_window_id) parse
        win_id out of this "Asking question" log line, since Question's own
        repr doesn't carry it (matching upstream's). Losing it here would
        silently break every targeted-question e2e step.
        """
        queue, (_first, _second) = setup
        with caplog.at_level(logging.DEBUG, 'prompt'):
            queue.ask_question(self._question(1), blocking=False)
        [line] = [r.message for r in caplog.records
                 if r.message.startswith('Asking question')]
        assert 'win_id=1' in line

    def test_shown_only_in_its_window(self, setup, modeman_fake):
        queue, (first, second) = setup
        queue.ask_question(self._question(1), blocking=False)
        assert first._prompt is None
        assert isinstance(second._prompt, promptmod.SelectPrompt)
        assert modeman_fake.entered == [(1, usertypes.KeyMode.prompt)]

    def test_untargeted_shown_everywhere(self, setup, modeman_fake):
        queue, (first, second) = setup
        queue.ask_question(self._question(None), blocking=False)
        assert isinstance(first._prompt, promptmod.SelectPrompt)
        assert isinstance(second._prompt, promptmod.SelectPrompt)
        assert modeman_fake.entered == [(0, usertypes.KeyMode.prompt),
                                        (1, usertypes.KeyMode.prompt)]

    def test_other_window_ignores_answer(self, setup, modeman_fake):
        queue, (_first, second) = setup
        question = self._question(1)
        queue.ask_question(question, blocking=False)
        second.prompt_accept('play')
        assert question.answer == 'play'
        assert modeman_fake.left == [(1, usertypes.KeyMode.prompt)]

    def test_other_window_ignores_mode_left(self, qtbot, setup,
                                            modeman_fake):
        queue, (_first, second) = setup
        question = self._question(1)
        queue.ask_question(question, blocking=False)
        with qtbot.wait_signal(question.cancelled):
            message.global_bridge.mode_left.emit(usertypes.KeyMode.prompt, 1)
        assert modeman_fake.left == [(1, usertypes.KeyMode.prompt)]
        assert second._prompt is None

    def test_unrelated_leave_does_not_cancel_targeted_question(
            self, setup, modeman_fake):
        """A leave in a window that isn't showing the question is noise."""
        queue, (_first, second) = setup
        question = self._question(1)
        queue.ask_question(question, blocking=False)
        message.global_bridge.mode_left.emit(usertypes.KeyMode.prompt, 0)
        assert not question.is_aborted
        assert question.answer is None
        assert isinstance(second._prompt, promptmod.SelectPrompt)

    def test_blocking_targeted_question_survives_other_window_leaving(
            self, qtbot, setup, modeman_fake):
        """Reproduces the picker/close-prompt scenario from review.

        An untargeted question is showing in both windows, then a blocking
        question targeted at window 1 interrupts it. Window 0 has nothing
        of its own to show any more and really leaves prompt mode as a
        result (containers are connected in creation order, window 0
        before window 1, so window 0's own _on_show_prompts - and the real
        leave it triggers - runs before window 1 has even shown `targeted`
        yet). That leave must not be mistaken for window 1's question
        being left unanswered, no matter that window 1 hasn't caught up.
        """
        queue, (first, second) = setup
        untargeted = self._question(None)
        queue.ask_question(untargeted, blocking=False)
        assert isinstance(first._prompt, promptmod.SelectPrompt)
        assert isinstance(second._prompt, promptmod.SelectPrompt)

        targeted = self._question(1)
        observed_question = []

        def _during_blocking_wait():
            # By the time this runs, ask_question() has already
            # synchronously delivered show_prompts(targeted) to both
            # containers - including window 0's real, cascading leave.
            observed_question.append(queue._question)
            second.prompt_accept('play')

        QTimer.singleShot(0, _during_blocking_wait)
        answer = self._ask_with_timeout(queue, targeted)

        assert observed_question == [targeted]
        assert answer == 'play'
        assert not targeted.is_aborted
        # The interrupted question is restored to both windows.
        assert isinstance(first._prompt, promptmod.SelectPrompt)
        assert first._prompt.question is untargeted
        assert isinstance(second._prompt, promptmod.SelectPrompt)
        assert second._prompt.question is untargeted

    def test_blocking_targeted_question_survives_when_shown_first(
            self, queue, config_stub, key_config_stub, modeman_fake):
        """Same scenario, with the target window's container created (and
        thus connected) first - the fix must not depend on connection
        order either way.
        """
        config_stub.val.bindings.default = {}
        second = promptmod.PromptContainer(1)
        first = promptmod.PromptContainer(0)
        try:
            untargeted = self._question(None)
            queue.ask_question(untargeted, blocking=False)
            assert isinstance(first._prompt, promptmod.SelectPrompt)
            assert isinstance(second._prompt, promptmod.SelectPrompt)

            targeted = self._question(1)
            observed_question = []

            def _during_blocking_wait():
                observed_question.append(queue._question)
                second.prompt_accept('play')

            QTimer.singleShot(0, _during_blocking_wait)
            answer = self._ask_with_timeout(queue, targeted)

            assert observed_question == [targeted]
            assert answer == 'play'
            assert not targeted.is_aborted
        finally:
            # See setup's docstring: both containers are still showing the
            # restored `untargeted` at this point (or would still be
            # showing `targeted` on a failure/hang), so leaving this to
            # deleteLater() would leak a live prompt_done listener.
            sip.delete(first)
            sip.delete(second)

    def test_targeted_question_aborted_when_its_window_closes(
            self, qtbot, queue, config_stub, key_config_stub,
            modeman_fake):
        config_stub.val.bindings.default = {}
        first = promptmod.PromptContainer(0)
        # Neither is qtbot.add_widget()-registered (see setup's docstring);
        # `second` is destroyed by the test itself below.
        second = promptmod.PromptContainer(1)
        try:
            targeted = self._question(1)
            queue.ask_question(targeted, blocking=False)
            assert isinstance(second._prompt, promptmod.SelectPrompt)

            queued = self._question(None)
            queue.ask_question(queued, blocking=False)

            second.deleteLater()
            qtbot.waitUntil(lambda: targeted.is_aborted, timeout=1000)
            qtbot.waitUntil(
                lambda: isinstance(first._prompt, promptmod.SelectPrompt),
                timeout=1000)
            assert first._prompt.question is queued
        finally:
            sip.delete(first)


def test_ask_async_targets_window(message_mock):
    message.ask_async('Pick one', usertypes.PromptMode.select,
                      lambda _answer: None, options=OPTIONS, win_id=3)
    assert message_mock.get_question().win_id == 3
