Feature: Invoking a new process
    Simulate what happens when running qutebrowser with an existing instance

    Background:
        Given I run :debug-close-other-sessions
        And I clean up open tabs

    Scenario: Using new_instance_open_target = tab
        When I set new_instance_open_target to tab
        And I open data/title.html
        And I open data/search.html as a URL
        Then the following tabs should be open:
            """
            - data/title.html
            - data/search.html (active)
            """

    Scenario: Using new_instance_open_target = tab-bg
        When I set new_instance_open_target to tab-bg
        And I open data/title.html
        And I open data/search.html as a URL
        Then the following tabs should be open:
            """
            - data/title.html (active)
            - data/search.html
            """

    Scenario: Using new_instance_open_target = window
        When I set new_instance_open_target to window
        And I open data/title.html
        And I open data/search.html as a URL
        Then the session should look like:
            """
            windows:
            - tabs:
              - history:
                - url: about:blank
                - url: http://localhost:*/data/title.html
            - tabs:
              - history:
                - url: http://localhost:*/data/search.html
            """

    Scenario: Using new_instance_open_target = private-window
        When I set new_instance_open_target to private-window
        And I open data/title.html
        And I open data/search.html as a URL
        Then the session should look like:
            """
            windows:
            - tabs:
              - history:
                - url: about:blank
                - url: http://localhost:*/data/title.html
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/search.html
            """

    Scenario: Using new_instance_open_target_window = last-opened
        When I set new_instance_open_target to tab
        And I set new_instance_open_target_window to last-opened
        And I open data/title.html
        And I open data/search.html in a new window
        And I open data/hello.txt as a URL
        Then the session should look like:
            """
            windows:
            - tabs:
              - history:
                - url: about:blank
                - url: http://localhost:*/data/title.html
            - tabs:
              - history:
                - url: http://localhost:*/data/search.html
              - history:
                - url: http://localhost:*/data/hello.txt
            """

    Scenario: Using new_instance_open_target_window = first-opened
        When I set new_instance_open_target to tab
        And I set new_instance_open_target_window to first-opened
        And I open data/title.html
        And I open data/search.html in a new window
        And I open data/hello.txt as a URL
        Then the session should look like:
            """
            windows:
            - tabs:
              - history:
                - url: about:blank
                - url: http://localhost:*/data/title.html
              - history:
                - url: http://localhost:*/data/hello.txt
            - tabs:
              - history:
                - url: http://localhost:*/data/search.html
            """

    # issue #1060

    Scenario: Using target_window = first-opened after tab-give
        When I set new_instance_open_target to tab
        And I set new_instance_open_target_window to first-opened
        And I open data/title.html
        And I open data/search.html in a new tab
        And I run :tab-give
        And I wait until data/search.html is loaded
        And I open data/hello.txt as a URL
        Then the session should look like:
            """
            windows:
            - tabs:
              - history:
                - url: about:blank
                - url: http://localhost:*/data/title.html
              - history:
                - url: http://localhost:*/data/hello.txt
            - tabs:
              - history:
                - url: http://localhost:*/data/search.html
            """

    Scenario: Opening a new qutebrowser instance with no parameters
        When I set new_instance_open_target to tab
        And I set url.start_pages to ["http://localhost:(port)/data/hello.txt"]
        And I open data/title.html
        And I spawn a new window
        And I wait until data/hello.txt is loaded
        Then the session should look like:
            """
            windows:
            - tabs:
              - history:
                - url: about:blank
                - url: http://localhost:*/data/title.html
            - tabs:
              - history:
                - url: http://localhost:*/data/hello.txt
            """

    Scenario: Several URLs in one IPC message share one private session
        When I open data/numbers/1.txt and data/numbers/2.txt in one IPC message with target private-window
        And I wait until data/numbers/1.txt is loaded
        And I wait until data/numbers/2.txt is loaded
        Then the private windows should share one session

    # Routing external links between containers

    Scenario: Links open directly when every session shares a container
        When I set new_instance_open_target to tab
        And I set url.start_pages to ["about:blank"]
        And I run :session-new route-same
        And I open data/search.html as a URL
        Then "Asking question *" should not be logged

    Scenario: Links ask for a window when several containers are open
        When I set new_instance_open_target to tab
        And I set url.start_pages to ["about:blank"]
        And I run :container-new route-pick
        And I run :session-new route-picked --container route-pick
        And I open data/search.html as a URL without waiting
        And the prompt window picks the window of session route-picked
        And I wait until data/search.html is loaded
        Then the session should look like:
            """
            windows:
            - session: default
            - session: route-picked
              tabs:
              - history:
                - url: about:blank
              - history:
                - url: http://localhost:*/data/search.html
            """

    Scenario: One picker for several links
        When I set new_instance_open_target to tab
        And I set url.start_pages to ["about:blank"]
        And I run :container-new route-multi
        And I run :session-new route-many --container route-multi
        And I open data/numbers/1.txt and data/numbers/2.txt in one IPC message with target auto
        And the prompt window picks the window of session route-many
        And I wait until data/numbers/1.txt is loaded
        And I wait until data/numbers/2.txt is loaded
        Then the session should look like:
            """
            windows:
            - session: default
            - session: route-many
              tabs:
              - history:
                - url: about:blank
              - history:
                - url: http://localhost:*/data/numbers/1.txt
              - history:
                - url: http://localhost:*/data/numbers/2.txt
            """

    Scenario: A picked window's session gets the new window
        When I set new_instance_open_target to window
        And I set url.start_pages to ["about:blank"]
        And I run :container-new route-win
        And I run :session-new route-windowed --container route-win
        And I open data/search.html as a URL without waiting
        And the prompt window picks the window of session route-windowed
        And I wait until data/search.html is loaded
        Then session route-windowed should have 2 windows

    Scenario: Cancelling the window picker discards the links
        When I set new_instance_open_target to tab
        And I run :container-new route-cancel
        And I run :session-new route-cancelled --container route-cancel
        And I open data/search.html as a URL without waiting
        And the prompt window runs :mode-leave
        Then the message "Links not opened: http://localhost:*/data/search.html" should be shown

    Scenario: Closing the asking window discards the links
        When I set new_instance_open_target to tab
        And I run :container-new route-abort
        And I run :session-new route-aborted --container route-abort
        And I open data/search.html as a URL without waiting
        And the window of session default runs :close
        Then the error "Window * closed before it could ask where to open the links" should be shown
        And the message "Links not opened: http://localhost:*/data/search.html" should be shown
