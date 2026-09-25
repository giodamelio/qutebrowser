Feature: Containers
    Sessions on different containers keep their cookies apart.

    Background:
        Given I run :debug-close-other-sessions
        And I set url.start_pages to ["about:blank"]
        And I clean up open tabs

    Scenario: Cookies stay inside their container
        When I run :container-new iso-a
        And I run :session-new iso-session --container iso-a
        And I open cookies/set?qute-container-test=42 without waiting
        And I wait until cookies is loaded
        And I run :session-close iso-session
        And I open cookies
        Then the cookie qute-container-test should not be set

    Scenario: Sessions on the same container share cookies
        When I run :container-new share-a
        And I run :session-new share-one --container share-a
        And I open cookies/set?qute-share-test=1 without waiting
        And I wait until cookies is loaded
        And I run :session-new share-two --container share-a
        And I open cookies
        Then the cookie qute-share-test should be set to 1

    Scenario: Reopening a session on a container right after closing it
        When I run :container-new reopen-a
        And I run :session-new reopen-session --container reopen-a
        And I open cookies/set-custom?max_age=300 without waiting
        And I wait until cookies is loaded
        And I run :session-close reopen-session
        And I run :session-open reopen-session
        And I open cookies
        Then the cookie cookie should be set to value

    Scenario: Creating a session on an unknown container
        When I run :session-new nope-session --container nope
        Then the error "Unknown container 'nope', create it with :container-new nope" should be shown

    Scenario: Deleting a container a session uses
        When I run :container-new used-a
        And I run :session-new used-session --container used-a
        And I run :container-delete used-a
        Then the error "Container used-a is used by sessions: used-session" should be shown

    Scenario: Deleting an unused container
        When I run :container-new unused-a
        And I run :container-delete unused-a
        And I run :session-new unused-session --container unused-a
        Then the error "Unknown container 'unused-a', create it with :container-new unused-a" should be shown

    Scenario: Renaming a container rewrites its sessions
        When I run :container-new old-a
        And I run :session-new rename-session --container old-a
        And I run :session-close rename-session
        And I wait for "Deleting profile 'old-a'" in the log
        And I run :container-rename old-a new-a
        Then the session file rename-session should contain "container: new-a"

    Scenario: Removing a declared container that a session uses
        When I set containers to {"decl-a": {"color": "red"}}
        And I run :session-new decl-session --container decl-a
        And I run :session-close decl-session
        And I set containers to {}
        Then the message "Keeping container decl-a as a runtime container, because sessions use it: decl-session" should be shown

    Scenario: Closing a container window adds nothing to window undo
        # Setting the size to 0 empties the window undo stack, so
        # windows closed by earlier scenarios can't be undone here.
        When I set tabs.undo_stack_size to 0
        And I set tabs.undo_stack_size to 100
        And I run :container-new undo-a
        And I run :session-new undo-session --container undo-a
        And I run :close
        And I wait for "removed: main-window" in the log
        And I run :undo --window
        Then the error "Nothing to undo" should be shown
