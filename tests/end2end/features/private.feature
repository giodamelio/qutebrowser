Feature: Using private browsing

    Background:
        Given I open about:blank
        And I clean up open tabs

    Scenario: Opening new tab in private window
        When I open about:blank in a private window
        And I open cookies/set?qute-private-test=42 without waiting in a new tab
        And I wait until cookies is loaded
        And I run :close
        And I wait for "removed: main-window" in the log
        And I open cookies
        Then the cookie qute-private-test should not be set

    Scenario: Opening new tab in private window with :navigate next
        When I open data/navigate in a private window
        And I run :navigate -t next
        And I wait until data/navigate/next.html is loaded
        And I open cookies/set?qute-private-test=42 without waiting
        And I wait until cookies is loaded
        And I run :close
        And I wait for "removed: main-window" in the log
        And I open cookies
        Then the cookie qute-private-test should not be set

    Scenario: Using command history in a new private browsing window
        When I run :cmd-set-text :message-info "Hello World"
        And I run :command-accept
        And I open about:blank in a private window
        And I run :cmd-set-text :message-error "This should only be shown once"
        And I run :command-accept
        And I wait for the error "This should only be shown once"
        And I run :close
        And I wait for "removed: main-window" in the log
        And I run :cmd-set-text :
        And I run :command-history-prev
        And I run :command-accept
        # Then the error should not be shown again

    ## https://github.com/qutebrowser/qutebrowser/issues/1219

    Scenario: Make sure private data is cleared when closing last private window
        When I open about:blank in a private window
        And I open cookies/set?cookie-to-delete=1 without waiting in a new tab
        And I wait until cookies is loaded
        And I run :close
        And I open about:blank in a private window
        And I open cookies
        Then the cookie cookie-to-delete should not be set

    Scenario: Private windows opened separately do not share cookies
        When I open about:blank in a private window
        And I open cookies/set?private-isolated=1 without waiting in a new tab
        And I wait until cookies is loaded
        And I open cookies in a private window
        Then the cookie private-isolated should not be set

    Scenario: Windows in the same private session share cookies
        When I open about:blank in a private window
        And I open cookies/set?private-shared=1 without waiting in a new tab
        And I wait until cookies is loaded
        And I open cookies in a new window
        Then the cookie private-shared should be set to 1

    Scenario: Detaching a tab with :tab-give -p starts a new private session
        When I open about:blank in a private window
        And I open cookies/set?private-detach=1 without waiting in a new tab
        And I wait until cookies is loaded
        And I open cookies in a new tab
        And I run :tab-give -p
        And I wait until cookies is loaded
        Then the cookie private-detach should not be set

    Scenario: Sharing cookies with private browsing
        When I open cookies/set?qute-test=42 without waiting in a private window
        And I wait until cookies is loaded
        And I open cookies in a new tab
        Then the cookie qute-test should be set to 42

    Scenario: Opening private window with :navigate increment
        # Private window handled in commands.py
        When I open data/numbers/1.txt in a private window
        And I run :window-only
        And I run :navigate -w increment
        And I wait until data/numbers/2.txt is loaded
        Then the session should look like:
            """
            windows:
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/numbers/1.txt
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/numbers/2.txt
            """

    Scenario: Opening private window with :navigate next
        # Private window handled in navigate.py
        When I open data/navigate in a private window
        And I run :window-only
        And I run :navigate -w next
        And I wait until data/navigate/next.html is loaded
        Then the session should look like:
            """
            windows:
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/navigate
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/navigate/next.html
            """

    Scenario: Opening private window with :tab-clone
        When I open data/hello.txt in a private window
        And I run :window-only
        And I run :tab-clone -w
        And I wait until data/hello.txt is loaded
        Then the session should look like:
            """
            windows:
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/hello.txt
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/hello.txt
            """

    Scenario: Opening private window via :click-element
        When I open data/click_element.html in a private window
        And I run :window-only
        And I run :click-element --target window id link
        And I wait until data/hello.txt is loaded
        Then the session should look like:
            """
            windows:
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/click_element.html
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/hello.txt
            """

    Scenario: Private windows are never written to session files
        When I run :debug-close-other-sessions
        And I open data/numbers/1.txt
        And I open data/hello.txt in a private window
        And I run :debug-flush-sessions
        And I wait for the message "Flushed sessions."
        Then the session file default should contain "numbers/1.txt"
        And the session file default should not contain "hello.txt"

    # https://github.com/qutebrowser/qutebrowser/issues/2638
    Scenario: Turning off javascript with private browsing
        When I set content.javascript.enabled to false
        And I open data/javascript/consolelog.html in a private window
        Then the javascript message "console.log works!" should not be logged

    # Probably needs qutewm to work properly...
    @qtwebkit_skip  @xfail_norun  # Only applies to QtWebEngine 
    Scenario: Make sure local storage is isolated with private browsing
        When I open data/hello.txt in a private window
        And I run :jseval localStorage.qute_private_test = 42
        And I wait for "42" in the log
        And I run :close
        And I wait for "removed: main-window" in the log
        And I open data/hello.txt
        And I run :jseval localStorage.qute_private_test
        Then "No output or error" should be logged

    Scenario: Opening quickmark in private window
        When I open data/numbers/1.txt in a private window
        And I run :window-only
        And I run :quickmark-add http://localhost:(port)/data/numbers/2.txt two
        And I run :quickmark-load two
        And I wait until data/numbers/2.txt is loaded
        Then the session should look like:
            """
            windows:
            - private: True
              tabs:
              - history:
                - url: http://localhost:*/data/numbers/1.txt
                - url: http://localhost:*/data/numbers/2.txt
            """

    # https://github.com/qutebrowser/qutebrowser/issues/5810

    Scenario: Using qute:// scheme after reiniting private profile
        When I open about:blank in a private window
        And I run :close
        And I open qute://version in a private window
        Then the page should contain the plaintext "Version info"

    Scenario: Downloading after reiniting private profile
        When I open about:blank in a private window
        And I run :close
        And I open data/downloads/downloads.html in a private window
        And I run :click-element id download
        And I wait for "*PromptMode.download*" in the log
        And I run :mode-leave
        Then "Removed download *: download.bin *" should be logged

    Scenario: Adblocking after reiniting private profile
        When I open about:blank in a private window
        And I run :close
        And I set content.blocking.hosts.lists to ["http://localhost:(port)/data/blocking/qutebrowser-hosts"]
        And I set content.blocking.adblock.lists to []
        And I set content.blocking.method to hosts
        And I run :adblock-update
        And I wait for the message "hostblock: Read 1 hosts from 1 sources."
        And I open data/blocking/external_logo.html in a private window
        Then "Request to qutebrowser.org blocked by host blocker." should be logged

    @pyqt!=5.15.0   # cookie filtering is broken on QtWebEngine 5.15.0
    Scenario: Cookie filtering after reiniting private profile
        When I open about:blank in a private window
        And I run :close
        And I set content.cookies.accept to never
        And I open data/title.html in a private window
        And I open cookies/set?unsuccessful-cookie=1 without waiting in a new tab
        And I wait until cookies is loaded
        And I open cookies
        Then the cookie unsuccessful-cookie should not be set

    Scenario: Disabling JS after reiniting private profile
        When I open about:blank in a new window
        And I run :window-only
        And I set content.javascript.enabled to false
        And I open about:blank in a private window
        And I run :close
        And I open data/javascript/enabled.html in a private window
        Then the page should contain the plaintext "JavaScript is disabled"

    Scenario: Opening several URLs with one :open -p
        When I run :debug-close-other-sessions
        And I open data/numbers/1.txt and data/numbers/2.txt with one :open -p
        And I wait until data/numbers/1.txt is loaded
        And I wait until data/numbers/2.txt is loaded
        Then the session should look like:
            """
            windows:
            - session: default
            - session: private-*
              private: true
              tabs:
              - history:
                - url: http://localhost:*/data/numbers/1.txt
              - history:
                - url: http://localhost:*/data/numbers/2.txt
            """

    Scenario: Closing a private window adds nothing to window undo
        # Setting the size to 0 empties the window undo stack, so
        # windows closed by earlier scenarios can't be undone here.
        When I set tabs.undo_stack_size to 0
        And I set tabs.undo_stack_size to 100
        And I open about:blank in a private window
        And I run :close
        And I wait for "removed: main-window" in the log
        And I run :undo --window
        Then the error "Nothing to undo" should be shown
