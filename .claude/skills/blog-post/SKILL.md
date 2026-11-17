---
name: blog-post
description: Use when writing, reviewing or publishing a post for the kraft-sdlc.dev blog (docsite/blog/), a release story such as "What's new in X.Y", or a copy for dev.to, Medium or another site. Covers what a post may claim, the draft-to-live process and canonical links; not prose style.
---

# A post on the Kraft blog

The blog is Kraft's own home for its writing: `docsite/blog/<slug>.md`, live at
`https://kraft-sdlc.dev/blog/<slug>` on the docs deploy after it merges to main.
`docsite/blog/README.md` has the mechanics (front matter, images, video, drafts,
previewing). This skill is the approach every post follows. Read both before
you start; where the README and this skill disagree, the README describes the
code and wins on mechanics.

Style is out of scope here: the checks below are about content and process.

## 1. The original lives here

- **Publish here first.** A post is written, reviewed and merged in this repo
  before it appears anywhere else. Never publish a post on another platform
  first and copy it back, except to rescue one that already exists only
  elsewhere (as the launch article was, from dev.to).
- **Everything ships with it; the address is permanent.** Images and video
  live in this repo, never on another site's CDN or embed, and a live post is
  never renamed or deleted: correct it in place. The README's "The file" and
  "Images and video" say how.

## 2. Every claim comes from the repo

Before any sentence goes in, find its source and keep the path in your notes
for the review:

| Claim | Source |
|---|---|
| What Kraft is and does | `README.md`, the `/why` page (`docsite/app/components/why/WhyPage.vue`) |
| Caps and defaults | `config/policy.yaml` ($10 a work item, $50 a day) |
| A release's features | its GitHub release notes and `docsite/content/6.project/*whats-new*` |
| Security and gates | `docsite/content/6.project/1.security-in-depth.md` |
| A run's numbers (time, cost, files) | the work item on a real instance (`kraft view show ID`), never `just dev` (its agents are fake), or the maintainer |

Then hold these lines, which posts have got wrong before:

- **Gates.** Never write that an agent can't skip or approve a gate. Write that
  the steps run in order from the chain file, and that a sandbox with a
  `network:` policy makes a gate a wall; an unsandboxed worker runs as the user
  (security-in-depth, "Agents and gates").
- **Local work.** Closing the laptop pauses work that runs on it. A step on
  another machine keeps going and hands in its result when the laptop wakes
  (`docsite/content/3.guides/4.run/13.run-a-step-on-another-machine.md`, "When
  your laptop sleeps").
- **No bead ids** (`Kraft-xxxxx`) in text. Check screenshots too, and ask the
  maintainer before publishing one that shows them.
- **Nothing invented.** No customers, testimonials, benchmarks, quotes or
  numbers that the repo or the maintainer didn't give you.
- **Docs links** go to pages and headings in the latest release, not only on
  main: the post shows beside the released docs.

When a claim has no source, cut it or ask the maintainer. Don't soften it into
something vaguer that still implies it.

## 3. What a post is

- **A story with one point.** A post follows one run, one decision or one
  feature, with the person's view of why it matters. It is not docs: link to
  the page that explains how, rather than repeating it.
- **A release post is a story, not the notes.** "What's new in X.Y" picks one
  or two headline features, shows each in use and why it matters, and links the
  release notes and What's new page for the rest. Write it only once `vX.Y.0`
  is tagged and released.
- **Honest about limits.** Say when not to use the thing, and what it costs.
  A post that only sells reads as an ad.
- **First person is the author's.** A post signed by the maintainer speaks as
  them; don't put opinions or anecdotes in their mouth. Ask for them.

## 4. From draft to live

1. **Branch and file.** A post gets its own branch and pull request, separate
   from any code change. Start the front matter with `draft: true` and keep it
   until the maintainer says, in the conversation or on the pull request, to
   publish. A deadline or a request to skip the draft is not that yes.
2. **Ask for what you can't source.** Screenshots of the maintainer's own
   board, run numbers, opinions. List them in the pull request description as
   open items rather than inventing placeholders that might ship.
3. **Check it rendered.** `KRAFT_BLOG_DRAFTS=1 just docs`, then open the post
   at a phone width and a desktop width, in light and dark. Look at every image
   and the video.
4. **Run the checks** that cover a post: `just ci-checks` (it includes Vale:
   posts follow the docs' terms and spelling, "merge request" and British
   spelling) and `python3 dev/check_site_docs_links.py` (it needs the release
   tags). For new site code, also `dev/build_docs_site.sh docsite/dist-site`. The docs workflow
   fails a draft that leaks into the build, and a published post missing from
   the feed.
5. **Open the pull request** following `CONTRIBUTING.md`: label it
   `release::none` (a post is no product change, so it adds no line to the
   release notes) and leave `## Changelog` empty. Put the post's address in
   the description.
6. **Publish** by deleting `draft: true`, in the same pull request or a
   follow-up one, only with the maintainer's yes. It goes live on the docs
   deploy after the merge.
7. **Link a release post from its release.** Once a "What's new in X.Y" post
   is live, give the maintainer a line for the top of that release's notes on
   GitHub, linking the post; they make the edit. Other posts need no link.

## 5. Copies elsewhere point back

Only after the post is live here, and only when the maintainer asks: this
skill prepares copies, it doesn't post them.

- **Canonical link.** Set it as the README's "Sharing a copy elsewhere" says,
  and check that the copy's page source shows it before you call it done.
- **The copy is the same post.** Change only what the platform forces (video
  embed, tags, absolute links). A wording change goes into the post here
  first, through a pull request; until it merges, the copy keeps the old
  wording.
