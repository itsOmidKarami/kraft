# Intent: replies to the review bots

Kraft reading every note of a review bot's thread, and the forge reads and
writes, the body gate and the reply record that let it answer a bot in that
thread without posting twice, leaking a secret or arguing forever.

`enforced-by:` names the test that pins each requirement.

## REQ bot-feedback-reads-every-page
WHEN the review wait reads what the review bots said, the system SHALL read
every page of the merge request's comments, reviews, review threads, each
thread's comments, notes and discussions.
enforced-by: tests/adapters/forge/test_reviewer_feedback.py::test_every_page_of_bot_feedback_is_read[gh-comments], tests/adapters/forge/test_reviewer_feedback.py::test_every_page_of_bot_feedback_is_read[gh-reviews], tests/adapters/forge/test_reviewer_feedback.py::test_every_page_of_bot_feedback_is_read[gh-threads], tests/adapters/forge/test_reviewer_feedback.py::test_every_page_of_bot_feedback_is_read[gh-thread-comments], tests/adapters/forge/test_reviewer_feedback.py::test_every_page_of_bot_feedback_is_read[glab-notes], tests/adapters/forge/test_reviewer_feedback.py::test_every_page_of_bot_feedback_is_read[glab-discussions]
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

## REQ thread-reads-and-writes-exist-on-every-forge
The system SHALL read one review thread of a merge request, reply in it, resolve
it, read and write the merge request's top-level comments, delete a note and read
the login it writes as, on GitHub and GitLab, through the CLI and the API clients
alike, and SHALL NOT read a thread of another merge request as this one's.
enforced-by: tests/adapters/forge/test_thread_io.py::test_viewer_is_the_login_kraft_writes_as[gh], tests/adapters/forge/test_thread_io.py::test_viewer_is_the_login_kraft_writes_as[glab], tests/adapters/forge/test_thread_io.py::test_read_thread[gh], tests/adapters/forge/test_thread_io.py::test_read_thread[gh-another-pull-request], tests/adapters/forge/test_thread_io.py::test_read_thread[gh-not-a-thread], tests/adapters/forge/test_thread_io.py::test_read_thread[glab], tests/adapters/forge/test_thread_io.py::test_read_thread[glab-locked], tests/adapters/forge/test_thread_io.py::test_read_thread[glab-missing], tests/adapters/forge/test_thread_io.py::test_reply_to_thread_posts_on_the_first_comment[gh], tests/adapters/forge/test_thread_io.py::test_reply_to_thread_posts_on_the_first_comment[gh-body-starting-with-at], tests/adapters/forge/test_thread_io.py::test_reply_to_thread_posts_on_the_first_comment[glab], tests/adapters/forge/test_thread_io.py::test_resolve_thread[gh], tests/adapters/forge/test_thread_io.py::test_resolve_thread[glab], tests/adapters/forge/test_thread_io.py::test_mr_comments[gh], tests/adapters/forge/test_thread_io.py::test_mr_comments[glab], tests/adapters/forge/test_thread_io.py::test_upsert_comment[gh-create], tests/adapters/forge/test_thread_io.py::test_upsert_comment[gh-edit], tests/adapters/forge/test_thread_io.py::test_upsert_comment[glab-create], tests/adapters/forge/test_thread_io.py::test_upsert_comment[glab-edit], tests/adapters/forge/test_thread_io.py::test_delete_note[gh-thread], tests/adapters/forge/test_thread_io.py::test_delete_note[gh-comment], tests/adapters/forge/test_thread_io.py::test_delete_note[glab], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-viewer], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-read_thread], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-reply_to_thread], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-resolve_thread], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-mr_comments], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-upsert_comment], tests/adapters/forge/test_github_parity.py::test_every_github_method_answers_alike[github-delete_note], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[viewer], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[read_thread], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[reply_to_thread], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[resolve_thread], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[mr_comments], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[upsert_comment], tests/adapters/forge/test_forge_parity.py::test_every_forge_method_answers_alike[delete_note], tests/adapters/forge/test_forge_thread_contract.py::test_gh_thread_writes_against_a_real_pull_request, tests/adapters/forge/test_forge_thread_contract.py::test_glab_thread_writes_against_a_real_merge_request
origin: src/kraft/adapters/forge/models.py

## REQ agent-text-that-could-render-or-leak-is-refused
IF agent text bound for the forge holds a control or invisible character, an
image, an HTML comment or a Kraft marker, URL text, an escaped backtick or a token
shape anywhere, or Markdown link syntax, raw HTML, a character reference or a
backslash escape outside code, THEN the system SHALL refuse it.
enforced-by: tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[zero-width], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[bidi], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[bell], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[markdown-image], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[img-tag], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[html-comment], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[forged-marker], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[url], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[www], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[protocol-relative], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[markdown-link], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[link-reference], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[link-definition], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[autolink], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[url-in-a-span], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[a-bare-word-and-colon], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[a-type-annotation-in-a-span], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[index-chain-in-a-span], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[generic-in-a-span], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[comparison-in-a-fence], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[cr-only-link-definition], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[raw-html], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[escaped-backtick], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[fence-with-a-backtick-info], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[entity-in-a-fence], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[entity-in-a-span], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[entity-in-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[numeric-reference], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[backslash-escape], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[token-in-a-fence], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[sk-token], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[aws-key], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[pem-header]
origin: src/kraft/adapters/forge/replies/body.py

## REQ quotes-in-agent-text-are-verbatim-and-prose-is-capped
IF a code span, a code block or a block quote in agent text is not found verbatim
in a tracked file at the head or in a note of the thread, or the text holds more
than 1,500 bytes besides its verbatim quotes, THEN the system SHALL refuse it,
and SHALL count a quote of under 20 bytes, of any form, as prose, not as a quote.
enforced-by: tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[span-verbatim], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[span-not-verbatim], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[fence-verbatim], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[fence-not-verbatim], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[block-quote-of-a-note], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[block-quote-not-verbatim], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[indented-block-verbatim], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[prose-over-the-cap], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[quotes-are-not-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[many-short-spans-are-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[a-long-span-is-a-quote], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[many-short-block-quotes-are-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[many-one-line-fences-are-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[many-short-indented-blocks-are-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[a-20-byte-quote-counts], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[a-19-byte-quote-is-prose], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[in-a-line], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[absent], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[whole-lines-in-order], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[out-of-order], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[common-anchor-with-many-hits], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[all-blank-block], tests/adapters/forge/replies/test_body.py::test_repo_quotes_search_the_tracked_files_at_the_head[crlf-file]
origin: src/kraft/adapters/forge/replies/body.py

## REQ a-reply-never-carries-a-secret-kraft-knows
IF agent text holds, as written, or squeezed when it looks like a credential,
forwards or reversed, a value of 8 characters or more that is a named agent
credential (any shape from 16), a token Kraft's log redaction knows, or a worker
environment value that is not a baseline or Kraft session name and not plain,
THEN the system SHALL refuse it, and SHALL NOT refuse anything for a shorter value.
enforced-by: tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[secret-as-written], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[secret-reversed], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[secret-squeezed], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[hyphenated-config-value-in-prose], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[config-value-as-written], tests/adapters/forge/replies/test_body.py::test_the_gate_refuses_what_could_render_run_or_leak[config-value-reversed], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[named-short-placeholder], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[named-plain-word-8-to-15], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[named-16-plus-plain], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[named-real-key], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[passthrough], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[literal-env], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[plain-words-numbers-booleans-and-short], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[baseline], tests/adapters/forge/replies/test_body.py::test_the_secrets_kraft_knows[kraft-session-names], tests/adapters/forge/replies/test_body.py::test_registered_tokens_and_extras[a-registered-token], tests/adapters/forge/replies/test_body.py::test_registered_tokens_and_extras[a-short-extra], tests/adapters/forge/replies/test_body.py::test_registered_tokens_and_extras[a-long-extra], tests/adapters/forge/replies/test_body.py::test_a_short_or_empty_secret_refuses_nothing
origin: src/kraft/adapters/forge/replies/body.py

## REQ a-reply-is-neutralised-led-capped-signed-and-marked
WHEN Kraft builds a note from agent text that passed the gate, the system SHALL
normalise its line breaks, neutralise every `/` line and every mention or
reference sigil with a word joiner, redact long random-looking strings, put
Kraft's lead first, cut it to 60,000 bytes closing an open code fence, and end it
with Kraft's footer and the hidden marker.
enforced-by: tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[plain], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[line-breaks], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[slash-command], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[indented-slash], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[slash-in-code], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[mention], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[mention-underscore-and-dot], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[issue-and-mr-refs], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[label-milestone-snippet-epic], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[user-at-sha], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[iteration], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[vulnerability], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[jira-key], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[no-sigil-target], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[inside-code-too], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[high-entropy], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[a-token-with-a-key-shape-inside], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[a-full-sha-is-kept], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[an-identifier-is-kept], tests/adapters/forge/replies/test_body.py::test_the_note_is_neutralised_led_signed_and_marked[mixed-case-path], tests/adapters/forge/replies/test_body.py::test_a_note_too_long_is_cut_closing_its_fence_before_the_footer, tests/adapters/forge/replies/test_body.py::test_a_cut_never_splits_a_character
origin: src/kraft/adapters/forge/replies/body.py

## REQ a-reply-intent-is-recorded-before-its-write
WHEN Kraft is about to write a reply or the summary comment, the system SHALL
record its intent first, then each send that did not post, the post, a reconcile
that found nothing, its end and any duplicate deleted, and SHALL read an intent's
state from every event of the work item across retries.
enforced-by: tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[intended], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[unclear], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[not-found-is-pending-again], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[rate-limited-counts], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[deferred-does-not-count], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[reconciled], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[posted], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[refused], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[superseded-after-a-reconcile], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[given-up], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[send-began-is-unclear], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[began-then-posted], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[a-crash-found-absent], tests/adapters/forge/replies/test_record.py::test_the_record_spans_every_fork, tests/adapters/forge/replies/test_record.py::test_intend_refuses_an_unknown_op_or_a_missing_hash[unknown-op], tests/adapters/forge/replies/test_record.py::test_intend_refuses_an_unknown_op_or_a_missing_hash[no-body-hash], tests/adapters/forge/replies/test_record.py::test_posted_hashes_map_every_id_to_the_returned_hash
origin: src/kraft/adapters/forge/replies/record.py

## REQ forge-write-failures-are-classified
WHEN a write for a reply fails, the system SHALL treat no answer or a 5xx as
unclear, a rate limit as retryable after its wait, a CLI that never started, an
authentication, TLS or SSO failure or GitHub's one-pending-review refusal as
deferred, and every other refusal as final.
enforced-by: tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[cli-timeout], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[no-http-response], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[http-timeout], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[5xx], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[429], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[403-rate-limit], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[graphql-rate-limit], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[cli-never-started], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[401], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[tls], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[403-sso], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[not-signed-in], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[dns-failure], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[connection-refused], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[connection-timed-out], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[422-pending-review], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[422-other], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[400], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[403-forbidden], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[404], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[409], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[410], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[413], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[graphql-forbidden], tests/adapters/forge/replies/test_send.py::test_a_failed_write_is_classified[graphql-unknown], tests/adapters/forge/test_transport.py::test_an_error_names_its_errors_detail[errors-objects], tests/adapters/forge/test_transport.py::test_an_error_names_its_errors_detail[errors-strings], tests/adapters/forge/test_transport.py::test_an_error_names_its_errors_detail[message-only], tests/adapters/forge/test_http.py::test_a_call_is_bounded_and_its_error_stays_short[errors-detail], tests/adapters/forge/test_forge_thread_contract.py::test_gh_thread_writes_against_a_real_pull_request
origin: src/kraft/adapters/forge/replies/send.py

## REQ a-reply-has-at-most-three-counted-sends
WHEN Kraft sends a reply, the system SHALL send only a pending intent the gate
allows, SHALL count each unclear or rate-limited send and give the intent up after
three, SHALL wait out a rate limit's retry-after, and SHALL try a deferred intent
at most once every five minutes without counting it.
enforced-by: tests/adapters/forge/replies/test_send.py::test_counted_sends_and_backoff, tests/adapters/forge/replies/test_send.py::test_send_skips_an_intent_that_is_not_pending[unclear], tests/adapters/forge/replies/test_send.py::test_send_skips_an_intent_that_is_not_pending[posted], tests/adapters/forge/replies/test_send.py::test_send_skips_an_intent_that_is_not_pending[ended], tests/adapters/forge/replies/test_send.py::test_the_gate_ends_an_intent_it_will_not_send[refused], tests/adapters/forge/replies/test_send.py::test_the_gate_ends_an_intent_it_will_not_send[superseded], tests/adapters/forge/replies/test_send.py::test_a_send_records_its_outcome[posted], tests/adapters/forge/replies/test_send.py::test_a_send_records_its_outcome[unclear], tests/adapters/forge/replies/test_send.py::test_a_send_records_its_outcome[refused], tests/adapters/forge/replies/test_send.py::test_a_send_records_its_outcome[an-os-error-is-unclear]
origin: src/kraft/adapters/forge/replies/send.py

## REQ forge-writes-are-spaced-per-account
WHILE Kraft writes replies, the system SHALL start at most one write a second on
each forge account, across work items.
enforced-by: tests/adapters/forge/replies/test_send.py::test_writes_are_spaced_per_account[same-account-waits-a-second], tests/adapters/forge/replies/test_send.py::test_writes_are_spaced_per_account[another-account-does-not]
origin: src/kraft/adapters/forge/replies/send.py

## REQ a-send-cut-short-is-reconciled-not-resent
WHEN a send is about to write, the system SHALL first record it as unclear, so a
crash, cancel or restart before its outcome is recorded is reconciled before any
resend, and SHALL treat a failure that is not a forge error as unclear.
enforced-by: tests/adapters/forge/replies/test_send.py::test_a_crash_mid_send_leaves_the_intent_unclear, tests/adapters/forge/replies/test_send.py::test_a_send_records_its_outcome[an-os-error-is-unclear], tests/adapters/forge/replies/test_record.py::test_an_intent_s_state_is_folded_from_its_events[send-began-is-unclear]
origin: src/kraft/adapters/forge/replies/send.py

## REQ a-posting-point-stops-sending-after-its-time-budget
WHEN 60 seconds have passed since a posting point began, the system SHALL start
no further send there, leaving what is unsent for the next posting point.
enforced-by: tests/adapters/forge/replies/test_send.py::test_a_posting_point_stops_sending_after_its_time_budget
origin: src/kraft/adapters/forge/replies/send.py

## REQ an-unclear-write-is-reconciled-before-any-resend
WHEN a posting point begins and a write's outcome is unclear, the system SHALL
read where its copy would be and record it posted when a note the viewer wrote
carries its marker, and otherwise record it not found and leave any resend to the
gate, and SHALL count a marker on any other author's note as nothing.
enforced-by: tests/adapters/forge/replies/test_send.py::test_an_unclear_write_is_reconciled_by_its_marker[landed-with-a-lost-answer], tests/adapters/forge/replies/test_send.py::test_an_unclear_write_is_reconciled_by_its_marker[never-landed], tests/adapters/forge/replies/test_send.py::test_a_marker_on_another_authors_note_is_not_a_reconciled_copy, tests/adapters/forge/replies/test_send.py::test_a_failed_reconcile_read_leaves_the_intent_unclear, tests/adapters/forge/replies/test_send.py::test_a_summary_edit_is_reconciled_by_its_body_hash[edit-landed], tests/adapters/forge/replies/test_send.py::test_a_summary_edit_is_reconciled_by_its_body_hash[edit-did-not-land]
origin: src/kraft/adapters/forge/replies/send.py

## REQ a-duplicate-reply-keeps-the-earliest-copy
WHEN two notes the viewer wrote carry one intent's marker and the same body, the
system SHALL keep the earliest, delete the others and move the record to the one
it kept, at later posting points, within the posting point's time budget.
enforced-by: tests/adapters/forge/replies/test_send.py::test_a_duplicate_keeps_the_earliest_copy[the-record-moves-to-the-earliest], tests/adapters/forge/replies/test_send.py::test_a_duplicate_keeps_the_earliest_copy[the-record-holds-the-earliest], tests/adapters/forge/replies/test_send.py::test_the_sweep_reads_nothing_past_its_budget
origin: src/kraft/adapters/forge/replies/send.py

## REQ a-bot-reviewer-entry-takes-replies-and-resolve-fixed
WHERE a bot reviewer entry is named in a task's `config`, in `repos.yaml` or in a
node override, the system SHALL accept `replies` and `resolve_fixed` on it, leave
each out of the entry's dump when it is false, and refuse `resolve_fixed` without
`replies: true` on the same entry, in its own words. A check entry SHALL never
take either key, the old `resolve: kraft` SHALL be refused as an unknown key, and
the schema and the `--reviewer` flag (`bot:LOGIN[:replies[:resolve_fixed]]`) SHALL
offer both keys, the Settings repos editor keeping them through a save.
enforced-by: tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-bot-with-replies], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-bot-with-replies-off], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-bot-with-resolve-fixed], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-bot-with-resolve-needs-replies], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-bot-with-resolve-fixed-off], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-bot-with-the-old-resolve-key], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-check-with-replies], tests/adapters/forge/test_automated_review.py::test_a_reviewer_entry_is_a_bot_or_a_check[a-check-with-resolve-fixed], tests/adapters/forge/test_automated_review.py::test_the_schema_offers_replies_and_resolve, tests/adapters/forge/test_automated_review.py::test_repos_yaml_loads_a_bot_with_replies_and_resolve, tests/test_overrides.py::test_resolve_without_replies_is_refused_in_its_own_words, tests/test_overrides.py::test_replies_are_accepted_in_a_node_override, tests/cli/test_item_reviewers.py::test_a_node_gets_its_reviewers_from_the_terminal[a-bot-and-a-check], tests/cli/test_item_reviewers.py::test_a_node_gets_its_reviewers_from_the_terminal[none-sends-empty], tests/cli/test_item_reviewers.py::test_a_node_gets_its_reviewers_from_the_terminal[a-bot-with-replies], tests/cli/test_item_reviewers.py::test_a_node_gets_its_reviewers_from_the_terminal[a-bot-with-replies-and-resolve-fixed], tests/cli/test_item_reviewers.py::test_a_node_gets_its_reviewers_from_the_terminal[a-check-whose-name-holds-a-colon], tests/cli/test_item_reviewers.py::test_a_malformed_reviewer_flag_is_an_argument_error[not-a-kind], tests/cli/test_item_reviewers.py::test_a_malformed_reviewer_flag_is_an_argument_error[an-unknown-option], tests/cli/test_item_reviewers.py::test_a_malformed_reviewer_flag_is_an_argument_error[the-old-resolve-form], tests/drafts/test_repos.py::test_a_save_keeps_the_yaml_only_automated_review_as_written
origin: src/kraft/automated_review.py §BotReviewer -- Omid, 2026-11-22: only a confirmed fix is resolved, so the key is `resolve_fixed`, a boolean.

## REQ resolve-fixed-resolves-only-at-completion-from-a-fresh-read
WHERE a bot has `resolve_fixed` on, the system SHALL resolve a thread only at node
completion after the last reply round, never at a posting point that starts a
round, from a read of the thread and the merge request made then, and SHALL
re-read both at the send and refuse a thread on another head or a closed merge
request. A bot without `resolve_fixed` SHALL have no thread resolved.
enforced-by: tests/executor/test_resolve.py::test_a_confirmed_fix_the_bot_never_answers_is_resolved, tests/executor/test_resolve.py::test_resolve_only_runs_when_no_round_starts, tests/executor/test_resolve.py::test_resolve_fixed_off_resolves_nothing, tests/executor/test_resolve.py::test_only_the_bots_with_resolve_fixed_are_resolved, tests/executor/test_resolve.py::test_resolve_reads_the_thread_afresh, tests/executor/test_resolve.py::test_the_resolve_gate_checks_the_head_again, tests/executor/test_resolve.py::test_the_resolve_gate_refuses_a_closed_merge_request, tests/executor/test_resolve.py::test_resolve_with_a_stale_head_leaves_threads_open
origin: src/kraft/executor/replies.py §_Point.resolve -- Omid, 2026-11-22: resolve only at node completion, from a fresh read.

## REQ only-a-confirmed-fix-is-resolved
WHERE a bot has `resolve_fixed` on, the system SHALL resolve a thread only when
Kraft's latest note in it is a posted "Fixed in" reply, and SHALL NOT resolve a
thread Kraft declined or answered, however the bot answers. A later decline,
answer or pushback in a thread Kraft fixed SHALL leave it open, listed as
`not_fixed`, `finding_outstanding` or `answer_unread`.
enforced-by: tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[confirmed-fix], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[fix-after-a-decline], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[decline-only], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[answer-only], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[fix-then-a-decline], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[triage-replied-after-the-fix], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[note-the-record-lacks], tests/executor/test_resolve.py::test_a_decline_the_bot_never_answers_stays_open, tests/executor/test_resolve.py::test_a_decline_repeated_on_a_second_head_stays_open, tests/executor/test_resolve.py::test_an_answered_thread_stays_open, tests/executor/test_resolve.py::test_a_declined_thread_fixed_in_a_later_cycle_is_resolved, tests/executor/test_resolve.py::test_a_fix_the_triage_later_declines_stays_open, tests/executor/test_resolve.py::test_a_fix_the_bot_pushes_back_on_stays_open
origin: src/kraft/adapters/forge/replies/gate.py §resolve_refusal -- Omid, 2026-11-22: only a confirmed fix is resolved: a decline or an answer is a person's to resolve.

## REQ resolve-needs-the-latest-triage-to-have-seen-every-note
WHERE a thread is a candidate to be resolved, the system SHALL require a latest
triage on the current head and SHALL leave the thread open when a note Kraft did
not write was created after that triage read the bots, less a margin of five
seconds, comparing creation times only; Kraft's own notes SHALL be left out of
that test.
enforced-by: tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[bot-answered-after-the-read], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[skew-margin], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[kraft-note-after-the-read], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[no-latest-triage], tests/executor/test_resolve.py::test_a_bot_answer_nobody_read_is_not_resolved_over, tests/executor/test_replies.py::test_the_latest_triage_is_the_newest_that_ran[a-skip-after-the-verdict], tests/executor/test_replies.py::test_the_latest_triage_is_the_newest_that_ran[only-a-skip]
origin: src/kraft/adapters/forge/replies/gate.py §resolve_refusal

## REQ resolve-never-follows-a-reply-held-back-or-a-finding-outstanding
WHERE a thread is a candidate to be resolved, the system SHALL leave it open, with
the reason in the reply report, when a reply intent of the node run for it is held
back, when a finding of the run still names it (the last measurement's findings and
unmeasured ones, the judge's declined ones and the latest triage's own result file),
when a record of the run carries dropped findings, when a person wrote in it, when it
was not started by a bot with replies on, when the forge says the viewer cannot
resolve it or when Kraft's note in it is gone, and SHALL list a read that failed, a
thread that is gone and a closed merge request.
enforced-by: tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[later-cycle-superseded-with-a-later-fix-posted], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[cap-refused], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[deferred], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[unclear], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[given-up], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[superseded-fix], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[later-cycle-superseded-alone], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[another-run], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[finding-outstanding], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[findings-dropped], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[kraft-note-deleted], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[person-written], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[an-edited-kraft-note], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[not-bot-started], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[bot-posts-as-a-user], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[cannot-resolve], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[locked-but-resolvable], tests/executor/test_resolve.py::test_outstanding_reads_every_source[last-record-findings], tests/executor/test_resolve.py::test_outstanding_reads_every_source[last-record-unmeasured], tests/executor/test_resolve.py::test_outstanding_reads_every_source[stop-downgrade-verdict], tests/executor/test_resolve.py::test_outstanding_reads_every_source[latest-triage-result-file], tests/executor/test_resolve.py::test_outstanding_reads_every_source[dropped-record], tests/executor/test_resolve.py::test_every_refusal_is_listed_left_open[read-failed], tests/executor/test_resolve.py::test_every_refusal_is_listed_left_open[thread-gone], tests/executor/test_resolve.py::test_every_refusal_is_listed_left_open[merge-request-closed], tests/executor/test_resolve.py::test_a_thread_with_a_below_severity_finding_is_left_open
origin: src/kraft/adapters/forge/replies/gate.py §resolve_refusal -- Omid, 2026-11-22: nothing is resolved over a reply held back or a finding outstanding.

## REQ a-resolved-thread-is-recorded-and-never-resolved-twice
WHEN Kraft resolves a thread, the system SHALL record the resolve as an intent before
the write and as posted after it, list it in the reply report as `resolved`, and
send a pending resolve of the run again at the next posting point through the same
gate. It SHALL leave a thread the bot resolved alone with no intent and no entry,
SHALL NOT send a resolve twice, SHALL NOT search a resolve for a marker, and SHALL
leave a thread open that it resolved once in any run of the item, an unclear
send that landed included, and someone reopened.
enforced-by: tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[already-resolved], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[already-resolved-and-not-resolvable], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[reopened-after-kraft-resolved], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[reopened-unclear], tests/adapters/forge/replies/test_gate.py::test_resolve_refusal[reopened-after-an-unclear-send-that-landed], tests/adapters/forge/replies/test_record.py::test_intend_accepts_a_resolve_op, tests/executor/test_resolve.py::test_a_resolve_whose_answer_is_lost_is_not_sent_twice, tests/executor/test_resolve.py::test_a_pending_resolve_is_sent_at_the_next_posting_point, tests/executor/test_resolve.py::test_a_thread_kraft_resolved_is_not_resolved_again_after_a_retry, tests/executor/test_resolve.py::test_a_thread_the_bot_resolved_is_left_alone, tests/executor/test_resolve.py::test_a_resolve_intent_is_never_swept_for_a_marker, tests/executor/test_resolve.py::test_a_confirmed_fix_the_bot_never_answers_is_resolved
origin: src/kraft/executor/replies.py §_Point._send_resolve -- Omid, 2026-11-22: a resolve is an intent like a reply; a thread Kraft resolved once is never resolved again.

## REQ the-brief-says-what-kraft-resolved
WHEN a node with `resolve_fixed` completes, the review brief SHALL be told to name
each thread Kraft resolved because it confirmed the fix, and each thread left open
with its reason, `not_fixed` above all as an open thread a person must decide, and
SHALL call a comment still unresolved only when the report does not mark it resolved.
enforced-by: tests/skills/test_review_feedback.py::test_the_review_brief_says_what_kraft_resolved[resolved-entry], tests/skills/test_review_feedback.py::test_the_review_brief_says_what_kraft_resolved[not-fixed-is-open], tests/skills/test_review_feedback.py::test_the_review_brief_says_what_kraft_resolved[unresolved-only-when-not-marked], tests/executor/test_round_prompts.py::test_the_brief_is_told_where_the_triage_verdict_is[a-reply-report-beside-the-verdict], tests/executor/test_round_prompts.py::test_the_brief_is_told_where_the_triage_verdict_is[a-reply-report-alone]
origin: src/kraft/skills/review-brief/SKILL.md -- Omid, 2026-11-22: the brief says what Kraft resolved and what a person must decide.

## REQ a-finding-names-only-threads-on-its-own-file
WHEN a reviewer's result file names review-bot threads on a finding, the system
SHALL keep only the threads, of a bot with replies on, that were in the
collection that session was handed and whose file is the finding's file, compared as normalised repository-relative
paths, and SHALL NOT count them in the finding's identity.
enforced-by: tests/test_findings.py::test_a_finding_keeps_only_threads_on_its_own_file[another-file], tests/test_findings.py::test_a_finding_keeps_only_threads_on_its_own_file[not-collected], tests/test_findings.py::test_a_finding_keeps_only_threads_on_its_own_file[not-collected-on-a-pathless-finding], tests/test_findings.py::test_a_finding_keeps_only_threads_on_its_own_file[normalised], tests/test_findings.py::test_a_finding_keeps_only_threads_on_its_own_file[a-file-thread-on-a-pathless-finding], tests/test_findings.py::test_a_finding_reads_its_thread_ids[a-list]
origin: src/kraft/findings.py §on_their_files

## REQ head-wait-activity-leaves-out-the-conversation
WHERE a bot the review wait reads has replies on, the system SHALL NOT count as
the bot's activity, or hand on as its summary, any note of a thread that holds a
Kraft note, nor a review whose body is blank and whose every comment is a reply in
such a thread, unless it requests changes or approves, and SHALL NOT count a
change in the bot's unresolved threads that Kraft has posted in. With replies
off, the wait SHALL read activity as before.
enforced-by: tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-reply-in-a-kraft-thread], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-the-same-off], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-the-bot-s-own-review-still-counts], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-review-with-a-new-thread], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-a-changes-requested-reply-still-counts], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-marker-on-a-bot-note], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[glab-reply-in-a-kraft-discussion], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[glab-the-same-off], tests/adapters/forge/test_settle.py::test_a_thread_kraft_posted_in_leaves_the_compared_set[bot-resolved-a-kraft-thread], tests/adapters/forge/test_settle.py::test_a_thread_kraft_posted_in_leaves_the_compared_set[bot-resolved-another-thread]
origin: src/kraft/adapters/forge/settle.py §decide

## REQ a-thread-s-conversation-is-read-by-creation-time
WHERE a review bot has replies on, the system SHALL treat a thread as in the conversation
only while it is open, its first note is that bot's, no person has written in it and a Kraft
note stands in it. Such a thread SHALL be answered when a note by its first note's author
follows Kraft's latest note, SHALL have a deadline of Kraft's latest note's creation time plus
the reply timeout, SHALL be owed a look when answered after the latest triage read it or, while
unanswered, before its deadline, and SHALL be done when it is out of the conversation, answered
or past its deadline. Every one of these comparisons SHALL read creation times, never
`updated_at`.
enforced-by: tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[answered], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[bot-spoke-before-kraft], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[answered-by-someone-else], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[unanswered], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[person-wrote], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[no-kraft-note], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[resolved], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[first-note-by-a-bot-with-replies-off], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[an-edited-kraft-note], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[deadline-is-kraft-s-created-at-plus-the-timeout], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[a-second-kraft-note-moves-the-deadline], tests/adapters/forge/replies/test_owed.py::test_a_thread_in_the_conversation[updated-at-moved], tests/adapters/forge/replies/test_owed.py::test_owed_a_look[answered-after-read-at], tests/adapters/forge/replies/test_owed.py::test_owed_a_look[answered-before-read-at], tests/adapters/forge/replies/test_owed.py::test_owed_a_look[no-latest-triage-any-answer], tests/adapters/forge/replies/test_owed.py::test_owed_a_look[unanswered-before-the-deadline], tests/adapters/forge/replies/test_owed.py::test_owed_a_look[unanswered-at-the-deadline], tests/adapters/forge/replies/test_owed.py::test_owed_a_look[unanswered-after-the-deadline], tests/adapters/forge/replies/test_owed.py::test_a_thread_is_done_for_the_reply_wait[no-standing], tests/adapters/forge/replies/test_owed.py::test_a_thread_is_done_for_the_reply_wait[answered], tests/adapters/forge/replies/test_owed.py::test_a_thread_is_done_for_the_reply_wait[before-deadline], tests/adapters/forge/replies/test_owed.py::test_a_thread_is_done_for_the_reply_wait[after-deadline]
origin: src/kraft/adapters/forge/replies/owed.py -- Omid, 2026-11-22: the conversation ends by a deadline and Kraft never waits on a thread past it.

## REQ head-wait-activity-follows-the-reply-record
WHERE a thread's id is in the reply record as posted or unclear, the head wait SHALL leave
that thread's conversation out of the bot's activity and out of the threads it compares
between checks, whether or not a Kraft note still shows in it; and a thread a bot resolves
after Kraft first posted in it between two checks SHALL NOT count as activity.
enforced-by: tests/adapters/forge/test_settle.py::test_a_thread_kraft_posted_in_leaves_the_compared_set[bot-resolved-a-kraft-thread], tests/adapters/forge/test_settle.py::test_a_thread_kraft_posted_in_leaves_the_compared_set[bot-resolved-another-thread], tests/adapters/forge/test_settle.py::test_a_thread_kraft_posted_in_leaves_the_compared_set[posted-between-checks-then-resolved], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-kraft-note-deleted-record-remembers], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[gh-kraft-note-deleted-record-forgot], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[glab-kraft-note-deleted-record-remembers], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[glab-a-resolve-moves-updated-at], tests/adapters/forge/test_reviewer_feedback_replies.py::test_the_head_wait_leaves_out_the_conversation[glab-a-resolve-moves-updated-at-kraft-unknown], tests/adapters/forge/test_run_replies.py::test_a_bot_resolving_a_thread_kraft_replied_in_does_not_settle_the_wait, tests/adapters/forge/replies/test_record.py::test_thread_ids[posted], tests/adapters/forge/replies/test_record.py::test_thread_ids[unclear], tests/adapters/forge/replies/test_record.py::test_thread_ids[refused-excluded], tests/adapters/forge/replies/test_record.py::test_thread_ids[fixed-and-triage-both], tests/adapters/forge/replies/test_record.py::test_thread_ids[summary-excluded], tests/adapters/forge/test_run_reply_wait.py::test_a_bot_resolving_a_thread_after_fixed_in_does_not_extend_the_next_heads_wait
origin: src/kraft/adapters/forge/settle.py §_activity -- Omid, 2026-11-22: a bot answering Kraft is not the bot reviewing a new head.

## REQ the-reply-wait-waits-for-each-thread-s-answer-or-deadline
WHILE a node is in a reply round, the review wait SHALL wait only for the threads the round
was started for, and SHALL be done when each is answered, resolved, written in by a person,
or past its deadline, never for a thread outside the round. Its next check SHALL be no later
than the latest deadline among the threads still pending, held to the reply timeout from now;
a deadline SHALL settle the wait and SHALL NOT stop the item. It SHALL still push and read CI
when the task watches it, SHALL write the collection with the instant it read at, and SHALL
record no heard or silent bots. The timeout SHALL be the review task's own
`reply_timeout_minutes`.
enforced-by: tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[every-thread-answered], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[one-thread-silent], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[resolved-thread-is-done], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[person-wrote-is-done], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[past-the-deadline], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[latest-deadline-among-the-pending], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[a-reviewer-with-replies-off-is-ignored], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[an-unanswered-thread-outside-the-round-is-ignored], tests/adapters/forge/test_settle.py::test_the_reply_wait_is_done_when[forge-clock-ahead-clamps-the-deadline], tests/adapters/forge/test_run_reply_wait.py::test_the_reply_wait_waits_then_settles, tests/adapters/forge/test_run_reply_wait.py::test_the_reply_wait_ignores_the_push_deadline, tests/adapters/forge/test_run_reply_wait.py::test_the_reply_wait_records_no_settled_review, tests/adapters/forge/test_run_reply_wait.py::test_the_reply_wait_writes_the_collection_with_read_at, tests/adapters/forge/test_run_reply_wait.py::test_the_reply_wait_still_pushes_and_reads_ci_with_watch_ci, tests/adapters/forge/test_run_reply_wait.py::test_an_ordinary_wait_never_waits_for_a_reply_deadline, tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[open-round], tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[no-round], tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[another-node-s-round], tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[a-record-written-after-it], tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[another-head], tests/executor/test_dispatch_reply.py::test_the_reply_timeout_is_the_review_tasks[node-default], tests/executor/test_dispatch_reply.py::test_the_reply_timeout_is_the_review_tasks[node-level], tests/executor/test_dispatch_reply.py::test_the_reply_timeout_is_the_review_tasks[task-override], tests/executor/test_dispatch_reply.py::test_a_task_that_is_not_the_review_wait_gets_no_reply
origin: src/kraft/adapters/forge/settle.py §reply_wait -- Omid, 2026-11-20: we need the timeout, we can't just move on, and we don't want to be stuck here either.

## REQ the-viewer-is-read-only-when-there-is-work
WHERE no reviewer of a node's review wait has replies on, the system SHALL make
no forge call for replies. A collecting wait with such a reviewer SHALL read the
login Kraft writes as once and keep it for the wait, and mark each collected note
Kraft wrote as Kraft's. An unreadable viewer SHALL post nothing at that posting
point. A completing posting point SHALL read the forge to decide a reply round only
when the record holds a posted reply of the node.
enforced-by: tests/adapters/forge/test_run_replies.py::test_a_replies_on_wait_reads_the_viewer_once_and_marks_kraft_notes, tests/adapters/forge/test_run_replies.py::test_a_replies_off_wait_never_reads_the_viewer, tests/executor/test_replies.py::test_an_unreadable_viewer_posts_nothing_until_it_reads, tests/executor/test_reply_round_walks.py::test_the_owed_check_makes_no_forge_call_without_a_posted_reply, tests/executor/test_reply_round_walks.py::test_replies_off_starts_no_round_and_reads_nothing
origin: src/kraft/adapters/forge/run.py §_collect_review

## REQ the-reply-limits-are-policy-keys-with-code-defaults
WHERE a repository has a review bot with replies on, the system SHALL read three
policy keys, `reply_cap_per_thread` (2), `reply_rounds_per_head` (2) and
`reply_timeout_minutes` (5), from the node's resolved policy; a key no layer sets, in
`policy.yaml`, a chain or an item's stored snapshot, SHALL read its code default. Each
SHALL be a positive integer, SHALL stay within its administrator maximum, and the two
node keys SHALL be refused on a scope that is not an execution node.
enforced-by: tests/test_policy_reply_limits.py::test_the_reply_keys_have_code_defaults[code-defaults], tests/test_policy_reply_limits.py::test_the_reply_keys_have_code_defaults[a-default-moves-it], tests/test_policy_reply_limits.py::test_the_reply_keys_have_code_defaults[maximum-holds-the-code-default], tests/test_policy_reply_limits.py::test_a_reply_default_above_its_maximum_is_rejected[reply-cap], tests/test_policy_reply_limits.py::test_a_reply_default_above_its_maximum_is_rejected[reply-rounds], tests/test_policy_reply_limits.py::test_a_reply_default_above_its_maximum_is_rejected[reply-timeout], tests/test_policy_reply_limits.py::test_an_override_above_the_administrator_maximum_is_refused[reply-cap], tests/test_policy_reply_limits.py::test_an_override_above_the_administrator_maximum_is_refused[reply-rounds], tests/test_policy_reply_limits.py::test_an_override_above_the_administrator_maximum_is_refused[reply-timeout], tests/test_policy_reply_limits.py::test_a_node_only_key_is_refused_below_a_node[task-refuses-cap], tests/test_policy_reply_limits.py::test_a_node_only_key_is_refused_below_a_node[template-accepts-cap], tests/test_policy_reply_limits.py::test_a_node_only_key_is_refused_below_a_node[task-accepts-timeout], tests/test_policy_reply_limits.py::test_a_reply_key_is_a_positive_integer[zero], tests/test_policy_reply_limits.py::test_a_reply_key_is_a_positive_integer[none-string], tests/test_policy_reply_limits.py::test_a_reply_key_is_a_positive_integer[negative], tests/test_policy_reply_limits.py::test_a_reply_key_is_a_positive_integer[float], tests/test_policy_reply_limits.py::test_the_meet_takes_the_smallest_reply_value, tests/templates/test_item_policy.py::test_a_reply_key_on_an_item_path[step-refused], tests/templates/test_item_policy.py::test_a_reply_key_on_an_item_path[execution-node-accepted], tests/templates/test_item_policy.py::test_a_snapshot_stored_before_the_reply_keys_loads, tests/templates/test_revision.py::test_an_override_stays_within_the_administrator_maxima[attempts], tests/addons/test_update_reply_limits.py::test_an_addon_may_set_a_reply_limit[reply-timeout], tests/addons/test_update_reply_limits.py::test_an_addon_may_set_a_reply_limit[reply-cap], tests/executor/test_replies.py::test_the_triage_cap_is_the_nodes_policy_key[the-nodes-key], tests/executor/test_replies.py::test_the_triage_cap_is_the_nodes_policy_key[no-snapshot-reads-the-code-default], tests/drafts/test_policy.py::test_a_reply_limit_is_edited_under_defaults_and_maxima[defaults-reply-cap-per-thread], tests/drafts/test_policy.py::test_a_reply_limit_is_edited_under_defaults_and_maxima[defaults-reply-rounds-per-head], tests/drafts/test_policy.py::test_a_reply_limit_is_edited_under_defaults_and_maxima[defaults-reply-timeout-minutes], tests/drafts/test_policy.py::test_a_reply_limit_is_edited_under_defaults_and_maxima[maxima-reply-cap-per-thread], tests/drafts/test_policy.py::test_a_reply_limit_is_edited_under_defaults_and_maxima[maxima-reply-rounds-per-head], tests/drafts/test_policy.py::test_a_reply_limit_is_edited_under_defaults_and_maxima[maxima-reply-timeout-minutes], tests/drafts/test_policy.py::test_a_reply_limit_under_a_level_is_refused, tests/api/test_item_policy.py::test_the_reply_keys_on_an_item_policy[stored-and-read-back], tests/api/test_item_policy.py::test_the_reply_keys_on_an_item_policy[step-path-refused], tests/api/test_item_policy.py::test_the_reply_keys_on_an_item_policy[over-maximum-refused], tests/api/test_item_policy.py::test_the_reply_keys_on_an_item_policy[timeout-on-a-task-path], tests/cli/test_verbs.py::test_an_items_own_policy_is_set_at_create_and_replaced_by_set_policy
origin: src/kraft/policy.py §InstancePolicy -- Omid, 2026-11-22: the reply limits are policy keys with code defaults.

## REQ the-per-thread-cap-counts-across-retries
WHEN a person retries an item whose Kraft triage replies in a thread have reached the
cap, the system SHALL still count every reply of the item's life in that thread and
refuse another.
enforced-by: tests/adapters/forge/replies/test_gate.py::test_the_cap_survives_a_retry, tests/adapters/forge/replies/test_record.py::test_the_record_spans_every_fork
origin: src/kraft/adapters/forge/replies/record.py §read

## REQ a-triage-reply-respects-the-cap-and-kraft-s-last-word
IF a review-bot thread is resolved, locked, not started by a bot with replies on,
or holds a note that is neither a configured bot's nor Kraft's unedited one, THEN
the system SHALL post no reply in it; and it SHALL post no triage reply when
Kraft's note is the thread's latest, or when Kraft's triage replies in the thread
over the item's life, deleted, unclear and unrecorded marked ones included, have
reached the cap, which is the node's `reply_cap_per_thread` policy key.
enforced-by: tests/adapters/forge/replies/test_gate.py::test_the_gate_reads_the_thread[a-person-wrote-in-it], tests/adapters/forge/replies/test_gate.py::test_the_gate_reads_the_thread[a-kraft-note-edited], tests/adapters/forge/replies/test_gate.py::test_the_gate_reads_the_thread[the-bot-from-a-user-account], tests/adapters/forge/replies/test_gate.py::test_the_gate_reads_the_thread[started-by-a-bot-with-replies-off], tests/adapters/forge/replies/test_gate.py::test_the_gate_reads_the_thread[locked], tests/adapters/forge/replies/test_gate.py::test_a_triage_reply_waits_its_turn[after-kraft], tests/adapters/forge/replies/test_gate.py::test_a_triage_reply_waits_its_turn[at-the-cap], tests/adapters/forge/replies/test_gate.py::test_a_triage_reply_waits_its_turn[an-unclear-send-counts], tests/adapters/forge/replies/test_gate.py::test_a_triage_reply_waits_its_turn[marked-notes-the-record-lost], tests/adapters/forge/replies/test_gate.py::test_a_triage_reply_waits_its_turn[a-fix-reply-does-not-count], tests/adapters/forge/replies/test_gate.py::test_a_triage_reply_waits_its_turn[cap-three-allows-a-third]
origin: src/kraft/adapters/forge/replies/gate.py §triage_refusal

## REQ replies-work-only-where-a-fix-loop-can-act
WHERE a node's review wait names a bot with replies on, but the node has no fix
loop or no triage among its own steps, or the item has several repositories, the
system SHALL post nothing for it and SHALL record why once per node run; it SHALL
do the same when the login Kraft writes as is one of the configured bots. A node
whose review wait names no bot with replies on SHALL make no forge call for
replies and record nothing.
enforced-by: tests/executor/test_replies.py::test_replies_have_no_effect_where_nothing_can_act[no-fix-loop], tests/executor/test_replies.py::test_replies_have_no_effect_where_nothing_can_act[no-triage], tests/executor/test_replies.py::test_replies_have_no_effect_where_nothing_can_act[the-viewer-is-the-bot], tests/executor/test_replies.py::test_posting_point_has_no_effect_on_a_multi_repo_item_or_with_no_forge[multi-repo], tests/executor/test_replies.py::test_posting_point_has_no_effect_on_a_multi_repo_item_or_with_no_forge[no-forge], tests/executor/test_replies.py::test_replies_off_posts_nothing_and_asks_the_forge_nothing, tests/executor/test_replies.py::test_an_unreadable_viewer_posts_nothing_until_it_reads
origin: src/kraft/executor/replies.py §_post -- Omid, 2026-11-20: on a node without a fix loop, replies have no effect.

## REQ triage-replies-come-from-the-latest-triage
WHEN Kraft posts a triage's replies, the system SHALL take them only from the
node's latest triage in its current run that started an agent and finished with a
result, SHALL NOT take any from a skipped triage, SHALL supersede a reply of an
earlier session, SHALL refuse one whose text the latest triage's result no longer
holds, and SHALL NOT post a reply in a thread the same triage raised as a
finding.
enforced-by: tests/executor/test_replies.py::test_a_reply_never_goes_beside_a_finding_on_the_same_thread, tests/executor/test_replies.py::test_the_latest_triage_is_the_newest_that_ran[a-skip-after-the-verdict], tests/executor/test_replies.py::test_the_latest_triage_is_the_newest_that_ran[a-failure-after-the-verdict], tests/executor/test_replies.py::test_the_latest_triage_is_the_newest_that_ran[before-the-run-began], tests/executor/test_replies.py::test_a_triage_reply_is_refused_or_superseded[a-thread-not-collected], tests/executor/test_replies.py::test_a_triage_reply_is_refused_or_superseded[a-newer-triage-ran], tests/executor/test_replies.py::test_a_triage_reply_is_refused_or_superseded[the-text-is-gone]
origin: src/kraft/executor/replies.py §latest_triage

## REQ a-triage-reply-is-posted-only-on-a-current-head
WHEN Kraft is about to post a triage reply, IF the triage session's head, its
collection's head, the checkout's head and the forge's head of the merge request
are not one commit, or the thread holds a bot's or a person's note the triage
never read, or the reply's rendered text is no longer the one recorded at its
intent, THEN the system SHALL supersede it and never post it; and IF the merge
request is not open, THEN the system SHALL refuse it.
enforced-by: tests/executor/test_replies.py::test_a_triage_reply_is_refused_or_superseded[the-forge-head-moved], tests/executor/test_replies.py::test_the_thread_moved_since_the_triage_read_it, tests/executor/test_replies.py::test_a_triage_reply_is_refused_or_superseded[the-mr-is-closed], tests/executor/test_replies.py::test_a_triage_reply_is_refused_or_superseded[the-result-file-changed]
origin: src/kraft/executor/replies.py §_Point.stale

## REQ a-reply-is-intended-once-whatever-the-wakes
WHEN a node's measurement returns again and again on the same head (a parked wait
woken), the system SHALL create one intent per reply and post it at most once: a
write whose answer was lost SHALL be reconciled at a later posting point, not
sent again, and a copy that lands late SHALL be found and deleted at a later
posting point. A reply still pending SHALL be posted at the posting point after a
recovery re-measure, and at each completion of the node (a clean recovery, a
clean measure, a judge's downgrade), since a reply unposted at completion is
dropped.
enforced-by: tests/executor/test_replies.py::test_a_triage_reply_is_posted_once_across_wakes, tests/executor/test_replies.py::test_a_lost_write_is_reconciled_not_sent_again, tests/executor/test_replies.py::test_a_late_copy_of_an_unclear_write_is_deleted, tests/executor/test_replies.py::test_a_pending_reply_is_posted_at_each_posting_point_that_walk_reaches[after-the-on-failure-repair-and-before-the-fix-cycle], tests/executor/test_replies.py::test_a_pending_reply_is_posted_at_each_posting_point_that_walk_reaches[the-repair-settles-it-and-the-node-completes], tests/executor/test_replies.py::test_a_pending_reply_is_posted_at_each_posting_point_that_walk_reaches[the-judge-downgrades-what-is-left]
origin: src/kraft/executor/replies.py §_Point.intend

## REQ a-fix-reply-is-posted-only-once-confirmed
WHEN a fix cycle's repair writes a reply for a review-bot thread, the system
SHALL post "Fixed in" only once the latest triage on the cycle's commit saw the
thread and wrote nothing for it, none of its findings has the finding's identity,
the commit changed the thread's file, the commit is both the newest push since
the cycle and the forge's head of an open merge request, the thread is still
collected, no later fix cycle started, and no "Fixed in" went to that thread for
that finding before; it SHALL wait while the commit is unpushed or no triage ran
on it, SHALL supersede the reply otherwise, including when the node completes
with it unconfirmed or unpushed and no reply round starts there (a reply still
waiting SHALL stay waiting through a round's start), and SHALL post the triage's reply instead when
that triage wrote one in the thread. With replies off, a finding SHALL carry no
thread and the fix loop SHALL be as before.
enforced-by: tests/executor/test_replies_fix.py::test_the_fix_verdict[confirmed], tests/executor/test_replies_fix.py::test_the_fix_verdict[another-head-pushed-since], tests/executor/test_replies_fix.py::test_the_fix_verdict[not-pushed-yet], tests/executor/test_replies_fix.py::test_the_fix_verdict[not-pushed-at-completion], tests/executor/test_replies_fix.py::test_the_fix_verdict[a-later-cycle], tests/executor/test_replies_fix.py::test_the_fix_verdict[no-triage-on-the-commit-yet], tests/executor/test_replies_fix.py::test_the_fix_verdict[unconfirmed-at-completion], tests/executor/test_replies_fix.py::test_the_fix_verdict[left-the-collection], tests/executor/test_replies_fix.py::test_the_fix_verdict[a-bogus-same-as], tests/executor/test_replies_fix.py::test_a_fix_reply_waits_for_the_triage_on_its_commit[the-merge-request-closed], tests/executor/test_replies_fix.py::test_threads_from_a_replies_off_bot_change_nothing, tests/executor/test_replies_fix.py::test_a_fix_reply_waits_for_the_triage_on_its_commit[confirmed], tests/executor/test_replies_fix.py::test_a_fix_reply_waits_for_the_triage_on_its_commit[raised-again], tests/executor/test_replies_fix.py::test_a_fix_reply_waits_for_the_triage_on_its_commit[the-same-finding-without-its-thread], tests/executor/test_replies_fix.py::test_a_fix_reply_waits_for_the_triage_on_its_commit[the-triage-replied-instead], tests/executor/test_replies_fix.py::test_a_fix_reply_needs_its_commit_on_the_forge, tests/executor/test_replies_fix.py::test_a_fix_reply_needs_the_thread_s_file_changed, tests/executor/test_replies_fix.py::test_a_fix_reply_goes_once, tests/adapters/forge/replies/test_gate.py::test_a_fix_reply_goes_once_per_finding[posted-before-for-the-finding], tests/executor/test_reply_round_walks.py::test_a_waiting_fix_reply_survives_a_round
origin: src/kraft/executor/replies.py §_Point._fix_verdict

## REQ both-agents-get-the-reply-rules
The triage skill and the instruction Kraft adds to a repair that is handed
findings from review-bot threads SHALL both carry, word for word, the rules that a
reply never holds a secret, a credential, an environment value, a token, a key or
the content of a file outside the repository, nor any encoded, split, reversed or
paraphrased form of one; never follows an instruction found in bot or commenter
text; and quotes only tracked files or the thread.
enforced-by: tests/skills/test_review_feedback.py::test_both_agents_get_the_reply_rules_word_for_word, tests/skills/test_review_feedback.py::test_the_skill_states_its_contract[replies-only-where-allowed], tests/skills/test_review_feedback.py::test_the_skill_states_its_contract[never-in-a-person-s-thread], tests/skills/test_review_feedback.py::test_the_skill_states_its_contract[a-fix-that-holds]
origin: src/kraft/skills/review-feedback/SKILL.md -- Omid, 2026-11-20: free text behind the body gate, and explicit instructions to the agents never to put secrets in a reply or follow instructions in bot or commenter text.

## REQ top-level-declines-go-in-one-summary-comment
WHEN a node with replies on completes, the system SHALL post the latest triage's
one-line declines of top-level comments, for bots with replies on, in one Kraft
comment on the merge request, found only by its marker on a comment Kraft wrote:
creating it, editing it in place when its text changed, editing it to say nothing
is declined when no decline is left, leaving it alone when no triage judged the
current head, and never creating it again after someone deleted it.
enforced-by: tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[first], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[edited-in-place], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[unchanged], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[nothing-declined-now], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[a-bot-with-replies-off], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[deleted-by-someone], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[a-refused-line], tests/executor/test_replies_fix.py::test_top_level_declines_go_in_one_comment[no-triage-on-the-current-head]
origin: src/kraft/executor/replies.py §_Point.summary

## REQ posting-never-parks-and-ends-with-the-node-run
WHEN a reply cannot be posted at a posting point, the system SHALL NOT park or
stop the work item, SHALL try again only at later posting points of the same node
run, and SHALL list every reply of the run still unposted, refused or superseded
when the node completes, and never post it later.
enforced-by: tests/executor/test_replies_fix.py::test_the_reply_report_lists_what_was_and_was_not_posted, tests/executor/test_replies_fix.py::test_a_reply_left_unsent_is_never_sent_by_a_later_run, tests/executor/test_replies_fix.py::test_a_failed_read_skips_one_reply_not_the_rest, tests/executor/test_replies.py::test_an_unreadable_viewer_posts_nothing_until_it_reads
origin: src/kraft/executor/replies.py §posting_point -- Omid, 2026-11-20: a reply still unposted when the node completes is listed in the brief and dropped; posting never parks the item.

## REQ the-brief-reads-the-reply-report
WHEN a node with replies on completes, the system SHALL write what it posted and
did not, every finding from a bot thread that never reached a fix, each thread still owed an
answer at the reply-round limit or unread, and a CI look still pending when a reply
round ended, each thread resolved or left open with its reason, to a
report the review brief is pointed at, built into its section whenever the
report lists anything, and SHALL remove its own node's report when the node
completes with replies off.
enforced-by: tests/executor/test_replies_fix.py::test_the_reply_report_lists_what_was_and_was_not_posted, tests/executor/test_replies_fix.py::test_a_fix_with_no_reply_is_reported, tests/executor/test_replies_fix.py::test_a_finding_still_raised_after_its_cycle_is_not_fixed, tests/executor/test_replies_fix.py::test_a_completion_with_replies_off_removes_the_report[this-node-s], tests/executor/test_replies_fix.py::test_a_completion_with_replies_off_removes_the_report[another-node-s], tests/executor/test_round_prompts.py::test_the_brief_is_told_where_the_triage_verdict_is[a-reply-report-beside-the-verdict], tests/executor/test_round_prompts.py::test_the_brief_is_told_where_the_triage_verdict_is[a-reply-report-alone], tests/skills/test_review_feedback.py::test_the_review_brief_is_told_to_report_what_the_triage_declined, tests/executor/test_reply_round_walks.py::test_an_owed_at_limit_entry_carries_the_unread_answer
origin: src/kraft/executor/replies.py §_Point.write_report

## REQ a-thread-is-owed-a-look-after-kraft-s-reply
WHEN a node with a fix loop and replies on is about to complete, the system SHALL
treat a thread Kraft ever posted a reply of this node in, on whatever head, as owed a
look when a configured bot answered it after the latest triage read the bots, or has
not answered and is not past the review task's `reply_timeout_minutes`, and SHALL
NOT treat a resolved thread or one a person wrote in as owed. A thread answered
after Kraft's last reply SHALL be owed even at the per-thread cap, since the
triage is to judge the answer. The decision SHALL read each candidate once with the
forge inside the posting point's time budget and SHALL make no forge call when the
record holds no posted reply of the node. WHERE the viewer, the merge request or a
thread cannot be read, the merge request is not open or the budget is spent, the
thread SHALL NOT be owed, the node SHALL complete without parking, and the report SHALL
list a thread it could not read as `not_read`.
enforced-by: tests/executor/test_reply_round_walks.py::test_an_answer_at_the_cap_is_read_and_judged_not_answered, tests/executor/test_reply_round_walks.py::test_a_reply_posted_on_one_head_is_read_on_the_next, tests/executor/test_reply_round_walks.py::test_the_owed_check_makes_no_forge_call_without_a_posted_reply, tests/executor/test_reply_round_walks.py::test_replies_off_starts_no_round_and_reads_nothing, tests/executor/test_reply_round_walks.py::test_the_owed_check_reads_the_review_tasks_timeout, tests/executor/test_reply_round_walks.py::test_an_unreadable_viewer_closed_request_or_failed_read_is_not_owed[viewer-unreadable], tests/executor/test_reply_round_walks.py::test_an_unreadable_viewer_closed_request_or_failed_read_is_not_owed[merge-request-closed], tests/executor/test_reply_round_walks.py::test_an_unreadable_viewer_closed_request_or_failed_read_is_not_owed[read-thread-raises]
origin: src/kraft/executor/replies.py §_Point.round -- Omid, 2026-11-20: we need the timeout and we can't just move on, but also we don't want to be stuck here either.

## REQ a-reply-round-runs-instead-of-completing-up-to-the-limit
WHEN a thread is owed a look at a completion exit of a node with a fix loop, and
fewer than `reply_rounds_per_head` rounds have started on the head in the node's
current run, the system SHALL record `reply_round_started` (the head, the round, the
threads and a deadline of the reply timeout from now) together with the cursor at the
node's first step, in one transaction, and SHALL measure the node again on that head
at the loop counter's count instead of completing. The decision SHALL come after the
posting point has posted and before a waiting fix reply is ended, the summary is
posted or the report is written, and an error in it SHALL complete the node. A new
head SHALL never be in a reply round, and a round SHALL start neither at the loopless
exit nor on a node with no fix loop. A round SHALL NOT reuse the earlier
measurement's sessions, and a crash inside it SHALL resume it without a second event.
A round whose measurement fails SHALL take the node's recovery while that is unspent,
and one that follows a fix cycle SHALL enter the fix loop. Reaching the limit SHALL
complete the node.
enforced-by: tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[open], tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[record-written], tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[other-head], tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[earlier-run], tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[another-node], tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[second-round], tests/executor/test_reply_round.py::test_in_a_reply_round_reads_the_event_the_record_and_the_head[no-event-never-asks-for-the-head], tests/executor/test_reply_round.py::test_started_count_is_per_node_head_and_run[two-on-one-head], tests/executor/test_reply_round.py::test_started_count_is_per_node_head_and_run[another-head], tests/executor/test_reply_round.py::test_started_count_is_per_node_head_and_run[earlier-run], tests/executor/test_reply_round.py::test_started_count_is_per_node_head_and_run[another-node], tests/executor/test_reply_round.py::test_begin_writes_the_event_and_the_cursor_together, tests/executor/test_reply_round_walks.py::test_a_silent_bot_gets_one_reply_round_then_the_node_completes, tests/executor/test_reply_round_walks.py::test_a_reply_round_runs_on_every_shape_that_takes_replies[shipped], tests/executor/test_reply_round_walks.py::test_a_reply_round_runs_on_every_shape_that_takes_replies[no-early], tests/executor/test_reply_round_walks.py::test_a_reply_round_runs_on_every_shape_that_takes_replies[ci-first], tests/executor/test_reply_round_walks.py::test_a_round_starts_from_the_exit_after_a_passing_on_failure, tests/executor/test_reply_round_walks.py::test_a_round_starts_from_the_judge_s_stop_downgrade_exit, tests/executor/test_reply_round_walks.py::test_a_bot_answer_is_judged_in_the_same_run[answer-then-another-decline], tests/executor/test_reply_round_walks.py::test_a_bot_answer_is_judged_in_the_same_run[answer-the-triage-accepts], tests/executor/test_reply_round_walks.py::test_the_per_head_limit_ends_the_conversation, tests/executor/test_reply_round_walks.py::test_a_waiting_fix_reply_survives_a_round, tests/executor/test_reply_round_walks.py::test_a_node_with_no_fix_loop_never_starts_a_round, tests/executor/test_reply_round_walks.py::test_an_error_in_the_round_decision_still_completes_the_node, tests/executor/test_reply_round_rules.py::test_a_round_started_after_a_doubted_repair_does_not_re_serve_the_doubts, tests/executor/test_reply_round_sequences.py::test_a_crash_inside_a_reply_round_resumes_it, tests/executor/test_reply_round_sequences.py::test_a_red_pipeline_in_a_reply_round_takes_the_node_s_recovery, tests/executor/test_reply_round_sequences.py::test_after_a_fix_cycle_a_red_round_goes_straight_to_the_fix_loop, tests/executor/test_reply_round_sequences.py::test_a_new_head_is_never_in_a_reply_round, tests/executor/test_reply_round_sequences.py::test_a_cap_of_three_allows_a_third, tests/store/test_sessions_reuse.py::test_a_reply_round_barrier_excludes_earlier_sessions[before-the-event-excluded], tests/store/test_sessions_reuse.py::test_a_reply_round_barrier_excludes_earlier_sessions[after-the-event-reusable], tests/store/test_sessions_reuse.py::test_a_reply_round_barrier_excludes_earlier_sessions[another-node-s-event-leaves-it]
origin: src/kraft/executor/reply_round.py -- Omid, 2026-11-20: the exchange always ends, and posting never parks or stops an item.

## REQ a-reply-round-s-triage-reads-only-what-is-new
WHILE a node is in a reply round, a task that declares `bot_feedback` SHALL start
an agent only when a configured bot's note created after the latest triage's `read_at`
stands in a thread the round was started for, or when no triage has read the bots in
the node's run, and SHALL NOT start it for an edit, a summary, another thread, a
person's note or Kraft's own. A triage skipped in a round SHALL be an unmeasured
task of the round's record, never a triage that found nothing, and SHALL keep the
findings an earlier measurement had from it. Outside a round the triage's own skip
rule SHALL be unchanged.
enforced-by: tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[new-bot-note-runs], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[answer-before-the-last-read-skips], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[edit-only-skips], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[other-thread-skips], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[no-latest-triage-runs], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[a-person-note-skips], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[kraft-note-only-skips], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[outside-a-round-part-1-still-runs-on-a-thread], tests/executor/test_agent_inputs.py::test_bot_feedback_in_a_reply_round[a-round-naming-no-thread-skips], tests/executor/test_measurement.py::test_a_skipped_triage_in_a_reply_round_is_an_unmeasured_task[reply-round-skip], tests/executor/test_measurement.py::test_a_skipped_triage_in_a_reply_round_is_an_unmeasured_task[ordinary-skip], tests/executor/test_measurement.py::test_a_skipped_triage_in_a_reply_round_is_an_unmeasured_task[reply-round-real-session], tests/executor/test_measurement.py::test_a_skipped_triage_in_a_reply_round_is_an_unmeasured_task[reply-round-non-triage-task-with-no-head], tests/executor/test_reply_round_rules.py::test_a_reply_round_record_keeps_the_earlier_triage_findings_as_unmeasured
origin: src/kraft/executor/round_prompts.py §bot_feedback_source

## REQ a-reply-round-s-record-pairs-no-fix
WHEN a measurement is made in a reply round, the system SHALL mark its
`findings_measured` event `reply_round: true`, SHALL keep the mark when the record
is amended, and SHALL give a marked record no fix when the judge's history is read, so
no stall or regression is read across it.
enforced-by: tests/executor/test_reply_round_rules.py::test_a_reply_round_record_pairs_no_fix[marked-pairs-none], tests/executor/test_reply_round_rules.py::test_a_reply_round_record_pairs_no_fix[unmarked-pairs-the-fix], tests/executor/test_reply_round_rules.py::test_a_reply_round_record_pairs_no_fix[unmarked-with-fix-is-stuck], tests/executor/test_reply_round_rules.py::test_a_reply_round_record_pairs_no_fix[stuck-fingerprint-ignores-the-marked-record], tests/executor/test_reply_round_rules.py::test_a_reply_round_record_pairs_no_fix[unmarked-regresses], tests/executor/test_reply_round_rules.py::test_a_reply_round_record_pairs_no_fix[regressed-ignores-the-marked-record], tests/executor/test_reply_round_rules.py::test_an_amend_keeps_the_reply_round_mark
origin: src/kraft/executor/dispatch.py §judge_history

## REQ a-reply-round-honours-every-decline-on-its-head
WHILE a node is in a reply round, every `stop_downgrade` verdict of the node's run
on the head SHALL count as declined, whether or not its measurement ended early and
on every node shape, so the round SHALL NOT collect, fix or judge again what the judge
declined.
enforced-by: tests/executor/test_reply_round_rules.py::test_declined_fingerprints_every[ended-early-counts-as-before], tests/executor/test_reply_round_rules.py::test_declined_fingerprints_every[not-ended-early-ignored-by-default], tests/executor/test_reply_round_rules.py::test_declined_fingerprints_every[not-ended-early-counts-with-every], tests/executor/test_reply_round_rules.py::test_declined_fingerprints_every[another-head-never], tests/executor/test_reply_round_rules.py::test_declined_fingerprints_every[another-node-never], tests/executor/test_reply_round_rules.py::test_a_reply_round_does_not_collect_what_the_judge_declined_on_the_head[loop-early], tests/executor/test_reply_round_rules.py::test_a_reply_round_does_not_collect_what_the_judge_declined_on_the_head[no-early], tests/executor/test_reply_round_rules.py::test_a_reply_round_does_not_collect_what_the_judge_declined_on_the_head[re-measure-after-the-repair]
origin: src/kraft/executor/dispatch.py §declined_fingerprints

## REQ a-reply-round-never-stops-the-item-on-pending-ci
WHILE a node is in a reply round, the `mr.ci` task SHALL be handed the round, and a
look that finds the pipeline still pending SHALL wait until the round's deadline and
then complete the node on the head's earlier green measurement, recording its wait as
`timed_out` and its session as `done`, and SHALL NOT stop the item. A pipeline that
goes red in the round SHALL still fail it, and a task with no round SHALL keep its own
timeout, including a later head's look that finds the wait a round started open.
enforced-by: tests/adapters/forge/test_run_ci_reply.py::test_the_ci_look_in_a_reply_round[pending-before-the-deadline-waits], tests/adapters/forge/test_run_ci_reply.py::test_the_ci_look_in_a_reply_round[pending-at-the-deadline-is-done], tests/adapters/forge/test_run_ci_reply.py::test_the_ci_look_in_a_reply_round[red-still-fails], tests/adapters/forge/test_run_ci_reply.py::test_the_ci_look_in_a_reply_round[green-is-done], tests/adapters/forge/test_run_ci_reply.py::test_the_ci_look_in_a_reply_round[no-reply-keeps-the-ci-waits-own-timeout], tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[ci-task-with-an-open-round], tests/executor/test_dispatch_reply.py::test_the_review_wait_is_handed_the_open_round[ci-task-with-another-heads-round], tests/executor/test_reply_round_walks.py::test_a_pending_pipeline_in_a_reply_round_completes_at_the_reply_deadline, tests/executor/test_reply_round_walks.py::test_a_pipeline_that_goes_red_in_a_reply_round_still_fails, tests/adapters/forge/test_run_ci_reply.py::test_a_later_head_s_ci_look_keeps_the_ci_waits_own_timeout
origin: src/kraft/adapters/forge/run.py §_run_one -- Omid, 2026-11-22: a reply's workflow may add a check, and posting never parks or stops an item.

## REQ the-reply-report-lists-what-a-round-left
WHEN a node with replies on completes, the reply report SHALL list each thread
still owed a look at the per-head limit as `owed_at_limit`, with the bot's unread word,
each thread the owed check could not read as `not_read`, a CI look still pending when a
round's time ran out as `ci_pending`, and every finding the last measurement raised or
did not re-read that never reached a fix, including after a round whose triage was
skipped.
enforced-by: tests/executor/test_reply_round_walks.py::test_an_owed_at_limit_entry_carries_the_unread_answer, tests/executor/test_reply_round_walks.py::test_a_reply_round_report_keeps_what_an_earlier_measurement_found, tests/executor/test_reply_round_walks.py::test_the_per_head_limit_ends_the_conversation, tests/executor/test_reply_round_walks.py::test_a_pending_pipeline_in_a_reply_round_completes_at_the_reply_deadline, tests/executor/test_reply_round_walks.py::test_an_unreadable_viewer_closed_request_or_failed_read_is_not_owed[viewer-unreadable], tests/executor/test_reply_round_walks.py::test_an_unreadable_viewer_closed_request_or_failed_read_is_not_owed[read-thread-raises]
origin: src/kraft/executor/replies.py §_Point.write_report

## REQ the-final-review-states-open-bot-threads-and-the-conversation-rule
WHEN the task that declares `triage_verdict` starts on an item with a configured
review bot, the system SHALL read the merge request's open bot threads and whether
the base branch requires resolved conversations, write both to a report the brief
is pointed at, and record `unknown` for what it cannot read, without stopping or
delaying the item. An item with no configured bot SHALL make no forge call and get
no note. WHEN a merge is refused, or held until its wait times out, on that rule, the
stop SHALL name the open bot threads, as the likely blocker unless the forge said
so, and SHALL NOT name them for any other refusal. A file name in that text SHALL
NOT change the cause the stop reports.
enforced-by: tests/executor/test_agent_inputs.py::test_the_brief_is_told_the_open_bot_threads_and_the_rule[required-some-open], tests/executor/test_agent_inputs.py::test_the_brief_is_told_the_open_bot_threads_and_the_rule[rule-read-failed], tests/executor/test_agent_inputs.py::test_the_brief_reads_through_the_items_real_forge, tests/executor/test_agent_inputs.py::test_a_repository_with_no_review_bot_gets_no_read_and_no_note, tests/adapters/forge/test_open_threads.py::test_a_refused_merge_names_the_open_bot_threads_only_when_they_are_why[blocked-rule-yes-is-likely], tests/adapters/forge/test_open_threads.py::test_a_refused_merge_names_the_open_bot_threads_only_when_they_are_why[another-refusal], tests/adapters/forge/test_open_threads.py::test_a_merge_the_forge_holds_names_the_threads_when_its_wait_times_out[held-on-its-threads], tests/executor/test_walk_forge_cause.py::test_a_file_name_in_a_merge_stops_threads_is_never_its_cause[auth-words], tests/executor/test_walk_forge_cause.py::test_a_file_name_in_a_merge_stops_threads_is_never_its_cause[network-words]
origin: src/kraft/adapters/forge/open_threads.py
