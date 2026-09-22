"""Template Schema V1: the typed chain schema (`models`), the library loader
and resolver (`library`), the repository/workspace/harness environment
(`environment`), run forks (`forks`) and retry overrides (`retry`).

The legacy hook registry (`registry.yaml`) and `gate_after` chain loader that
lived here are gone: V1 is incompatible with them by design
(`template-v1-is-not-backward-compatible`), and a home still holding them is
recognised by `library.is_pre_v1` and refused until `kraft admin update`
replaces it."""
