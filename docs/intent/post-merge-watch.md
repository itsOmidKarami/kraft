# Post-merge watch

What `mr.post_merge_ci` does when the merged branch's own pipeline settles red.

## REQ post-merge-red-pauses-items-on-the-merged-commit

WHEN the post-merge pipeline settles red with at least one failed job that is not a publish job, the
system SHALL pause every running item whose base is the merged commit.
enforced-by: tests/adapters/forge/test_merge_watch.py::test_merge_watch_files_a_follow_up_bead_and_pauses_items_on_the_broken_sha, tests/adapters/forge/test_merge_watch.py::test_merge_watch_pauses_items_unless_only_publish_jobs_failed[default-pauses], tests/adapters/forge/test_merge_watch.py::test_merge_watch_pauses_items_unless_only_publish_jobs_failed[mixed-still-pauses]
origin: src/kraft/adapters/forge/run.py §merge_watch

## REQ post-merge-publish-only-red-pauses-nothing

WHEN every failed job of a red post-merge pipeline is a publish job, the system SHALL file the
follow-up bead and SHALL NOT pause any item.
enforced-by: tests/adapters/forge/test_merge_watch.py::test_merge_watch_pauses_items_unless_only_publish_jobs_failed[configured-glob-exempts]
origin: src/kraft/adapters/forge/run.py §merge_watch

## REQ post-merge-publish-jobs-match-the-configured-globs

WHERE a repository sets `post_merge_publish_jobs`, the system SHALL treat exactly the failed jobs whose
names match those globs as publish jobs.
enforced-by: tests/adapters/forge/test_ci.py::test_is_publish_job[exact], tests/adapters/forge/test_ci.py::test_is_publish_job[glob-suffix], tests/adapters/forge/test_ci.py::test_is_publish_job[workflow-slash-job], tests/adapters/forge/test_ci.py::test_is_publish_job[build-is-not-publish], tests/adapters/forge/test_ci.py::test_is_publish_job[glob-is-anchored], tests/test_config_repos.py::test_load_repos_reads_an_entry[keeps-post-merge-publish-jobs]
origin: src/kraft/adapters/forge/ci.py §is_publish_job

## REQ post-merge-publish-jobs-default-to-none

WHERE a repository sets no `post_merge_publish_jobs` or sets `[]`, the system SHALL treat no job as a
publish job.
enforced-by: tests/test_config_repos.py::test_load_repos_reads_an_entry[post-merge-publish-jobs-default-to-empty], tests/adapters/forge/test_ci.py::test_is_publish_job[no-patterns-no-publish-job]
origin: src/kraft/config.py §RepoEntry
