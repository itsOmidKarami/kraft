# Embedder health

Semantic search's embedder is an opt-in extra: "not installed" and
"installed but broken" are different states, and both have to reach an
operator through `/health` and `kraft admin doctor`.

## REQ embedder-encode-failure-is-recorded
WHEN an available embedder's encode call fails, the system SHALL record the failure reason and keep it until a later encode call succeeds.
enforced-by: tests/index/test_embed.py::test_the_last_encode_failure_is_kept_until_a_later_success
origin: src/kraft/index/embed.py §Embedder.try_encode (Kraft-pm2rj)

## REQ health-reports-a-broken-installed-embedder
WHEN the embedder is installed but its last encode call failed, the system SHALL report it in `/health` as available with the failure reason.
enforced-by: tests/index/test_service.py::test_health_reports_a_broken_embedder_that_is_installed
origin: src/kraft/index/service.py §health (Kraft-pm2rj)

## REQ doctor-fails-an-installed-but-broken-embedder
IF the embedder extra is installed but the model will not load or encode, THEN `kraft admin doctor` SHALL FAIL the embeddings check, distinct from a missing extra.
enforced-by: tests/cli/test_doctor.py::test_an_installed_but_broken_embedder_fails
origin: src/kraft/doctor.py §_health_checks (Kraft-pm2rj)
