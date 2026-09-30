# Upstream Kit descriptors (conformance set)

Copied unmodified from [docker/sandbox-kit-spec](https://github.com/docker/sandbox-kit-spec)
at tag `v3.0.0-m.7` (commit `e502d26`), under the Apache License 2.0
(`LICENSE`, `NOTICE` beside this file, copied from the same commit).

Each `<name>.yaml` here is upstream's
`tck/sandbox/testdata/fixtures/<name>/<name>.yaml`:

- `agent-sessions.yaml`: `tck/sandbox/testdata/fixtures/agent-sessions/agent-sessions.yaml`
- `credential-inject-only.yaml`: `tck/sandbox/testdata/fixtures/credential-inject-only/credential-inject-only.yaml`
- `credential-phases-both.yaml`: `tck/sandbox/testdata/fixtures/credential-phases-both/credential-phases-both.yaml`
- `credential-phases-install.yaml`: `tck/sandbox/testdata/fixtures/credential-phases-install/credential-phases-install.yaml`
- `credential-phases-runtime.yaml`: `tck/sandbox/testdata/fixtures/credential-phases-runtime/credential-phases-runtime.yaml`
- `credential.yaml`: `tck/sandbox/testdata/fixtures/credential/credential.yaml`
- `egress.yaml`: `tck/sandbox/testdata/fixtures/egress/egress.yaml`
- `groups.yaml`: `tck/sandbox/testdata/fixtures/groups/groups.yaml`
- `hooks.yaml`: `tck/sandbox/testdata/fixtures/hooks/hooks.yaml`
- `ordinary-optional.yaml`: `tck/sandbox/testdata/fixtures/ordinary-optional/ordinary-optional.yaml`
- `privileged.yaml`: `tck/sandbox/testdata/fixtures/privileged/privileged.yaml`
- `resources.yaml`: `tck/sandbox/testdata/fixtures/resources/resources.yaml`
- `sbx-workload.yaml`: `tck/sandbox/testdata/fixtures/sbx-workload/sbx-workload.yaml`
- `scoped-egress-v2.yaml`: `tck/sandbox/testdata/fixtures/scoped-egress-v2/scoped-egress-v2.yaml`
- `unknown-required-capability.yaml`: `tck/sandbox/testdata/fixtures/unknown-required-capability/unknown-required-capability.yaml`
- `workload.yaml`: `tck/sandbox/testdata/fixtures/workload/workload.yaml`

Bumping `kraft.worker.kit.SPEC_TAG` re-copies these from the new tag and
renames the parent directory.
