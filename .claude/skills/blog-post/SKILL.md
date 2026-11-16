---
name: blog-post
description: Kraft's own rules for a post on the kraft-sdlc.dev blog - what a post may claim, where its facts come from, how it goes from a draft to live, and how a copy shared on dev.to, Medium or elsewhere points back to the original. Use when writing, reviewing or publishing a blog post, a release story such as "What's new in X.Y", or a copy of one on another site; not for how to write prose.
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
- **Everything the post needs ships with it.** Images go in
  `docsite/public/blog/<slug>/`, not on another site's CDN. A docs screenshot
  already in `.github/assets/` is linked as `/assets/<name>.png`. A video uses
  `::site-video`, never a platform's embed syntax (`{% embed %}`).
- **The address is permanent.** Pick the slug once. A live post is never
  renamed or deleted; correct it in place.

## 2. Every claim comes from the repo

Before any sentence goes in, find its source and keep the path in your notes
for the review:

| Claim | Source |
|---|---|
| What Kraft is and does | `README.md`, `docsite/content/1.get-started/0.why-kraft.md` |
| Caps and defaults | `config/policy.yaml` ($10 a work item, $50 a day) |
| A release's features | its GitHub release notes and `docsite/content/6.project/*whats-new*` |
| Security and gates | `docsite/content/6.project/1.security-in-depth.md` |
| A run's numbers (time, cost, files) | the work item itself (`kraft view show`), or the maintainer |

Then hold these lines, which posts have got wrong before:

- **Gates.** Never write that an agent can't skip or approve a gate. Write that
  the steps run in order from the chain file, and that a sandbox with a
  `network:` policy makes a gate a wall; an unsandboxed worker runs as the user
  (security-in-depth, "Agents and gates").
- **Local work.** Closing the laptop pauses local work. It doesn't keep running.
- **No bead ids** (`Kraft-xxxxx`) in text. Check screenshots too, and ask the
  maintainer before publishing one that shows them.
- **Nothing invented.** No customers, testimonials, benchmarks, quotes or
  numbers that the repo or the maintainer didn't give you.
- **Docs links** go to pages and headings in the latest release, not only on
  main: the post shows beside the released docs. `just ci-checks` runs
  `dev/check_site_docs_links.py`, which fails otherwise.

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
  A post that only sells reads as an ad, and dev.to has flagged one before.
- **First person is the author's.** A post signed by the maintainer speaks as
  them; don't put opinions or anecdotes in their mouth. Ask for them.

## 4. From draft to live

1. **Branch and file.** A post gets its own branch and pull request, separate
   from any code change. Start the front matter with `draft: true` unless the
   maintainer wants it live on merge.
2. **Ask for what you can't source.** Screenshots of the maintainer's own
   board, run numbers, opinions. List them in the pull request description as
   open items rather than inventing placeholders that might ship.
3. **Check it rendered.** `KRAFT_BLOG_DRAFTS=1 just docs`, then open the post
   at a phone width and a desktop width, in light and dark. Look at every image
   and the video.
4. **Run the checks** that cover a post: `just ci-checks`, and for new site
   code `just docs-site`. `just vale` reads this folder's README but not the
   posts, which keep their author's voice (a post may say "pull request" to a
   GitHub reader). The docs workflow fails a draft that leaks
   into the build, and a published post missing from the feed.
5. **Open the pull request** following `CONTRIBUTING.md`: label it
   `release::none` (a post is no product change, so it adds no line to the
   release notes) and leave `## Changelog` empty. Put the post's address in
   the description.
6. **Publish** by deleting `draft: true`, in the same pull request or a
   follow-up one, only with the maintainer's yes. It goes live on the docs
   deploy after the merge.

## 5. Copies elsewhere point back

Only after the post is live here, and only when the maintainer asks: this
skill prepares copies, it doesn't post them.

- **dev.to:** set `canonical_url:` to the post's address. A copy imported
  from the feed (`https://kraft-sdlc.dev/blog/feed.xml`) needs the same check
  before it is published.
- **Medium:** import the story from the post's address, and check that its
  canonical link names the post here.
- **Check every copy** shows the canonical link in its page source before you
  call it done.
- **Anywhere without a canonical setting:** end the copy with "First published
  on kraft-sdlc.dev" linking the post.
- **The copy is the same post.** Change only what the platform forces (its
  video embed, its tags). Fix a mistake here first, then in the copies.
