# Intent: Kraft in Herdr

`plugins/herdr/` shows every live work item in Herdr as an agent with its own
tab. It reads Kraft only through the `kraft` command and drives Herdr only
through the `herdr` command. The one change to Kraft itself is general:
`kraft view events --all -f` streams every item's events.

## REQ events-all-streams-every-item-from-the-board-cursor

WHEN `kraft view events --all -f` runs without `--after`, the system SHALL
stream every item's events from the board's event cursor, and SHALL NOT end the
stream when one item completes.
enforced-by: tests/cli/test_watching.py::test_events_all_follows_every_item_from_the_board_cursor
origin: src/kraft/cli/view.py §_cmd_events

## REQ events-all-needs-follow

IF `kraft view events --all` runs without `-f`, THEN the system SHALL refuse,
because there is no backlog to print.
enforced-by: tests/cli/test_watching.py::test_events_all_needs_follow
origin: src/kraft/cli/view.py §_cmd_events

## REQ herdr-state-for-every-display-status

The Herdr plugin SHALL map every Kraft display status to one of the states
`idle`, `working`, `blocked` and `unknown`, and SHALL map only `needs_you` and
`failed` to `blocked`.
enforced-by: plugins/herdr/tests/test_mapping.py::test_every_kraft_display_status_is_mapped, plugins/herdr/tests/test_mapping.py::test_no_state_is_done
origin: plugins/herdr/kraft_herdr.py §STATES

## REQ herdr-bridge-readopts-tabs-by-label

WHEN Herdr restarts and the bridge reconciles, the bridge SHALL reuse each item's
existing tab, found by its label, and SHALL NOT open a second tab for it.
enforced-by: plugins/herdr/tests/test_bridge.py::test_reconcile_readopts_tabs_by_label_after_restart
origin: plugins/herdr/kraft_herdr.py §Bridge.reconcile

## REQ herdr-bridge-reports-a-departed-item-finished-once

WHEN an item leaves the board, the bridge SHALL report its tab finished exactly
once, and SHALL NOT reopen its tab.
enforced-by: plugins/herdr/tests/test_bridge.py::test_missing_item_is_reported_finished_once
origin: plugins/herdr/kraft_herdr.py §Bridge.reconcile

## REQ herdr-bridge-runs-once

IF another bridge holds the lock, THEN a second bridge SHALL exit without
reporting anything.
enforced-by: plugins/herdr/tests/test_bridge.py::test_second_bridge_exits_on_the_lock
origin: plugins/herdr/kraft_herdr.py §acquire_lock

## REQ herdr-bridge-reconnects-when-the-stream-ends

WHEN the event stream ends, the bridge SHALL start it again and reconcile, and
SHALL NOT open duplicate tabs.
enforced-by: plugins/herdr/tests/test_bridge.py::test_stream_end_reconnects
origin: plugins/herdr/kraft_herdr.py §Bridge.run

## REQ herdr-item-tab-waits-for-a-first-session

WHILE an item has no worker session, its tab SHALL keep running and wait,
and SHALL NOT exit.
enforced-by: plugins/herdr/tests/test_item_tab.py::test_item_tab_waits_for_a_first_session, plugins/herdr/tests/test_item_tab.py::test_item_tab_survives_logs_failing_with_no_session
origin: plugins/herdr/kraft_herdr.py §item_tab
