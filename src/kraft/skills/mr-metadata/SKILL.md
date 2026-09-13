---
name: mr-metadata
description: Turn a finished branch into the merge request a reviewer can act on -- its title, labels, reviewers and the description.
---

# Authoring the merge request's metadata

Kraft is about to open a merge request from your branch. Everything you write
here becomes the MR itself: its title, its labels, who it asks to review, and
the description a reviewer reads before they read anything else. Nobody
rewrites this after you -- write it for the reviewer, not for Kraft.

## Read the repo's conventions first, and obey them over anything here

- `.gitlab/merge_request_templates/*.md`, `.github/PULL_REQUEST_TEMPLATE.md`
  (and `.github/PULL_REQUEST_TEMPLATE/*.md`): if the project ships a template,
  fill *that*, section for section, rather than this skill's own headings
  below.
- `CONTRIBUTING.md` for stated title and description rules.
- `CODEOWNERS` for who reviews the paths this diff touches.
- What the project already does: `git log --oneline -30` for the title
  pattern (Conventional Commits or not), `glab mr list --state merged` / `gh
  pr list --state merged` for how merged MRs are titled and labelled, and the
  project's existing label set (`glab label list`) -- a label that does not
  exist is a failed API call, not a new label.

## Then read the change, not the plan

`git diff main...HEAD`, the spec, the plan, and the local review findings. A
description written from the spec describes the change that was planned, not
the one that was made.

## What the description answers, in this order

- **What** this introduces, in the repo's own terms -- files, functions,
  behaviour.
- **Why** -- the problem being solved, inlined. Kraft's specs and plans live in
  gitignored `.engineering/` and `docs/superpowers/`: a reviewer cannot open
  them, so a link or a spec id is not an answer.
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
- Three lines is a fine description for a three-line diff.
- Scope deliberately left out belongs in the description -- it is most of
  what gets rejected.
