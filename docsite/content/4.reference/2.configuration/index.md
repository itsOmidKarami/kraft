---
title: Configuration
description: Where Kraft's configuration files live and which file controls what.
---

Kraft's configuration is a set of YAML files under `$KRAFT_HOME/templates/`; this page maps each file to its reference page.

Everything here lives under `$KRAFT_HOME/templates/` (default
`~/.kraft/templates/`). Kraft seeds it from the packaged defaults on first run
and never overwrites it afterwards, so an upgrade cannot clobber an edited
policy. The Settings screens in the UI edit these same files, and editing them
by hand is equally supported.

- `kraft admin doctor` reports anything that does not parse.
- `kraft admin reload` picks up an on-disk edit without a restart.
- `kraft admin templates lint` checks the whole library and every chain at
  once, and writes nothing.

Kraft validates each file when it loads it, and reports a file that fails with
the offending key named. A chain component may only `extends` a library
component of its own kind, and a `reject_to` may only name a node before the
gate that declares it.

`registry.yaml`, hook names and `gate_after` do not exist in Template Schema
V1. Kraft refuses a home that still holds them until you run
`kraft admin update`. See
[CLI: migrating a pre-V1 template configuration](/reference/cli/admin#migrating-a-pre-v1-template-configuration).

## Files

| File | What it configures | Page |
|---|---|---|
| `repos.yaml` | Connected repos: setup command, env, steering, workspaces. | [Repos](/reference/configuration/repos) |
| `policy.yaml` | Caps, budget, archiving, defaults and maxima, triggers. | [Policy](/reference/configuration/policy) |
| `library.yaml` and `chains/*.yaml` | Reusable components and the chain templates built from them. | [Library and chains](/reference/configuration/library-and-chains) |
| `harnesses.yaml` | Harness profiles and agent profiles. | [Harnesses file](/reference/configuration/harnesses-file) |
| `access.yaml` | Bind address, password, remote access. | [Access](/reference/configuration/access) |
| `intake.yaml` | Autonomous pickup of issues. | [Intake](/reference/configuration/intake) |
