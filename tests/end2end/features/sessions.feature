Feature: Saving and loading sessions

  Background:
    Given I run :debug-close-other-sessions
    And I clean up open tabs
    And I set url.start_pages to ["about:blank"]

  Scenario: Saving a simple session
    When I open data/hello.txt
    And I open data/title.html in a new tab
    Then the session should look like:
      """
      windows:
        - active: true
          tabs:
            - history:
              - url: about:blank
              - active: true
                url: http://localhost:*/data/hello.txt
            - active: true
              history:
              - active: true
                url: http://localhost:*/data/title.html
                title: Test title
      """

  @qtwebengine_skip
  Scenario: Zooming (qtwebkit)
    When I open data/hello.txt
    And I run :zoom 50
    Then the session should look like:
      """
      windows:
        - tabs:
          - history:
            - url: about:blank
              zoom: 1.0
            - url: http://localhost:*/data/hello.txt
              zoom: 0.5
      """

  # The zoom level is only stored for the newest element for QtWebEngine.
  @qtwebkit_skip
  Scenario: Zooming (qtwebengine)
    When I open data/hello.txt
    And I run :zoom 50
    Then the session should look like:
      """
      windows:
        - tabs:
          - history:
            - url: about:blank
            - url: http://localhost:*/data/hello.txt
              zoom: 0.5
      """

  @qtwebengine_skip
  Scenario: Scrolling (qtwebkit)
    When I open data/scroll/simple.html
    And I run :scroll-px 10 20
    Then the session should look like:
      """
      windows:
        - tabs:
          - history:
            - url: about:blank
              scroll-pos:
                x: 0
                y: 0
            - url: http://localhost:*/data/scroll/simple.html
              scroll-pos:
                x: 10
                y: 20
      """

  # The scroll position is only stored for the newest element for QtWebEngine.
  @qtwebkit_skip
  Scenario: Scrolling (qtwebengine)
    When I open data/scroll/simple.html
    And I wait for "* simple loaded" in the log
    And I run :scroll-px 10 20
    And I wait until the scroll position changed to 10/20
    Then the session should look like:
      """
      windows:
        - tabs:
          - history:
            - url: about:blank
            - url: http://localhost:*/data/scroll/simple.html
              scroll-pos:
                x: 10
                y: 20
      """
  Scenario: Redirect
    When I open redirect-to?url=data/title.html without waiting
    And I wait until data/title.html is loaded
    Then the session should look like:
      """
      windows:
        - tabs:
          - history:
            - url: about:blank
            - active: true
              url: http://localhost:*/data/title.html
              original-url: http://localhost:*/redirect-to?url=data/title.html
              title: Test title
      """

  Scenario: Valid UTF-8 data
    When I open data/sessions/snowman.html
    Then the session should look like:
      """
      windows:
      - tabs:
        - history:
          - url: about:blank
          - url: http://localhost:*/data/sessions/snowman.html
            title: snow☃man
      """

  @qtwebengine_skip
  Scenario: Long output comparison (qtwebkit)
    When I open data/numbers/1.txt
    And I open data/title.html
    And I open data/numbers/2.txt in a new tab
    And I open data/numbers/3.txt in a new window
    # Full output apart from "geometry:" and the active window (needs qutewm)
    Then the session should look like:
      """
      windows:
      - tabs:
        - history:
          - scroll-pos:
              x: 0
              y: 0
            title: about:blank
            url: about:blank
            zoom: 1.0
          - scroll-pos:
              x: 0
              y: 0
            title: http://localhost:*/data/numbers/1.txt
            url: http://localhost:*/data/numbers/1.txt
            zoom: 1.0
          - active: true
            scroll-pos:
              x: 0
              y: 0
            title: Test title
            url: http://localhost:*/data/title.html
            zoom: 1.0
        - active: true
          history:
          - active: true
            scroll-pos:
              x: 0
              y: 0
            title: ''
            url: http://localhost:*/data/numbers/2.txt
            zoom: 1.0
      - tabs:
        - active: true
          history:
          - active: true
            scroll-pos:
              x: 0
              y: 0
            title: ''
            url: http://localhost:*/data/numbers/3.txt
            zoom: 1.0
      """

  # FIXME:qtwebengine what's up with the titles there?
  @qtwebkit_skip
  Scenario: Long output comparison (qtwebengine)
    When I open data/numbers/1.txt
    And I open data/title.html
    And I open data/numbers/2.txt in a new tab
    And I open data/numbers/3.txt in a new window
    # Full output apart from "geometry:" and the active window (needs qutewm)
    Then the session should look like:
      """
      windows:
      - tabs:
        - history:
          - title: about:blank
            url: about:blank
          - title: http://localhost:*/data/numbers/1.txt
            url: http://localhost:*/data/numbers/1.txt
          - active: true
            scroll-pos:
              x: 0
              y: 0
            title: Test title
            url: http://localhost:*/data/title.html
            zoom: 1.0
        - active: true
          history:
          - active: true
            scroll-pos:
              x: 0
              y: 0
            title: localhost:*/data/numbers/2.txt
            url: http://localhost:*/data/numbers/2.txt
            zoom: 1.0
      - tabs:
        - active: true
          history:
          - active: true
            scroll-pos:
              x: 0
              y: 0
            title: localhost:*/data/numbers/3.txt
            url: http://localhost:*/data/numbers/3.txt
            zoom: 1.0
      """

  # https://github.com/qutebrowser/qutebrowser/issues/879

  Scenario: Saving a session with a page using history.replaceState()
    When I open data/sessions/history_replace_state.html without waiting
    Then the javascript message "Called history.replaceState" should be logged
    And the session should look like:
      """
      windows:
      - tabs:
        - history:
          - url: about:blank
          - active: true
            url: http://localhost:*/data/sessions/history_replace_state.html?state=2
            title: Test title
      """

  @qtwebengine_skip
  Scenario: Saving a session with a page using history.replaceState() and navigating away (qtwebkit)
    When I open data/sessions/history_replace_state.html
    And I open data/hello.txt
    Then the javascript message "Called history.replaceState" should be logged
    And the session should look like:
      """
      windows:
      - tabs:
        - history:
          - url: about:blank
          - url: http://localhost:*/data/sessions/history_replace_state.html?state=2
            # What we'd *really* expect here is "Test title", but that
            # workaround is the best we can do.
            title: http://localhost:*/data/sessions/history_replace_state.html?state=2
          - active: true
            url: http://localhost:*/data/hello.txt
      """

  # Seems like that bug is fixed upstream in QtWebEngine
  @skip  # Too flaky
  Scenario: Saving a session with a page using history.replaceState() and navigating away
    When I open data/sessions/history_replace_state.html without waiting
    And I wait for "* Called history.replaceState" in the log
    And I open data/hello.txt
    Then the session should look like:
      """
      windows:
      - tabs:
        - history:
          - url: about:blank
          - url: http://localhost:*/data/sessions/history_replace_state.html?state=2
            title: Test title
          - active: true
            url: http://localhost:*/data/hello.txt
      """

  # :session-* commands

  Scenario: Creating a session opens it in a new window
    When I run :session-new work-new
    And I wait for "Saved session work-new" in the log
    Then the session work-new should exist
    And the session should look like:
      """
      windows:
      - session: default
      - session: work-new
        tabs:
        - history:
          - url: about:blank
      """

  Scenario: Creating a session named default
    When I run :session-new default
    Then the error "'default' is reserved" should be shown

  Scenario: Creating a session with an invalid name
    When I run :session-new Work
    Then the error "Invalid name 'Work': use lowercase letters, digits, '_' and '-', starting with a letter or digit" should be shown

  Scenario: Creating a session without a name
    When I run :session-new
    Then the error "Give a session name, or use --private" should be shown

  Scenario: Creating a private session
    When I run :session-new --private
    Then the session should look like:
      """
      windows:
      - session: default
      - session: private-*
        private: true
      """

  Scenario: Combining --private with a name
    When I run :session-new --private work-private
    Then the error "--private can't be combined with a name or --container" should be shown

  Scenario: Closing and reopening a session restores its windows
    When I run :session-new work-reopen
    And I open data/numbers/1.txt
    And I run :session-close work-reopen
    And I wait for "Saved session work-reopen" in the log
    And I run :session-open work-reopen
    And I wait until data/numbers/1.txt is loaded
    Then the session should look like:
      """
      windows:
      - session: default
      - session: work-reopen
        tabs:
        - history:
          - url: http://localhost:*/data/numbers/1.txt
      """

  Scenario: Opening an unknown session
    When I run :session-open inexistent-session
    Then the error "Session inexistent-session not found!" should be shown

  Scenario: Deleting an open session
    When I run :session-new work-delete-open
    And I run :session-delete work-delete-open
    Then the error "Session work-delete-open is open, close it first" should be shown

  Scenario: Deleting a closed session
    When I run :session-new work-delete
    And I run :session-close work-delete
    And I run :session-delete work-delete
    Then the session work-delete should not exist

  Scenario: Deleting the default session
    When I run :session-delete default
    Then the error "Session default can't be deleted" should be shown

  Scenario: Renaming a session
    When I run :session-new work-old
    And I run :session-rename work-old work-renamed
    Then the session work-renamed should exist
    And the session work-old should not exist

  Scenario: Moving a window into another session
    When I run :session-new work-move
    And I open data/numbers/2.txt
    And I run :session-move-window default
    Then the session should look like:
      """
      windows:
      - session: default
      - session: default
      """
    And the session file work-move should not contain "numbers/2.txt"

  Scenario: Moving a private window
    When I open about:blank in a private window
    And I run :session-move-window default
    Then the error "Can't move windows into or out of private sessions" should be shown

  Scenario: Pinned tabs survive closing and reopening a session
    When I run :session-new work-pin
    And I open data/numbers/1.txt
    And I open data/numbers/2.txt in a new tab
    And I open data/numbers/3.txt in a new tab
    And I run :tab-pin with count 2
    And I run :session-close work-pin
    And I wait for "Saved session work-pin" in the log
    And I run :session-open work-pin
    And I wait until data/numbers/3.txt is loaded
    Then the session should look like:
      """
      windows:
      - session: default
      - session: work-pin
        tabs:
        - history:
          - url: http://localhost:*/data/numbers/1.txt
        - history:
          - url: http://localhost:*/data/numbers/2.txt
            pinned: true
        - history:
          - url: http://localhost:*/data/numbers/3.txt
            pinned: false
      """


  # Moving tabs between profiles

  Scenario: Giving a tab to a window on another container
    When I run :container-new give-a
    And I run :session-new give-session --container give-a
    And I open data/numbers/1.txt
    And I open data/numbers/2.txt in a new tab
    And I give the current tab to the window of session default
    Then the error "Can't move tabs from session give-session (container give-a) to session default (container default)" should be shown

  Scenario: Giving a tab from a private window to a regular one
    When I open data/numbers/1.txt in a private window
    And I open data/numbers/2.txt in a new tab
    And I give the current tab to the window of session default
    Then the error "Can't move tabs from session private-* (private) to session default (container default)" should be shown

  Scenario: Giving a tab to another window of the same session
    When I open data/numbers/1.txt in a new window
    And I open data/numbers/2.txt in a new tab
    And I give the current tab to the window of session default
    And I wait until data/numbers/2.txt is loaded
    Then the session should look like:
      """
      windows:
      - session: default
        tabs:
        - history:
          - url: about:blank
        - history:
          - url: http://localhost:*/data/numbers/2.txt
      - session: default
        tabs:
        - history:
          - url: http://localhost:*/data/numbers/1.txt
      """

  Scenario: Taking a tab from a window on another container
    When I run :container-new take-a
    And I run :session-new take-session --container take-a
    And I take tab 1 from the window of session default
    Then the error "Can't move tabs from session default (container default) to session take-session (container take-a)" should be shown

  Scenario: Giving a tab to another session on the same container
    When I run :container-new share-give-a
    And I run :session-new share-give-one --container share-give-a
    And I open data/numbers/1.txt
    And I run :session-new share-give-two --container share-give-a
    And I open data/numbers/2.txt
    And I open data/numbers/3.txt in a new tab
    And I give the current tab to the window of session share-give-one
    And I wait until data/numbers/3.txt is loaded
    Then the session should look like:
      """
      windows:
      - session: default
      - session: share-give-one
        tabs:
        - history:
          - url: about:blank
          - url: http://localhost:*/data/numbers/1.txt
        - history:
          - url: http://localhost:*/data/numbers/3.txt
      - session: share-give-two
        tabs:
        - history:
          - url: about:blank
          - url: http://localhost:*/data/numbers/2.txt
      """
