# Intent: item files

Where a work item's files are written and read. Everything but the worktree
goes under `run/items/<id>/`; a file written before the upgrade is read where
it is. A worker writes only its own two session files and its item's `docs/`,
and Kraft never follows a link in anything a worker can write.

## REQ a-gate-document-is-written-to-its-items-docs

WHEN the system tells an agent where to write a gate document, it SHALL name
`run/items/<id>/docs/<kind>.md`.
enforced-by:

## REQ a-session-s-files-are-in-its-own-folder

WHEN the system creates a session that is not an escalation turn, it SHALL put
the session's log in `run/items/<id>/sessions/<sid>/` and its result file in
that folder's `out/`.
enforced-by:

## REQ a-filed-attachment-is-copied-to-its-items-folder

WHEN intake copies an attachment, the system SHALL copy it into
`run/items/<id>/filed/`.
enforced-by:

## REQ a-document-is-read-from-the-new-place-first

WHEN the system reads a work item's gate document, it SHALL read
`run/items/<id>/docs/<kind>.md` if that exists, and otherwise
`.engineering/<kind>s/<id>.md` in the item's worktree.
enforced-by: tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[both-new-wins], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[new-only], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[old-only], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[neither]
origin: src/kraft/item_paths.py §document -- the new place is read first because a newer revision is only ever written there; nothing is moved at upgrade, so the old place is the fallback rather than a per-item flag.

## REQ an-existing-sandbox-home-is-kept

WHILE `run/sandbox-home/<id>/` exists, the system SHALL use it as the item's
sandbox home.
enforced-by: tests/test_item_paths.py::test_an_existing_sandbox_home_is_kept[old-home-exists]
origin: src/kraft/item_paths.py §sandbox_home -- the old home holds the agent CLI's chats a paused session resumes from.

## REQ a-new-sandbox-home-is-in-the-items-folder

WHILE `run/sandbox-home/<id>/` does not exist, the system SHALL use
`run/items/<id>/sandbox-home/` as the item's sandbox home.
enforced-by: tests/test_item_paths.py::test_an_existing_sandbox_home_is_kept[no-old-home]
origin: src/kraft/item_paths.py §sandbox_home

## REQ an-escalation-thread-keeps-its-paths

WHILE an escalation thread continues, the system SHALL give each of its turns
the result and summary paths the thread's first turn was given.
enforced-by:

## REQ kraft-records-the-summary-it-asked-for

WHEN a session exits with `summary.md` in its own or its thread's folder, the
system SHALL record that file as its summary.
enforced-by:

## REQ a-worker-cannot-name-a-summary-under-items

IF a worker reports a summary ref under `items/`, THEN the system SHALL NOT
record it.
enforced-by:

## REQ the-session-folder-is-not-mounted

WHILE a sandboxed session runs, the system SHALL NOT mount any session folder
as a folder.
enforced-by:

## REQ a-worker-writes-only-its-own-two-files-and-docs

WHILE a sandboxed session runs, the system SHALL NOT mount read-write any path
under `run/items/<id>/` other than its own `result.json`, its own
`summary.md`, its item's `docs/` and its item's sandbox home.
enforced-by:

## REQ a-harness-is-granted-only-out-and-docs

WHEN the system names the folders a harness may write outside its worktree, it
SHALL name only the session's `out/` folder, its item's `docs/` and the
repository's git folder.
enforced-by:

## REQ no-read-of-a-worker-written-file-follows-a-link

WHEN the system reads a file in a folder a worker can write, it SHALL NOT
follow a symbolic link.
enforced-by: tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[symlink-is-none], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[fifo-is-none], tests/test_item_paths.py::test_a_document_is_read_from_the_new_place_first[old-folder-symlink-is-none], tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[symlink], tests/test_item_paths.py::test_kraft_records_only_a_summary_the_worker_wrote[fifo], tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[source-symlink], tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[source-folder-symlink]
origin: src/kraft/item_paths.py -- every read goes through `worker.worktree_read`, which opens one component at a time with `O_NOFOLLOW` and opens the file non-blocking, so a planted FIFO cannot hang the server either.

## REQ no-write-into-a-worker-folder-follows-a-link

WHEN the system writes into a folder a worker can write, it SHALL NOT follow or
replace a symbolic link.
enforced-by: tests/test_item_paths.py::test_a_planted_name_in_docs_is_neither_followed_nor_replaced, tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[dest-symlink], tests/test_item_paths.py::test_copying_a_worker_s_file_follows_no_link[dest-folder-symlink]
origin: src/kraft/item_paths.py §copy_in -- each folder on the way is opened with `open_dir_no_symlinks` and the file with `O_CREAT | O_EXCL | O_NOFOLLOW` relative to it, so a name already there is refused rather than written through.

## REQ history-and-filed-are-not-mounted

WHILE a sandboxed session runs, the system SHALL NOT mount its item's
`history/` or `filed/`.
enforced-by:

## REQ every-agent-is-told-its-items-documents

WHEN the system launches an agent, it SHALL name in its context the absolute
path of each of its item's documents that exists.
enforced-by:

## REQ a-revised-document-keeps-its-earlier-version

WHEN the system dispatches a task that produces a kind whose document exists,
it SHALL first copy that document to `history/<kind>.<n>.md`.
enforced-by: tests/test_item_paths.py::test_a_revised_document_keeps_its_earlier_versions, tests/test_item_paths.py::test_an_old_document_is_seeded_into_docs_before_its_revision[own-worktree], tests/test_item_paths.py::test_an_old_document_is_seeded_into_docs_before_its_revision[member-worktree]
origin: src/kraft/item_paths.py §keep_version -- the document stays where it is, so the agent revises it in place and a failed re-run still leaves the gate one; an old document is also seeded into `docs/` so the revise note and the write-to path name one file.

## REQ archiving-removes-the-sandbox-home

WHEN a work item is archived, the system SHALL remove its sandbox home.
enforced-by:

## REQ archiving-keeps-the-items-folder

WHEN a work item is archived, the system SHALL keep its `docs/`, `history/`,
`filed/` and `sessions/`.
enforced-by:

## REQ abandoning-removes-the-items-folder

WHEN a work item is abandoned, the system SHALL remove `run/items/<id>/`.
enforced-by:

## REQ storage-counts-item-sandbox-homes

The system SHALL count every `run/items/*/sandbox-home` in the governed
storage figure, under its item.
enforced-by:

## REQ doctor-counts-worktrees-with-old-documents

WHEN `kraft admin doctor` runs, the system SHALL report how many worktrees
hold a `.engineering/` folder.
enforced-by:
