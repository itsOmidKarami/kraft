# Intent: replies to the review bots

Kraft reading every note of a review bot's thread, and the forge reads and
writes, the body gate and the reply record that let it answer a bot in that
thread without posting twice, leaking a secret or arguing forever.

`enforced-by:` names the test that pins each requirement.

## REQ bot-feedback-reads-every-page
WHEN the review wait reads what the review bots said, the system SHALL read
every page of the merge request's comments, reviews, review threads, each
thread's comments, notes and discussions.
enforced-by: , , , , , 
origin: src/kraft/adapters/forge/gh.py

## REQ collected-threads-carry-their-notes
WHEN the review wait writes the collection a triage reads, the system SHALL list
each thread's notes with their id, author, whose each is, body, creation time,
the note each answers and the review each was submitted in, SHALL leave out
GitHub pending comments and GitLab system notes, SHALL keep each thread's id,
file, line and flattened body as before, and SHALL record when the bots were read.
enforced-by: tests/adapters/forge/test_reviewer_feedback.py::test_reviewer_feedback_reads[gh-bot], tests/adapters/forge/test_reviewer_feedback.py::test_reviewer_feedback_reads[glab-bot], tests/adapters/forge/test_reviewer_feedback.py::test_reviewer_feedback_reads[gh-pending-left-out-of-notes], tests/adapters/forge/test_reviewer_feedback.py::test_reviewer_feedback_reads[glab-system-note-left-out-of-notes], tests/adapters/forge/test_reviewer_feedback.py::test_reviewer_feedback_reads[gh-a-reply-names-what-it-answers], tests/adapters/forge/test_settle.py::test_each_collected_note_says_whose_it_is, tests/adapters/forge/test_run_review.py::test_the_collect_path_settles_then_hands_on_the_collection[deadline-crossed-between-reads]
origin: src/kraft/adapters/forge/settle.py

## REQ a-github-bot-note-comes-from-an-app-account
WHEN Kraft decides whether a note is a configured bot's, the system SHALL
require, where the forge reports an author type, that the author is an app
account, whatever its login.
enforced-by: tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[app-account], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[github-user-named-like-the-bot], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[gitlab-login-decides]
origin: src/kraft/adapters/forge/replies/facts.py

## REQ a-kraft-note-is-the-viewers-and-unedited
WHEN Kraft decides whether a note is its own, the system SHALL count a note the
reply record holds as posted only while its body still hashes to what the forge
returned, and any other note only when the viewer wrote it and it carries a
reply marker of this work item.
enforced-by: tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[no-viewer-known], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[the-viewers-marked-note], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[a-marker-on-someone-elses-note], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[another-items-marker], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[recorded-and-unedited], tests/adapters/forge/replies/test_facts.py::test_whose_a_note_is[recorded-then-edited], tests/adapters/forge/replies/test_facts.py::test_a_marker_is_read_off_the_last_line_and_left_out_of_the_hash[reply], tests/adapters/forge/replies/test_facts.py::test_a_marker_is_read_off_the_last_line_and_left_out_of_the_hash[trailing-newline], tests/adapters/forge/replies/test_facts.py::test_a_marker_is_read_off_the_last_line_and_left_out_of_the_hash[summary], tests/adapters/forge/replies/test_facts.py::test_a_marker_is_read_off_the_last_line_and_left_out_of_the_hash[marker-not-on-the-last-line], tests/adapters/forge/replies/test_facts.py::test_a_marker_is_read_off_the_last_line_and_left_out_of_the_hash[none]
origin: src/kraft/adapters/forge/replies/facts.py
