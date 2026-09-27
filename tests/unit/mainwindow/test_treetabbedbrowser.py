# SPDX-FileCopyrightText: Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import functools

import pytest
from qutebrowser.qt.core import QUrl

from qutebrowser.config.configtypes import NewTabPosition, NewChildPosition
from qutebrowser.misc.notree import Node
from qutebrowser.mainwindow import (tabbedbrowser, treetabbedbrowser,
                                    treetabwidget)

from tests.unit.misc.test_notree import str_to_tree


@pytest.fixture
def mock_browser(mocker):
    # Mock browser used as `self` below because we are actually testing mostly
    # standalone functionality apart from the tab stack related counters.
    # Which are also only defined in __init__, not on the class, so mock
    # doesn't see them. Hence specifying them manually here.
    browser = mocker.Mock(
        spec=treetabbedbrowser.TreeTabbedBrowser,
        widget=mocker.Mock(spec=treetabwidget.TreeTabWidget),
        _tree_tab_child_rel_idx=0,
        _tree_tab_sibling_rel_idx=0,
        _tree_tab_toplevel_rel_idx=0,
    )

    # Sad little workaround to create a bound method on a mock, because
    # _position_tab calls a method on self but we are using a mock as self to
    # avoid initializing the whole tabbed browser class.
    def reset_passthrough():
        return treetabbedbrowser.TreeTabbedBrowser._reset_stack_counters(
            browser
        )
    browser._reset_stack_counters = reset_passthrough

    return browser


class TestPositionTab:
    """Test TreeTabbedBrowser._position_tab()."""

    @pytest.mark.parametrize(
        "     relation, cur_node, pos, expected", [
            ("sibling", "three", "first", "one",),
            ("sibling", "three", "prev", "two",),
            ("sibling", "three", "next", "three",),
            ("sibling", "three", "last", "six",),
            ("sibling", "one", "first", "root",),
            ("sibling", "one", "prev", "root",),
            ("sibling", "one", "next", "one",),
            ("sibling", "one", "last", "seven",),

            ("related", "one", "first", "one",),
            ("related", "one", "last", "six",),
            ("related", "two", "first", "two",),
            ("related", "two", "last", "two",),

            (None, "five", "first", "root",),
            (None, "five", "prev", "root",),
            (None, "five", "next", "one",),
            (None, "five", "last", "seven",),
            (None, "seven", "prev", "one",),
            (None, "seven", "next", "seven",),
        ]
    )
    def test_position_tab(
        self,
        config_stub,
        mock_browser,
        # parameterized
        relation,
        cur_node,
        pos,
        expected,
    ):
        """Test tree tab positioning.

        How to use the parameters above:
        * refer to the tree structure being passed to str_to_tree() below, that's
          our starting state
        * specify how the new node should be related to the current one
        * specify cur_node by value, which is the tab currently focused when the
          new tab is opened and the one the "sibling" and "related" arguments
          refer to
        * set "pos" which is the position of the new node in the list of
          siblings it's going to end up in. It should be one of first, list, prev,
          next (except the "related" relation doesn't support prev and next)
        * specify the expected preceding node (the preceding sibling if there is
          one, otherwise the parent) after the new node is positioned, "root" is
          a valid value for this

        Having the expectation being the preceding tab (sibling or parent) is
        a bit limited, in particular if the new tab somehow ends up as a child
        instead of the next sibling you wouldn't be able to tell those
        situations apart. But I went this route to avoid having to specify
        multiple trees in the parameters.
        """
        root = str_to_tree(
            """
            - one
              - two
              - three
              - four
                - five
              - six
            - seven
            """,
        )[0]
        new_node = Node("new", parent=root)

        config_stub.val.tabs.new_position.stacking = False
        self.call_position_tab(
            mock_browser,
            root,
            cur_node,
            new_node,
            pos,
            relation,
        )

        preceding_node = None
        if new_node.parent.children[0] == new_node:
            preceding_node = new_node.parent
        else:
            for n in new_node.parent.children:
                if n.value == "new":
                    break
                preceding_node = n
            else:
                pytest.fail("new tab not found")

        assert preceding_node.value == expected

    def call_position_tab(
        self,
        mock_browser,
        root,
        cur_node,
        new_node,
        pos,
        relation,
        background=False,
    ):
        sibling = related = False
        if relation == "sibling":
            sibling = True
        elif relation == "related":
            related = True
        elif relation == "background":
            background = True
        elif relation is not None:
            pytest.fail(
                "Valid values for relation are: "
                "sibling, related, background, None"
            )

        # This relation -> parent mapping is copied from
        # TreeTabbedBrowser.tabopen().
        cur_node = next(n for n in root.traverse() if n.value == cur_node)
        assert not (related and sibling)
        if related:
            parent = cur_node
            NewChildPosition().from_str(pos)
        elif sibling:
            parent = cur_node.parent
            NewTabPosition().from_str(pos)
        else:
            parent = root
            NewTabPosition().from_str(pos)

        treetabbedbrowser.TreeTabbedBrowser._position_tab(
            mock_browser,
            cur_node=cur_node,
            new_node=new_node,
            pos=pos,
            parent=parent,
            sibling=sibling,
            related=related,
            background=background,
        )

    @pytest.mark.parametrize(
        "     test_tree, relation, pos, expected", [
            ("tree_one", "sibling", "next", "one,two,new1,new2,new3",),
            ("tree_one", "sibling", "prev", "one,new3,new2,new1,two",),
            ("tree_one", None, "next", "one,two,new1,new2,new3",),
            ("tree_one", None, "prev", "new3,new2,new1,one,two",),
            ("tree_one", "related", "first", "one,two,new1,new2,new3",),
            ("tree_one", "related", "last", "one,two,new1,new2,new3",),
        ]
    )
    def test_position_tab_stacking(
        self,
        config_stub,
        mock_browser,
        # parameterized
        test_tree,
        relation,
        pos,
        expected,
    ):
        """Test tree tab positioning with tab stacking enabled.

        With tab stacking enabled the first background tab should be opened
        beside the current one, successive background tabs should be opened on
        the other side of prior opened tabs, not beside the current tab.
        This test covers what is currently implemented, I'm not sure all the
        desired behavior is implemented currently though.
        """
        # Simpler tree here to make the assert string a bit simpler.
        # Tab "two" is hardcoded as cur_tab.
        root = str_to_tree(
            """
            - one
              - two
            """,
        )[0]
        config_stub.val.tabs.new_position.stacking = True

        for val in ["new1", "new2", "new3"]:
            new_node = Node(val, parent=root)

            self.call_position_tab(
                mock_browser,
                root,
                "two",
                new_node,
                pos,
                relation,
                background=True,
            )

        actual = ",".join([n.value for n in root.traverse()])
        actual = actual[len("root,"):]
        assert actual == expected


class TestHiddenTabs:
    """Test how tabs hidden under collapsed tabs are labelled and revealed."""

    @pytest.fixture
    def tree(self, mocker, mock_browser):
        """A tree where two is collapsed, hiding three and four.

        - one
          - two (collapsed)
            - three
              - four
        - five
        """
        root = Node(None)
        mock_browser.widget.tree_root = root
        nodes = {}
        for name, parent in [('one', root), ('two', 'one'), ('three', 'two'),
                             ('four', 'three'), ('five', root)]:
            tab = mocker.Mock(name=name)
            if isinstance(parent, str):
                parent = nodes[parent]
            nodes[name] = tab.node = Node(tab, parent=parent)
        nodes['two'].collapsed = True
        in_tab_bar = ['one', 'two', 'five']
        mock_browser.widget.indexOf.side_effect = lambda tab: next(
            (idx for idx, name in enumerate(in_tab_bar)
             if nodes[name].value is tab), -1)
        return nodes

    def test_tab_labels(self, mock_browser, tree):
        labels = treetabbedbrowser.TreeTabbedBrowser.tab_labels(mock_browser)
        assert labels == [
            ('1', tree['one'].value),
            ('2', tree['two'].value),
            ('2.1', tree['three'].value),
            ('2.2', tree['four'].value),
            ('3', tree['five'].value),
        ]

    def test_reveal_hidden_tab(self, mock_browser, tree):
        treetabbedbrowser.TreeTabbedBrowser.reveal_tab(
            mock_browser, tree['four'].value)
        assert not any(node.collapsed for node in tree.values())
        mock_browser.widget.tree_tab_update.assert_called_once_with()

    def test_reveal_shown_tab(self, mock_browser, tree):
        treetabbedbrowser.TreeTabbedBrowser.reveal_tab(
            mock_browser, tree['two'].value)
        assert tree['two'].collapsed
        mock_browser.widget.tree_tab_update.assert_not_called()


class TestRemoveTab:
    """Test TreeTabbedBrowser._remove_tab() with tabs outside the tab bar."""

    def test_already_removed(self, mocker, mock_browser):
        """A tab its page closes twice has left the tree (#8414)."""
        tab = mocker.Mock()
        tab.node = Node(tab)
        mock_browser.widget.indexOf.return_value = -1
        with pytest.raises(tabbedbrowser.TabDeletedError):
            treetabbedbrowser.TreeTabbedBrowser._remove_tab(mock_browser, tab)
        mock_browser._add_undo_entry.assert_not_called()

    def test_hidden(self, mocker, mock_browser):
        """A tab hidden under a collapsed tab is removed from the tree."""
        root = Node(None)
        mock_browser.widget.tree_root = root
        tabs = {name: mocker.Mock(name=name, pending_removal=False)
                for name in ['one', 'two', 'three']}
        one = tabs['one'].node = Node(tabs['one'], parent=root)
        two = tabs['two'].node = Node(tabs['two'], parent=one)
        three = tabs['three'].node = Node(tabs['three'], parent=two)
        one.collapsed = True
        mock_browser.widget.indexOf.side_effect = (
            lambda tab: 0 if tab is tabs['one'] else -1)

        treetabbedbrowser.TreeTabbedBrowser._remove_tab(
            mock_browser, tabs['two'], add_undo=False)

        assert two.parent is None
        assert three.parent is one
        assert tabs['two'].pending_removal
        tabs['two'].private_api.shutdown.assert_called_once_with()
        tabs['two'].deleteLater.assert_called_once_with()
        mock_browser.widget.tree_tab_update.assert_called_once_with()


class TestUndoEntry:
    """Test _TreeUndoEntry.restore_into_tab() when the tree has changed."""

    @pytest.fixture
    def restore(self, mocker):
        def restore(root, **fields):
            entry = treetabbedbrowser._TreeUndoEntry(
                url=QUrl('https://example.org/'), history=b'', index=0,
                pinned=False, local_index=0, **fields)
            tab = mocker.Mock()
            tab.node = Node(tab, parent=root)
            entry.restore_into_tab(tab)
            return tab.node
        return restore

    def test_taken_uid(self, restore):
        """A uid already in the tree isn't used twice (#8274)."""
        root = Node(None)
        taken = Node('taken', parent=root)
        node = restore(root, uid=taken.uid, parent_node_uid=root.uid,
                       children_node_uids=[])
        assert node.uid != taken.uid
        assert root.get_descendent_by_uid(taken.uid) is taken

    def test_missing_nodes(self, restore):
        root = Node(None)
        node = restore(root, uid=-1, parent_node_uid=-2,
                       children_node_uids=[-3])
        assert node.parent is root
        assert node.children == ()

    def test_child_became_ancestor(self, restore):
        """A former child now above the old parent stays where it is.

        - child
          - parent  <- the closed tab's parent, moved under its former child
        """
        root = Node(None)
        child = Node('child', parent=root)
        parent = Node('parent', parent=child)
        node = restore(root, uid=-1, parent_node_uid=parent.uid,
                       children_node_uids=[child.uid])
        assert node.parent is parent
        assert child.parent is root
        assert node.children == ()
        assert len(list(root.traverse())) == 4


def test_revealed_tab_gets_favicon_setting(mocker):
    """A tab revealed from a collapsed tab follows tabs.favicons.show."""
    widget = mocker.Mock(spec=treetabwidget.TreeTabWidget)
    root = Node(None)
    widget.tree_root = root
    parent_tab, tab = mocker.Mock(), mocker.Mock()
    parent_tab.node = Node(parent_tab, parent=root)
    tab.node = Node(tab, parent=parent_tab.node)
    widget.indexOf.side_effect = lambda t: 0 if t is parent_tab else -1

    treetabwidget.TreeTabWidget.update_tree_tab_visibility(widget)

    widget.insertTab.assert_called_once()
    widget.update_tab_favicon.assert_called_once_with(tab)


def test_drag_several_places_at_once(mocker):
    """A drag that moves a tab two places in one tabMoved signal."""
    widget = mocker.Mock(spec=treetabwidget.TreeTabWidget)
    root = Node(None)
    widget.tree_root = root
    widget._recursion_guard = False
    widget._move_tree_node = functools.partial(
        treetabwidget.TreeTabWidget._move_tree_node, widget)
    tabs = {}
    for name in ['one', 'two', 'three']:
        tabs[name] = mocker.Mock(name=name)
        tabs[name].node = Node(tabs[name], parent=root)
    tab_bar = [tabs['two'], tabs['three'], tabs['one']]
    widget._tab_by_idx.side_effect = tab_bar.__getitem__
    widget.tabBar.return_value.drag_in_progress = True

    # QTabBar passes the indices backwards while dragging.
    treetabwidget.TreeTabWidget.on_tab_moved(widget, 2, 0)

    assert [node.value for node in root.children] == tab_bar
    assert not widget._recursion_guard
    widget.tree_tab_update.assert_called_once_with()
