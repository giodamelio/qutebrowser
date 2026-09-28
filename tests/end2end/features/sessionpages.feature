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
        Then the page should contain the plaintext "pages-b"
        And the page should contain the plaintext "never, in memory only"

    Scenario: Clicking a qute://sessions column header sorts by it
        When I run :session-new sort-b
        And I run :session-new sort-a
        And I run :session-list
        And I wait until qute://sessions/ is loaded
        And I run :jseval --world main document.querySelector('#sessions th').click(); console.log('ascending: ' + (() => { const names = Array.from(document.querySelectorAll('#sessions > tbody > tr > td.name'), cell => cell.textContent); return names.join() === names.slice().sort().join(); })())
        And I run :jseval --world main document.querySelector('#sessions th').click(); console.log('descending: ' + (() => { const names = Array.from(document.querySelectorAll('#sessions > tbody > tr > td.name'), cell => cell.textContent); return names.join() === names.slice().sort().reverse().join(); })())
        Then the javascript message "ascending: true" should be logged
        And the javascript message "descending: true" should be logged

    Scenario: :session-list -t opens the page in a new tab
        When I open data/numbers/1.txt
        And I run :session-list -t
        And I wait until qute://sessions/ is loaded
        Then the following tabs should be open:
            """
            - data/numbers/1.txt
            - qute://sessions/ (active)
            """
