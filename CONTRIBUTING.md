# Contributing

## Releasing

Kraft's version is the git tag. `setuptools-scm` derives it at build time, so
there is no `version =` line to bump and no bump commit to forget.

That removes the loud failure and leaves a quiet one: if nobody ever makes a
tag, nothing fails — `main` just accumulates untagged commits while
`install.sh` keeps serving a release from months ago. So every merge request
declares what it ships, and merging is what tags.

### The label

Put exactly one of these on your merge request:

| Label | Means |
|---|---|
| `release::major` | a breaking change to the CLI, the API, or on-disk state |
| `release::minor` | a new capability that does not break an existing one |
| `release::patch` | a fix to something that already shipped |
| `release::none` | nothing a user of Kraft receives |

They are scoped labels — the `::` is what makes GitLab enforce "at most one".

The `release-impact` job fails a merge request that carries none of them. That
is a bookkeeping failure and it will annoy you. It is also the only moment when
the person who wrote the change knows what the change is worth, which is why the
question is asked here and not on `main`.

`release::none` is a first-class answer, and the expected one for
documentation, comments, CI configuration and test-only changes. It is not an
escape hatch — it is the declaration that this change ships nothing.

Nothing checks that the declared impact matches the diff. The label is a claim
by its author; review is what tests it.

### What happens on merge

The `auto-tag` job asks the API which merge request produced the merge commit,
reads its `release::` label, and computes the next tag from the newest existing
one. `release::none` tags nothing. Anything else writes that version into both
plugin manifests, commits it, and pushes `vX.Y.Z`, which starts a tag pipeline: the `release` job builds the wheel, smoke-installs it to check the
version and the bundled SPA, uploads it, and creates the GitLab Release that
`install.sh` and `kraft admin update` read.

A commit that reaches `main` without a merge request — a direct push, the revert
button, a cherry-pick — has no label to read. It is treated as `none` and says
so in the job log. Direct pushes to `main` do not release.

## Release setup (once, by a maintainer)

Neither of the first two can be done by code in this repo. Until both exist,
`release-impact` fails every merge request and `auto-tag` cannot push.

1. **Create four scoped labels** in the GitLab project: `release::major`,
   `release::minor`, `release::patch`, `release::none`. The `::` matters —
   scoped labels are what guarantee a merge request carries at most one.
2. **Create a project access token** with the `api` scope and the Maintainer
   role, and add it as a masked, protected CI/CD variable named
   `RELEASE_TOKEN`. `CI_JOB_TOKEN` cannot push tags, which is why this is the
   one new secret the release mechanism needs.
3. **Protect the `v*` tag pattern** with "Maintainers" allowed to create, so
   nothing but that token and a human maintainer can publish a release.
4. **Create `itsOmidKarami/kraft`** on GitHub, public and empty, and add it as a
   remote: `git remote add plugins git@github.com:itsOmidKarami/kraft.git`.
   The name is deliberately the one the monorepo will take when it migrates off
   GitLab, so `/plugin marketplace add itsOmidKarami/kraft` is correct now and
   stays correct afterwards. Until that migration, GitHub `kraft` holds only the
   split plugins and GitLab `kraft` holds the source.
5. **Stamp the manifests before the first tag.** `auto-tag` runs
   `dev/stamp_plugin_versions.py` and commits the result *before* it tags, so
   every automatic release publishes a truthful version. A tag created by hand
   skips that, and `just plugins-publish` would then publish whatever number is
   committed - `0.0.0` for the kraft plugin. For the first release only, run
   `python3 dev/stamp_plugin_versions.py <version>`, merge that, and tag the
   merge commit.
6. **Make the first tag `v0.6.0` or higher.** `plugins/kraft-lite` has already
   been published at `0.5.2` from its old repo. Both plugins now take their
   version from this repo's tag, so a first release below that number would ship
   kraft-lite *backwards* to anyone who has it installed. There are no tags yet,
   so the first one is a free choice - use it.
7. **Leave a redirect on `itsOmidKarami/kraft-lite`.** It stops receiving
   releases; its README should point at the combined marketplace. Its last
   release stays installable for anyone who already added it.

The merge request that introduces this mechanism needs a `release::` label
itself, so create the labels before opening it.

## The marketplace plugins

Both plugins live under `plugins/` and publish together, as one marketplace:

```
/plugin marketplace add itsOmidKarami/kraft
```

`just plugins-publish` does it, with a real `git subtree split --prefix=plugins`,
pushing to `github.com/itsOmidKarami/kraft`.
The published history is this repo's own commits — the ones that touched
`plugins/` — not a regenerated commit per release. That is the whole reason
`plugins/kraft/` sits there rather than inside the Python package: split can only
publish a directory that already has the layout you want published.

An installed Kraft has no `plugins/` beside it, so `just bundle` copies
`plugins/kraft/skills` into `src/kraft/_bundled/plugin-skills` — the same way it
copies the built SPA — and `init.skills_dir()` prefers that copy, falling back to
the source tree for a checkout that never ran `just bundle`.

Both share one version, and it is this repo's release tag. `auto-tag` writes it
into both manifests and commits before tagging, because a split publishes what is
committed. The cost is honest: a release of the CLI republishes both plugins at
the new number even when neither changed.

`lite-publish`, `lite-check` and the `lite-version` CI job are gone. They existed
to guard a version committed by hand in `plugins/kraft-lite/.claude-plugin/plugin.json`;
there is no hand-written number left to forget.

### When the monorepo moves to GitHub

The split exists only because the source repo is private on GitLab. A public
monorepo needs none of it: `.claude-plugin/marketplace.json` moves to the repo
root with `"source": "./plugins/kraft"` and `"./plugins/kraft-lite"`, and
`/plugin marketplace add itsOmidKarami/kraft` resolves against the real
repository. `plugins-publish`, `dev/stamp_plugin_versions.py` and the version
commit in `auto-tag` all delete themselves at that point - the versions can go
back to being read from the tag at build time, because nothing has to publish a
committed copy any more.

The directory layout does not change, which is why it is this one. Tracked as
Kraft-t3qn.
