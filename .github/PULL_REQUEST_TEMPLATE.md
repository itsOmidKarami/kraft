<!-- What this changes, and why -- the diff already shows what moved. -->



## Changelog

<!-- One user-facing line, e.g. "Fix: ...". A release::none pull request is left out of the release notes, so leave this empty. With any other label, an empty section lists the pull request's title instead. -->

## Release impact

Apply exactly one label: `release::major`, `release::minor`, `release::patch`, or `release::none`.
Maintainers may also add `notes::highlight` to the release's headline change.
`release::none` is a first-class answer for docs, comments, CI config, and test-only
changes. See [CONTRIBUTING.md](https://github.com/itsOmidKarami/kraft/blob/main/CONTRIBUTING.md#pull-requests-and-release-labels).

<!--
Only if this pull request deletes a test function, a frontend test file, or an
intent `## REQ` heading: uncomment the sections it needs, one id per line with
the reason after it. CI's `removals declared` job fails without them (see
"Removing a test" in docs/testing.md).

## Removed tests
- tests/test_old.py::test_gone -- replaced by tests/test_new.py::test_here
- tests/test_old.py::test_cluster_* -- folded into tests/test_old.py::test_folded[...]

## Removed requirements
- some-req-name -- superseded by other-req-name
-->
