---
name: mr-metadata
description: "Use when Kraft is about to open a merge request from a finished branch."
---

# Authoring the merge request's metadata

Kraft is about to open a merge request from your branch. Everything you write
here becomes the MR itself: its title, its labels, who it asks to review, and
the description a reviewer reads before they read anything else. Nobody
rewrites the title or description after you -- write it for the reviewer, not for Kraft.

## Read the repo's conventions first, and obey them over anything here

- `.gitlab/merge_request_templates/*.md`, `.github/PULL_REQUEST_TEMPLATE.md`
  (and `.github/PULL_REQUEST_TEMPLATE/*.md`): if the project ships a template,
  fill *that*, section for section, rather than this skill's own headings
  below.
- `CONTRIBUTING.md` for stated title and description rules, **and for any
  label CI requires**. Some projects fail a merge request that carries no
  label of a given kind (a release or change-type label, say). If this one
  does, you must pick one: its CONTRIBUTING, or the CI job that checks, is
  where the choices are written down. If it states no label rule, there is
  none to satisfy.
- `CODEOWNERS` for who reviews the paths this diff touches.
- What the project already does: `git log --oneline -30` for the title
  pattern (Conventional Commits or not), `glab mr list --merged` / `gh
  pr list --state merged` for how merged MRs are titled and labelled, and the
  project's existing label set (`glab label list`) -- a label that does not
  exist is a failed API call, not a new label.

## Then read the change, not the plan

The diff against the base branch (`git diff <base>...HEAD`), the spec, the
plan, and the local review findings. A description written from the spec
describes the change that was planned, not the one that was made.

## What the description answers, in this order

- **What** this introduces, in the repo's own terms -- files, functions,
  behaviour.
- **Why** -- the problem being solved, inlined. This work item's spec and plan
  are not committed: a reviewer cannot open them, so a link or a spec id is
  not an answer.
- **How** -- the approach, and the alternatives rejected, if a reviewer would
  otherwise ask "why not X".
- **Evidence** -- the commands run and what they printed. Test counts, lint,
  typecheck. Not "tests pass".

## Rules

- Do not advocate for approval. You are not selling the change.
- Do not restate the work item title as the description.
- Do not invent a reviewer or a label. Empty is correct unless the repo names
  one, and a label that does not already exist in this project is a failed
  API call, not a new label.
- A label the project's rules require is not optional: every one of them goes
  in `labels`. A merge request opened without it fails CI before anyone reads
  it.
- Three lines is a fine description for a three-line diff.
- Scope deliberately left out belongs in the description -- it is most of
  what gets rejected.
- Do not hard-wrap a paragraph at some fixed column. GitHub and GitLab render
  a single `\n` inside a paragraph as a line break, not a space -- unlike a
  terminal or an editor, they don't reflow it back into prose. Write each
  paragraph as one unwrapped line (or let it wrap in your editor without
  inserting real newlines); use a blank line only where you mean an actual
  paragraph break.
