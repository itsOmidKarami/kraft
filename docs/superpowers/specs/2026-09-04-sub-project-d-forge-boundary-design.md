# Sub-project D: forge boundary

Date: 2026-09-04
Beads: Kraft-8mu.1 (parent Kraft-8mu)

## Problem

Kraft names GitLab in three places outside the adapter layer:

- `config.py:130` — `probe_repo` string-matches `"gitlab"` in the origin URL and
  returns a `gitlab_project` key.
- `api.py:951`, `api.py:1005` — `RepoBody` and `RepoPatch` carry
  `gitlab_project`; `add_repo` persists it into `repos.yaml`.
- `Settings.tsx:214` renders a field labelled "GitLab project", falling back to
  the string `"no GitLab remote"`; `types.ts:209,223` type it.

`03_plugin_adapters.md:14` already decided the adapter itself is generalized —
"CI/MR gets a **generalized** `HttpClientAdapter`, not a one-off GitLab client".
So the layer that will eventually make HTTP calls is fine. What is hardwired is
the layer above it: the repo's identity on its forge.

Every hook that would consume that identity — `on.mr.open`, `on.ci.poll`,
`on.review.mr.run`, `on.merge` — is bound to `builtins.noop` in
`templates/registry.yaml`. Nothing reads `gitlab_project` today.

That is the whole reason to do this now. The change is a field rename and a
tolerant reader while nothing consumes the field. After the first forge plugin
ships it is a data migration of every user's `repos.yaml` plus a rewrite of that
plugin.

## Non-goals

- **Implementing a GitHub client.** There is no GitLab client either. This
  sub-project makes GitHub *possible*; it does not make it *work*. The forge
  plugins are separate efforts against the `noop` hooks.
- Bitbucket, Gitea, self-hosted forge detection beyond the host name.
- Changing `HttpClientAdapter`, which does not exist yet and is already specced
  correctly.
- Auth or token storage. `03_plugin_adapters.md:334` assumes a keyring-sourced
  token; that stays a plugin concern, and a per-forge one.

## 1. Repo identity

`gitlab_project: str | None` becomes two fields:

```yaml
forge: gitlab | github | null     # which forge, or none detected
project: "group/repo" | null      # the path-with-namespace on that forge
```

`forge` is a plain string, not an enum type, and unknown values load without
error. A user on a self-hosted forge Kraft has never heard of can set
`forge: gitea` by hand; no hook is bound to consume it, so nothing breaks, and
the day a Gitea plugin exists the data is already right. Validating the value
here would buy nothing and cost the hand-edit path that `repos.yaml` exists to
support.

Splitting into two fields rather than keeping one opaque URL is what lets a
plugin bind by forge in `registry.yaml` later without parsing strings at
dispatch time.

## 2. Probe

`probe_repo` replaces the `"gitlab" in remote` branch with a host-table lookup
over the origin URL:

```python
_FORGES = {"gitlab.com": "gitlab", "github.com": "github"}
```

Both SSH (`git@host:group/repo.git`) and HTTPS (`https://host/group/repo.git`)
shapes are already handled by the existing split-on-host-then-strip logic; it
generalizes to any host in the table without change beyond the table lookup.

Detection stays best-effort and read-only, matching the existing contract at
`config.py:100` — "a field it cannot determine comes back `None` rather than
failing the probe". A repo whose origin is an unrecognized host probes as
`forge: null, project: null` and is still connectable. Kraft never edits repo
files to find this out.

Self-hosted GitLab and GitHub Enterprise are out of scope for detection: their
hostnames are arbitrary and there is no reliable read-only signal. They are
reachable by hand-editing `repos.yaml`, which is the documented escape hatch for
exactly this class of thing.

## 3. Reading old files

`repos.yaml` lives in `$KRAFT_HOME/templates/`, which
`2026-09-04-packaging-and-dev-execution-design.md` established is seeded once and
**never overwritten** — the guarantee that an upgrade cannot clobber an edited
config. A migration that rewrites the user's file would break that guarantee for
a rename.

So the compatibility lives in the reader. `config.load_repos` is the single
choke point (`api.py` reaches `repos.yaml` only through it) and gains one
normalization pass:

| On disk | Loaded as |
|---|---|
| `gitlab_project: g/r` | `forge: gitlab`, `project: g/r` |
| `forge` + `project` | unchanged |
| both present | `forge`/`project` win; the legacy key is dropped |
| neither | `forge: null`, `project: null` |

`save_repos` writes only the new shape. The legacy key disappears from a user's
file the first time they edit any repo in Settings, and never disappears on its
own. No migration step, no version stamp, no upgrade note.

The existing validation in `load_repos` — every repo needs a string `path` — is
untouched.

## 4. API and UI

`RepoBody` and `RepoPatch` swap `gitlab_project` for `forge` and `project`, both
still `str | None`. `add_repo`'s entry construction takes them from the body or
falls back to the probe, exactly as it does today for `test_command`.

`Settings.tsx:214` becomes forge-neutral:

- label: `Forge project`, hint unchanged (`· on.mr.open / on.ci.poll / on.merge`)
- readout: `gitlab · group/repo`, or `no forge remote detected` when `forge` is
  null

The readout stays a readout. It is populated from the probe and the field is not
made editable in this sub-project — making it editable is only useful once a
plugin consumes it, and an editable field for a value nothing reads is a support
question waiting to happen.

`types.ts:209,223` follow.

## 5. Testing

Existing probe and repos tests carry the change. New cases, all cheap:

- `probe_repo` against fixture remotes: GitLab SSH, GitLab HTTPS, GitHub SSH,
  GitHub HTTPS, an unknown host, and no origin at all.
- `load_repos` normalization: legacy-only, new-only, both, neither.
- `save_repos` round-trip drops the legacy key.
- One `POST /repos` case asserting a GitHub remote lands `forge: github`.

The frontend `Settings.test.tsx` case asserting the GitLab readout updates to the
neutral one.

## 6. Open question deferred to the forge plugins

How a plugin is selected per forge — a `forge:` discriminator on the
`registry.yaml` hook entry, versus one plugin that switches internally — is left
open. It is decidable only against a real client, and this sub-project's output
(a `forge` string on the repo) is the input either answer needs.

## Acceptance

- The only occurrences of `gitlab` left in `src/kraft/` are `_FORGES` and
  `_normalize_forge`'s legacy handling — §3 requires the reader to name the old
  key, so a sweep that demands zero hits is asking for §3 to be deleted.
  `frontend/src/` has none at all.
- A `repos.yaml` written before this change loads, and its repo reports
  `forge: gitlab`.
- A GitHub repo connects through Settings and stores `forge: github`.
- `just test`, `just test-ui`, `just lint` pass.
