# Remote console

`kraft console --server URL` shows a Kraft on another machine after a password
sign-in, and holds a session, never this machine's token.

## REQ remote-client-never-sends-the-mcp-token
WHERE the console was given `--server`, the system SHALL NOT read or send `run/mcp-token` on an HTTP call or on the event stream.
enforced-by: tests/client/test_transport.py::test_a_remote_client_sends_the_session_cookie_and_never_reads_the_token, tests/client/test_read.py::test_a_remote_event_stream_follows_the_scheme_and_sends_only_the_cookie[https-is-wss], tests/client/test_read.py::test_a_remote_event_stream_follows_the_scheme_and_sends_only_the_cookie[http-is-ws]
origin: src/kraft/client/transport.py

## REQ remote-client-never-follows-redirects
WHERE the console was given `--server`, the system SHALL NOT follow a redirect, so the session cookie reaches no other host.
enforced-by: tests/client/test_transport.py::test_a_redirect_is_not_followed_so_the_cookie_stays_on_its_host
origin: src/kraft/client/transport.py

## REQ remote-stream-refusal-is-confirmed-once
WHEN a remote event stream's handshake is refused, the system SHALL make one HTTP call to tell an ended session from a perimeter refusal.
enforced-by: tests/client/test_read.py::test_a_refused_remote_stream_is_confirmed_with_one_http_call[session-gone], tests/client/test_read.py::test_a_refused_remote_stream_is_confirmed_with_one_http_call[perimeter-refused-the-handshake]
origin: src/kraft/client/reads.py

## REQ server-url-normalization-is-strict
The system SHALL key a stored session by scheme, lower-cased host and explicit port, and SHALL refuse a path, query, fragment, user name or IPv6 zone id.
enforced-by: tests/client/test_remote.py::test_a_server_url_is_keyed_by_scheme_host_and_port[lowercase+port], tests/client/test_remote.py::test_a_server_url_is_keyed_by_scheme_host_and_port[ipv6-lowercased], tests/client/test_remote.py::test_a_server_url_that_cannot_be_a_key_is_refused_in_one_sentence[path-prefix], tests/client/test_remote.py::test_a_server_url_that_cannot_be_a_key_is_refused_in_one_sentence[query], tests/client/test_remote.py::test_a_server_url_that_cannot_be_a_key_is_refused_in_one_sentence[fragment], tests/client/test_remote.py::test_a_server_url_that_cannot_be_a_key_is_refused_in_one_sentence[userinfo], tests/client/test_remote.py::test_a_server_url_that_cannot_be_a_key_is_refused_in_one_sentence[zone-id]
origin: src/kraft/client/remote.py

## REQ stored-session-forgotten-only-if-unchanged
WHEN a session is rejected, the system SHALL delete its stored entry only if the entry still holds the token that was rejected.
enforced-by: tests/client/test_remote.py::test_an_entry_is_forgotten_only_if_it_still_holds_the_token_that_failed[same-token-deleted], tests/client/test_remote.py::test_an_entry_is_forgotten_only_if_it_still_holds_the_token_that_failed[re-signed-in-meanwhile-kept]
origin: src/kraft/client/remote.py

## REQ sign-in-reads-the-cookie-by-hand-and-names-the-client
WHEN signing in, the system SHALL send a User-Agent that names the client and read the session cookie from `Set-Cookie` without checking its `Secure` flag.
enforced-by: tests/client/test_remote.py::test_sign_in_posts_the_password_with_a_label_and_reads_the_cookie_by_hand
origin: src/kraft/client/remote.py

## REQ plain-http-sign-in-warns-first
WHEN signing in over plain http to a host that is not loopback, the system SHALL warn that the password is unencrypted and SHALL ask before asking for the password.
enforced-by: tests/console/test_signin.py::test_plain_http_to_another_host_warns_every_sign_in_and_asks_to_go_on[go-on], tests/console/test_signin.py::test_plain_http_to_another_host_warns_every_sign_in_and_asks_to_go_on[decline]
origin: src/kraft/console/signin.py

## REQ remote-console-runs-nothing-on-this-machine
WHERE the console was given `--server`, the system SHALL NOT start a `kraft` child process and SHALL refuse a typed command with one sentence.
enforced-by: tests/console/test_remote.py::test_a_remote_console_refuses_a_typed_command_and_does_nothing[runs-a-child], tests/console/test_remote.py::test_a_remote_console_refuses_a_typed_command_and_does_nothing[goes-to-a-screen], tests/console/test_remote.py::test_run_command_is_closed_remotely_whoever_asks
origin: src/kraft/console/app.py

## REQ remote-console-never-offers-start-server
WHERE the console was given `--server`, the system SHALL NOT offer to start a server, even for a loopback address.
enforced-by: tests/console/test_remote.py::test_a_remote_console_never_offers_to_start_a_server_even_through_a_loopback_forward
origin: src/kraft/console/app.py

## REQ ended-session-is-said-and-forgotten
WHEN a remote console's session is rejected, the system SHALL say the server ended the session and SHALL delete the stored session.
enforced-by: tests/console/test_remote.py::test_a_session_that_ends_while_running_says_so_and_is_deleted
origin: src/kraft/console/app.py

## REQ web-ui-shows-kraft-client-labels-as-given
The web UI SHALL show a session label that starts with `Kraft ` as given, capped at 60 characters.
enforced-by: frontend/src/ua.test.ts::parseUserAgent › shows a Kraft client's own label as given and caps it at 60 characters
origin: frontend/src/ua.ts
