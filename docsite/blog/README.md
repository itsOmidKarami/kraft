# Writing a blog post

Posts on [kraft-sdlc.dev/blog](https://kraft-sdlc.dev/blog) are Markdown files
in this folder, written and reviewed in a merge request like any other change.
A post goes live on the docs deploy that follows its merge to main, with no
release: the build at the root of the site takes `docsite/blog/` from main,
not from the latest release tag.

## The file

The file's name is the post's address: `my-post.md` is `/blog/my-post`. Keep
it short, lower case and hyphenated, and don't rename it once it's live, or
every link to it breaks.

```markdown
---
title: "What's new in 2.6: the headline in a few words"
description: One or two sentences for the index, the feed and share cards.
date: 2026-11-20
author: Your Name
image: /blog/my-post/cover.png
tags: [release]
draft: true
---

The first paragraph, straight into the story.
```

| Field | Required | What it does |
|---|---|---|
| `title` | yes | The page's heading and the title in feeds and share cards. Quote it when it has a colon. |
| `description` | yes | Under the title on the index and the post, in the feed, and in share cards. About 150 characters at most. |
| `date` | yes | `YYYY-MM-DD`. The index and the feed sort by it, newest first. |
| `author` | yes | The name shown under the title. |
| `image` | no | The share card: 1000×420 or wider at that ratio, under `docsite/public/blog/<post>/`. Without one, the site draws a card from the title. The page itself doesn't show it. |
| `tags` | no | A few lower-case words, shown under the title and sent as feed categories. |
| `draft` | no | `true` keeps the post out of the site, the sitemap and the feed, so a draft can merge before it is ready. Delete the line to publish. |

To see a draft while you write it, run `KRAFT_BLOG_DRAFTS=1 just docs` and open
`http://localhost:3000/blog`.

## Images and video

Put a post's images in `docsite/public/blog/<post>/` and link them from the
root: `![What it shows](/blog/my-post/board.png)`. Everything in
`docsite/public/` is published as it is, a draft's images included: don't
put an image there that can't be public before its post is. The alt text says what the
image shows, not "a screenshot". A docs screenshot already in `.github/assets/`
can be linked as `/assets/<name>.png`.

A YouTube video uses the site's player, which loads nothing from YouTube until
the reader presses play:

```markdown
::site-video
---
id: PPr7e5-1yws
title: "Kraft: stop babysitting your coding agents!"
length: "1:53"
label: "Play the video: Kraft, stop babysitting your coding agents, 1 minute 53 seconds"
---
::
```

An animation of the item page (`slices`, `gate` or `subagents`) plays only
while it is on screen and holds a still frame under reduced motion. `still` is
that frame as an image, which the feed shows in its place. Each one is a
timeline in `app/utils/blogAnimation/<name>.ts` and a view in
`app/components/blog/animation/<Name>.vue`; a new one is those two files and
its line in `BlogAnimation.vue`'s list:

```markdown
::blog-animation
---
name: slices
still: /blog/my-post/slices.png
alt: "An illustration of what it shows, step by step"
---
::
```

## Links

Link a docs page by its path, `[the security page](/project/security-in-depth#agents-and-gates)`.
A post shows at the root, next to the latest release's docs, so a page or
heading that is only on main links nowhere until the next release.
`dev/check_site_docs_links.py` fails the docs build when that happens.

## What a post may claim

The rules for what a post may claim, and the steps from draft to live, are in
[the blog-post skill](../../.claude/skills/blog-post/SKILL.md).

## Sharing a copy elsewhere

The post here is the original. A copy on another site names it, so readers and
search engines credit this one:

- **dev.to:** set `canonical_url:` in the dev.to editor's front matter to the
  post's address. dev.to can also import posts from
  `https://kraft-sdlc.dev/blog/feed.xml` (its Settings, Extensions); if you
  use that, check that each imported draft names the post here as its
  canonical URL before you publish it.
- **Medium:** import the story from the post's address, then check that its
  canonical link names the post here.
- **Anywhere else:** link the original at the top or bottom, for example
  "First published on kraft-sdlc.dev" linking the post's address,
  and use its canonical-URL setting when it has one.
