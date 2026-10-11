# Intent: Repo libraries

A connected repository shares chains, library components, skills and agent
profiles from `.kraft/` on its default branch. An instance loads them under
the repository's id only after a person accepts a review, and changes them
only when a person accepts the next one. `enforced-by:` names the test that
pins each requirement.

## REQ a-repo-library-is-the-default-branchs-kraft-folder

WHEN the commit `origin` names as its default branch holds a layout file under `.kraft/`, the system SHALL offer it as a repo library named by the repository's id.
enforced-by: tests/addons/test_repo_library.py::test_extract_gives_the_layout_files_and_a_derived_manifest, tests/addons/test_repo_library.py::test_a_repo_with_no_layout_file_has_no_repo_library[no-kraft-folder], tests/addons/test_repo_library.py::test_a_repo_with_no_layout_file_has_no_repo_library[only-a-collection], tests/addons/test_load.py::test_an_accepted_repo_library_loads_under_the_repo_id
origin: src/kraft/addons/repo_library.py §extract

## REQ the-remote-names-the-commit

The system SHALL take the commit to read from the remote's answer, never from a ref, working tree or worktree of the connected checkout.
enforced-by: tests/addons/test_repo_library.py::test_the_commit_comes_from_the_remote_not_the_checkout
origin: src/kraft/addons/repo_library.py §remote

## REQ an-unaccepted-repo-library-is-not-loaded

IF a repo library has not been accepted on this instance, THEN the system SHALL NOT load it and SHALL NOT report a fault.
enforced-by: tests/addons/test_repo_library.py::test_a_first_load_waits_with_everything_shown_as_new, tests/cli/test_repos_connect.py::test_connect_offers_the_repo_library[yes-never-accepts], tests/cli/test_repos_connect.py::test_connect_offers_the_repo_library[no-terminal-never-accepts], tests/cli/test_repos_connect.py::test_connect_offers_the_repo_library[json-never-accepts]
origin: src/kraft/addons/repo_library.py §check

## REQ a-repo-library-holds-what-an-addon-may

IF a repo library breaks a rule an add-on is refused for, or qualifies a name with anything but its own `library:`, THEN the system SHALL refuse it and name the file and key.
enforced-by: tests/addons/test_repo_library.py::test_a_repo_library_is_refused_for_what_an_addon_is[subprocess], tests/addons/test_repo_library.py::test_a_repo_library_is_refused_for_what_an_addon_is[permission], tests/addons/test_repo_library.py::test_a_repo_library_is_refused_for_what_an_addon_is[harness-the-instance-lacks], tests/addons/test_repo_library.py::test_a_repo_library_is_refused_for_what_an_addon_is[its-own-repo-id-as-a-qualifier], tests/addons/test_repo_library.py::test_a_repo_library_is_refused_for_what_an_addon_is[broken-chain]
origin: src/kraft/addons/repo_library.py §check

## REQ a-repo-id-must-be-free-to-be-a-namespace

IF a repository's id is `kraft`, an installed add-on's namespace, or longer than 64 characters, THEN the system SHALL refuse to accept its library.
enforced-by: tests/addons/test_repo_library.py::test_an_id_that_cannot_be_a_namespace_is_refused[reserved], tests/addons/test_repo_library.py::test_an_id_that_cannot_be_a_namespace_is_refused[an-addons-namespace], tests/addons/test_repo_library.py::test_an_id_that_cannot_be_a_namespace_is_refused[too-long]
origin: src/kraft/addons/repo_library.py §_id_problem

## REQ only-the-reviewed-candidate-is-accepted

IF the candidate or the lock is not the one a person reviewed, THEN the system SHALL refuse the accept and write nothing.
enforced-by: tests/addons/test_repo_library.py::test_an_accept_after_the_lock_moved_writes_nothing, tests/api/test_repos_library.py::test_accepting_what_was_not_reviewed_writes_nothing[another-digest]
origin: src/kraft/addons/repo_library.py §accept

## REQ a-change-waits-for-a-person

WHERE an entry does not set `library_auto_update: true`, WHEN its accepted repo library changes, the system SHALL report the change as waiting and SHALL NOT apply it.
enforced-by: tests/addons/test_repo_library.py::test_the_daily_check_reports_and_applies_nothing[a-change], tests/addons/test_repo_library.py::test_the_daily_check_applies_text_and_holds_the_rest[not-opted-in], tests/test_doctor_addons.py::test_the_repo_libraries_row[waiting]
origin: src/kraft/addons/repo_library.py §check_all

## REQ an-unaccepted-repo-is-not-asked-about

The system SHALL NOT contact a remote for a library that was never accepted, except when a person asks.
enforced-by: tests/addons/test_repo_library.py::test_the_daily_check_asks_about_no_library_nobody_accepted
origin: src/kraft/addons/repo_library.py §check_all

## REQ a-repo-librarys-default-chain-is-its-repos

WHEN an item is filed with no chain in a repository whose entry's `default_chain` is unset or `default` and whose loaded library has a chain `default`, the system SHALL run that chain.
enforced-by: tests/api/test_deps_repo_library.py::test_which_chain_an_item_with_no_chain_runs[the-repos-default], tests/api/test_deps_repo_library.py::test_which_chain_an_item_with_no_chain_runs[default-counts-as-unset], tests/api/test_deps_repo_library.py::test_which_chain_an_item_with_no_chain_runs[the-entrys-own-wins]
origin: src/kraft/api/deps.py §chain_template_for

## REQ a-worker-does-not-take-a-repo-library

IF a worker runs `kraft repo library`, THEN the system SHALL refuse.
enforced-by: tests/cli/test_repos_library.py::test_a_worker_may_not_take_a_repo_library
origin: src/kraft/cli/repo.py §_cmd_library

## REQ a-named-repo-library-holds-its-repo

IF local configuration names a repo library's namespace, THEN the system SHALL refuse to disconnect the repository and list each reference.
enforced-by: tests/api/test_repos_library.py::test_disconnect_is_refused_while_something_names_the_library
origin: src/kraft/api/routes/repos.py §remove_repo

## REQ an-opted-in-repo-takes-text-changes

WHERE an entry sets `library_auto_update: true`, WHEN its accepted library changes in prompt, skill or steering text only, the system SHALL apply the change without a person and load it.
enforced-by: tests/addons/test_repo_library.py::test_the_daily_check_applies_text_and_holds_the_rest[text-only], tests/api/test_deps_repo_library.py::test_an_opted_in_repo_takes_a_text_change_when_the_server_checks
origin: src/kraft/addons/repo_library.py §_unattended

## REQ a-reach-line-holds-an-unattended-apply

IF a changed repo library's review has a Reach line, THEN the system SHALL keep serving the accepted version and report the change as held.
enforced-by: tests/addons/test_repo_library.py::test_the_daily_check_applies_text_and_holds_the_rest[a-reach-line], tests/test_doctor_addons.py::test_the_repo_libraries_row[held]
origin: src/kraft/addons/repo_library.py §_unattended

## REQ a-change-of-shape-waits-for-a-person

IF a change alters any chain other than in its text, or adds or removes a chain, THEN the system SHALL NOT apply it without a person.
enforced-by: tests/addons/test_repo_library.py::test_what_holds_a_change_with_no_reach_line[a-node-added], tests/addons/test_repo_library.py::test_what_holds_a_change_with_no_reach_line[a-chain-added], tests/addons/test_repo_library.py::test_what_holds_a_change_with_no_reach_line[a-judges-prompt], tests/addons/test_repo_library.py::test_a_local_chain_that_changes_shape_through_the_library_holds, tests/addons/test_repo_library.py::test_the_daily_check_applies_text_and_holds_the_rest[a-chain-added]
origin: src/kraft/addons/repo_library.py §_holds

## REQ a-deciding-tasks-text-waits-for-a-person

IF a change alters the text of a skill or steering profile that a gate's `auto_review`, a `judge` or an `escalation` reads, THEN the system SHALL NOT apply it without a person.
enforced-by: tests/addons/test_repo_library.py::test_what_holds_a_change_with_no_reach_line[a-reviewers-skill], tests/addons/test_repo_library.py::test_what_holds_a_change_with_no_reach_line[a-reviewers-steering], tests/addons/test_repo_library.py::test_the_daily_check_applies_text_and_holds_the_rest[a-reviewers-skill]
origin: src/kraft/addons/repo_library.py §_holds

## REQ a-moved-remote-is-reach

IF the remote's URL or its default branch's name differs from what was accepted, THEN the system SHALL say so as a Reach line.
enforced-by: tests/addons/test_repo_library.py::test_a_change_from_somewhere_else_is_said_as_reach[another-url], tests/addons/test_repo_library.py::test_a_change_from_somewhere_else_is_said_as_reach[another-default-branch]
origin: src/kraft/addons/repo_library.py §check

## REQ a-moved-default-chain-is-reach

IF a change gives or takes away the repository's default chain, THEN the system SHALL say so as a Reach line.
enforced-by: tests/addons/test_repo_library.py::test_a_change_that_moves_the_repos_default_chain_is_reach[gains-default], tests/addons/test_repo_library.py::test_a_change_that_moves_the_repos_default_chain_is_reach[loses-default], tests/addons/test_repo_library.py::test_a_change_that_moves_the_repos_default_chain_is_reach[the-entry-names-another]
origin: src/kraft/addons/repo_library.py §check

## REQ nothing-is-first-loaded-or-dropped-unattended

The system SHALL NOT accept a first load, or drop a library, without a person.
enforced-by: tests/addons/test_repo_library.py::test_an_opted_in_entry_never_drops_or_guesses[library-gone], tests/addons/test_repo_library.py::test_a_first_load_has_no_holds_of_its_own, tests/addons/test_repo_library.py::test_the_daily_check_asks_about_no_library_nobody_accepted
origin: src/kraft/addons/repo_library.py §check_all

## REQ the-key-is-asked-for-once

WHEN a person accepts a first load at a prompt, the system SHALL ask once whether later text changes may be taken unattended, SHALL say what that trusts, and SHALL set the key only on a yes.
enforced-by: tests/cli/test_repos_library.py::test_a_first_accept_asks_once_about_later_changes[yes], tests/cli/test_repos_library.py::test_a_first_accept_asks_once_about_later_changes[no], tests/cli/test_repos_library.py::test_a_first_accept_asks_once_about_later_changes[enter-is-no], tests/cli/test_repos_library.py::test_a_first_accept_asks_once_about_later_changes[the-yes-flag-is-not-asked], tests/cli/test_repos_library.py::test_a_later_accept_is_not_asked_again
origin: src/kraft/cli/repo.py §_ask_auto_update

## REQ an-unbounded-auto-update-is-warned-about

WHERE an entry sets `library_auto_update: true` on an instance with no `maxima:`, `allowed_tools` or `sandbox`, doctor SHALL warn.
enforced-by: tests/test_doctor_addons.py::test_the_repo_libraries_row_warns_about_unbounded_auto_update[no-bounds], tests/test_doctor_addons.py::test_the_repo_libraries_row_warns_about_unbounded_auto_update[maxima], tests/test_doctor_addons.py::test_the_repo_libraries_row_warns_about_unbounded_auto_update[sandbox]
origin: src/kraft/doctor.py §_repo_libraries_check
