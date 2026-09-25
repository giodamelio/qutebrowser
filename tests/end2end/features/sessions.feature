Feature: Saving and loading sessions

  Background:
    Given I clean up open tabs

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

