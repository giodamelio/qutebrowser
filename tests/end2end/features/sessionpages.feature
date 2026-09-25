Feature: Session and container pages
    qute://containers and qute://sessions list every container and session.

    Background:
        Given I run :debug-close-other-sessions
        And I set url.start_pages to ["about:blank"]
        And I clean up open tabs

    Scenario: :container-list shows a container and its session
        When I run :container-new pages-a
        And I run :session-new pages-session --container pages-a
        And I run :container-list
        And I wait until qute://containers/ is loaded
        Then the page should contain the plaintext "pages-a"
        And the page should contain the plaintext "pages-session (open)"

    Scenario: :session-list shows a container session and a private session
        When I run :container-new pages-b
        And I run :session-new pages-list --container pages-b
        And I open about:blank in a private window
        And I run :session-list
        And I wait until qute://sessions/ is loaded
        Then the page should contain the plaintext "Container: pages-b"
        And the page should contain the plaintext "private, in memory and never saved"

    Scenario: :session-list -t opens the page in a new tab
        When I open data/numbers/1.txt
        And I run :session-list -t
        And I wait until qute://sessions/ is loaded
        Then the following tabs should be open:
            """
            - data/numbers/1.txt
            - qute://sessions/ (active)
            """
