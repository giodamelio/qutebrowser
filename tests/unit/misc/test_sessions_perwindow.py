# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for per-window session management in qutebrowser.misc.sessions."""

import os

import pytest
import yaml

from qutebrowser.misc import sessions
from qutebrowser.utils import objreg, qtutils, utils


@pytest.fixture
def sess_man(tmp_path):
    """Fixture providing a SessionManager."""
    return sessions.SessionManager(base_path=str(tmp_path))


class FakeMainWindow:
    """Fake MainWindow for testing session filtering."""

    def __init__(self, win_id, session_name='default', private=False):
        self.win_id = win_id
        self.session_name = session_name
        self._deleted = False
        self.registry = objreg.ObjectRegistry()

    def saveGeometry(self):
        return b'fake-geometry'


class FakeTabbedBrowser:
    """Fake TabbedBrowser for testing."""

    def __init__(self, private=False):
        self.is_private = private
        self.widget = FakeWidget()

    def widgets(self):
        return []


class FakeWidget:
    """Fake widget with currentIndex."""

    def currentIndex(self):
        return 0


class FakeApp:
    """Fake QApplication."""

    def activeWindow(self):
        return None


@pytest.fixture
def fake_windows(monkeypatch):
    """Set up fake windows in objreg for testing."""
    windows = {}

    def make_window(win_id, session_name=None, private=False):
        win = FakeMainWindow(win_id, session_name=session_name, private=private)
        tb = FakeTabbedBrowser(private=private)
        objreg.window_registry[win_id] = win
        objreg.register('main-window', win, scope='window', window=win_id)
        objreg.register('tabbed-browser', tb, scope='window', window=win_id)
        windows[win_id] = (win, tb)
        return win

    # Patch sip.isdeleted to always return False for our fakes
    monkeypatch.setattr('qutebrowser.misc.sessions.sip.isdeleted',
                        lambda obj: getattr(obj, '_deleted', False))

    # Patch objects.qapp
    monkeypatch.setattr('qutebrowser.misc.sessions.objects.qapp', FakeApp())

    yield make_window

    # Cleanup
    for win_id in list(windows.keys()):
        try:
            objreg.delete('main-window', scope='window', window=win_id)
        except KeyError:
            pass
        try:
            objreg.delete('tabbed-browser', scope='window', window=win_id)
        except KeyError:
            pass
        try:
            del objreg.window_registry[win_id]
        except KeyError:
            pass


class TestSessionManagerNoCurrentAttr:
    """Test that SessionManager no longer has a 'current' attribute."""

    def test_no_current(self, sess_man):
        assert not hasattr(sess_man, 'current')


class TestSaveAllSessionFiltering:
    """Test _save_all with session_name filtering."""

    def test_no_filter(self, sess_man, fake_windows):
        """_save_all with no filter saves all windows."""
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='personal')
        fake_windows(2, session_name='default')

        data = sess_man._save_all()
        assert len(data['windows']) == 3

    def test_filter_by_session_name(self, sess_man, fake_windows):
        """_save_all with session_name='work' saves only work windows."""
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='personal')
        fake_windows(2, session_name='work')

        data = sess_man._save_all(session_name='work')
        assert len(data['windows']) == 2
        for win in data['windows']:
            assert win['session'] == 'work'

    def test_filter_default_session(self, sess_man, fake_windows):
        """_save_all with session_name='default' saves only default windows."""
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='default')
        fake_windows(2, session_name='default')

        data = sess_man._save_all(session_name='default')
        assert len(data['windows']) == 2
        for win in data['windows']:
            assert win['session'] == 'default'

    def test_session_key_in_yaml(self, sess_man, fake_windows):
        """Windows with session_name get 'session' key in YAML data."""
        fake_windows(0, session_name='myproject')

        data = sess_man._save_all()
        assert data['windows'][0]['session'] == 'myproject'


    def test_empty_result_when_no_match(self, sess_man, fake_windows):
        """_save_all returns empty windows list when no session matches."""
        fake_windows(0, session_name='work')

        data = sess_man._save_all(session_name='personal')
        assert len(data['windows']) == 0

    def test_private_windows_excluded(self, sess_man, fake_windows):
        """Private windows excluded by default even when session matches."""
        fake_windows(0, session_name='work', private=True)
        fake_windows(1, session_name='work', private=False)

        data = sess_man._save_all(session_name='work')
        assert len(data['windows']) == 1

    def test_private_windows_included_with_flag(self, sess_man, fake_windows):
        """Private windows included when with_private=True."""
        fake_windows(0, session_name='work', private=True)
        fake_windows(1, session_name='work', private=False)

        data = sess_man._save_all(session_name='work', with_private=True)
        assert len(data['windows']) == 2


class TestGetSessionName:
    """Test _get_session_name with per-window sessions."""

    def test_explicit_name(self, sess_man):
        """Explicit name is returned as-is."""
        assert sess_man._get_session_name('work') == 'work'

    def test_default_sentinel_with_config(self, sess_man, config_stub):
        """Default sentinel uses config.val.session.default_name."""
        config_stub.val.session.default_name = 'configured'
        assert sess_man._get_session_name(sessions.default) == 'configured'

    def test_default_sentinel_with_window(self, sess_man, fake_windows,
                                          config_stub):
        """Default sentinel falls back to window's session_name."""
        config_stub.val.session.default_name = None
        fake_windows(42, session_name='fromwindow')
        assert sess_man._get_session_name(sessions.default,
                                          win_id=42) == 'fromwindow'

    def test_default_sentinel_fallback(self, sess_man, config_stub):
        """Default sentinel falls back to 'default' when nothing is set."""
        config_stub.val.session.default_name = None
        assert sess_man._get_session_name(sessions.default) == 'default'


class TestSavePerSession:
    """Test save() with session_name filter."""

    def test_save_with_session_filter(self, sess_man, fake_windows, tmp_path):
        """save() with session_name only includes matching windows."""
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='personal')

        sess_man.save('work', session_name='work')

        path = tmp_path / 'work.yml'
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert len(data['windows']) == 1
        assert data['windows'][0]['session'] == 'work'

    def test_save_default_to_file(self, sess_man, fake_windows, tmp_path):
        """save() can save default windows to a file."""
        fake_windows(0, session_name='default')
        fake_windows(1, session_name='work')

        sess_man.save('default', session_name='default')

        path = tmp_path / 'default.yml'
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert len(data['windows']) == 1
        assert data['windows'][0]['session'] == 'default'


class TestSaveAllSessions:
    """Test save_all_sessions() method."""

    def test_save_all_from_live_windows(self, sess_man, fake_windows, tmp_path):
        """save_all_sessions() saves each session and untagged windows."""
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='personal')
        fake_windows(2, session_name='default')

        sess_man.save_all_sessions()

        assert (tmp_path / 'work.yml').exists()
        assert (tmp_path / 'personal.yml').exists()
        assert (tmp_path / 'default.yml').exists()

    def test_save_all_from_last_window_data(self, sess_man, tmp_path):
        """save_all_sessions(last_window=True) splits by session key."""
        sess_man._last_window_session = {
            'windows': [
                {'geometry': b'g1', 'tabs': [], 'session': 'work'},
                {'geometry': b'g2', 'tabs': [], 'session': 'work'},
                {'geometry': b'g3', 'tabs': [], 'session': 'personal'},
                {'geometry': b'g4', 'tabs': [], 'session': 'default'},
            ]
        }

        sess_man.save_all_sessions(last_window=True)

        # Check work.yml
        with open(tmp_path / 'work.yml') as f:
            data = yaml.safe_load(f)
        assert len(data['windows']) == 2

        # Check personal.yml
        with open(tmp_path / 'personal.yml') as f:
            data = yaml.safe_load(f)
        assert len(data['windows']) == 1

        # Check default.yml
        with open(tmp_path / 'default.yml') as f:
            data = yaml.safe_load(f)
        assert len(data['windows']) == 1

    def test_save_all_load_next_time(self, sess_man, fake_windows, tmp_path,
                                     config_stub, monkeypatch):
        """save_all_sessions with load_next_time stores session list in state."""
        from qutebrowser.config import configfiles
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='personal')
        fake_windows(2, session_name='default')

        # Mock configfiles.state with a simple dict-of-dicts
        fake_state = {'general': {}}
        monkeypatch.setattr(configfiles, 'state', fake_state)

        sess_man.save_all_sessions(load_next_time=True)

        sessions_str = fake_state['general']['sessions']
        names = sorted(s.strip() for s in sessions_str.split(','))
        assert names == ['default', 'personal', 'work']


class TestSessionAssign:
    """Test the session_assign command function."""

    def test_assign_session(self, fake_windows):
        """session_assign sets session_name on the window."""
        win = fake_windows(0)
        sessions.session_assign('myproject', win_id=0)
        assert win.session_name == 'myproject'

    def test_reset_to_default(self, fake_windows):
        """session_assign with no name resets to default."""
        win = fake_windows(0, session_name='old')
        sessions.session_assign(None, win_id=0)
        assert win.session_name == 'default'

    def test_internal_name_rejected(self, fake_windows):
        """session_assign rejects names starting with underscore."""
        from qutebrowser.api import cmdutils
        fake_windows(0)
        with pytest.raises(cmdutils.CommandError):
            sessions.session_assign('_internal', win_id=0)


class TestSessionAssignThenSave:
    """Test that session_assign followed by session_save works correctly."""

    def test_assign_then_save_uses_assigned_session(self, sess_man, fake_windows,
                                                     tmp_path, config_stub,
                                                     monkeypatch):
        """session_save after session_assign saves to the assigned session."""
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        config_stub.val.session.default_name = None

        # Create window with default session
        win = fake_windows(0, session_name='default')

        # Assign to 'myproject'
        sessions.session_assign('myproject', win_id=0)
        assert win.session_name == 'myproject'

        # Now save without arguments - should save to 'myproject'
        sessions.session_save(sessions.default, win_id=0, quiet=True)

        # Verify the file was created with the right name
        assert (tmp_path / 'myproject.yml').exists()
        assert not (tmp_path / 'default.yml').exists()

    def test_assign_then_save_filters_windows(self, sess_man, fake_windows,
                                               tmp_path, config_stub,
                                               monkeypatch):
        """session_save after session_assign only saves windows with matching session."""
        import yaml
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        config_stub.val.session.default_name = None

        # Create two windows: one for 'work', one for 'personal'
        fake_windows(0, session_name='work')
        fake_windows(1, session_name='personal')

        # Run session_save from work window
        sessions.session_save(sessions.default, win_id=0, quiet=True)

        # Verify only work.yml was created
        assert (tmp_path / 'work.yml').exists()

        # And it only contains the work window
        with open(tmp_path / 'work.yml') as f:
            data = yaml.safe_load(f)
        assert len(data['windows']) == 1
        assert data['windows'][0]['session'] == 'work'

class TestSessionClose:
    """Test the session_close command function."""

    def test_close_saves_and_closes(self, sess_man, fake_windows, tmp_path,
                                    monkeypatch):
        """session_close saves the session and closes matching windows."""
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        w0 = fake_windows(0, session_name='work')
        w1 = fake_windows(1, session_name='work')
        fake_windows(2, session_name='personal')

        closed = []
        w0.close = lambda: closed.append(0)
        w1.close = lambda: closed.append(1)

        sessions.session_close('work', win_id=0)
        assert sorted(closed) == [0, 1]
        assert (tmp_path / 'work.yml').exists()

    def test_close_uses_current_window_session(self, sess_man, fake_windows,
                                                tmp_path, monkeypatch):
        """session_close without name uses current window's session."""
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        w0 = fake_windows(0, session_name='myproject')
        closed = []
        w0.close = lambda: closed.append(0)

        sessions.session_close(win_id=0)
        assert closed == [0]
        assert (tmp_path / 'myproject.yml').exists()

    def test_close_rejects_internal(self, fake_windows, monkeypatch, sess_man):
        """session_close rejects internal session names."""
        from qutebrowser.api import cmdutils
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        fake_windows(0, session_name='_internal')
        with pytest.raises(cmdutils.CommandError, match='internal session'):
            sessions.session_close('_internal', win_id=0)

    def test_close_force_skips_save(self, sess_man, fake_windows, tmp_path,
                                     monkeypatch):
        """session_close --force skips saving."""
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        w0 = fake_windows(0, session_name='work')
        closed = []
        w0.close = lambda: closed.append(0)

        sessions.session_close('work', force=True, win_id=0)
        assert closed == [0]
        assert not (tmp_path / 'work.yml').exists()

    def test_close_no_matching_windows(self, sess_man, fake_windows,
                                        monkeypatch):
        """session_close raises error when no windows match."""
        from qutebrowser.api import cmdutils
        monkeypatch.setattr(sessions, 'session_manager', sess_man)
        fake_windows(0, session_name='other')
        with pytest.raises(cmdutils.CommandError, match='No windows found'):
            sessions.session_close('work', win_id=0)

class TestLoadWindowSessionName:
    """Test that _load_window sets session_name on created windows."""

    @pytest.fixture
    def mock_mainwindow(self, monkeypatch):
        """Mock mainwindow.MainWindow to capture session_name."""
        created = []

        class MockMainWindow:
            def __init__(self, *, geometry, private, session_name='default'):
                self.win_id = len(created)
                self.session_name = session_name
                self._geometry = geometry
                self._private = private
                self.registry = objreg.ObjectRegistry()
                created.append(self)
                objreg.window_registry[self.win_id] = self
                # Register a fake tabbed browser
                tb = FakeTabbedBrowser()
                objreg.register('tabbed-browser', tb, scope='window',
                                window=self.win_id)

            def show(self):
                pass

        monkeypatch.setattr('qutebrowser.misc.sessions.mainwindow.MainWindow',
                            MockMainWindow)
        yield created

        for win in created:
            try:
                objreg.delete('tabbed-browser', scope='window',
                              window=win.win_id)
            except KeyError:
                pass
            try:
                del objreg.window_registry[win.win_id]
            except KeyError:
                pass
    def test_session_name_from_yaml(self, sess_man, mock_mainwindow):
        """Window gets session_name from YAML data."""
        win_data = {
            'geometry': b'fake',
            'tabs': [],
            'session': 'project-x',
        }
        sess_man._load_window(win_data)
        assert mock_mainwindow[0].session_name == 'project-x'

    def test_session_name_from_argument(self, sess_man, mock_mainwindow):
        """Window gets session_name from the session_name argument."""
        win_data = {
            'geometry': b'fake',
            'tabs': [],
        }
        sess_man._load_window(win_data, session_name='loaded-session')
        assert mock_mainwindow[0].session_name == 'loaded-session'

    def test_yaml_overrides_argument(self, sess_man, mock_mainwindow):
        """YAML 'session' key takes precedence over argument."""
        win_data = {
            'geometry': b'fake',
            'tabs': [],
            'session': 'from-yaml',
        }
        sess_man._load_window(win_data, session_name='from-arg')
        assert mock_mainwindow[0].session_name == 'from-yaml'

    def test_default_session_when_no_info(self, sess_man, mock_mainwindow):
        """Window gets 'default' when no session info available."""
        win_data = {
            'geometry': b'fake',
            'tabs': [],
        }
        sess_man._load_window(win_data)
        assert mock_mainwindow[0].session_name == 'default'


class TestSessionStatusBarWidget:
    """Test the Session statusbar widget."""

    def test_set_session_name(self):
        from qutebrowser.mainwindow.statusbar.session import Session
        widget = Session()
        widget.set_session_name('work')
        assert widget.text() == '[work]'

    def test_clear_session_name(self):
        from qutebrowser.mainwindow.statusbar.session import Session
        widget = Session()
        widget.set_session_name('work')
        widget.set_session_name('default')
        assert widget.text() == '[default]'

    def test_empty_string_clears(self):
        from qutebrowser.mainwindow.statusbar.session import Session
        widget = Session()
        widget.set_session_name('work')
        widget.set_session_name('')
        assert widget.text() == '[default]'
