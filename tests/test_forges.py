"""`forges.yaml`: the account model, its address rules and its file."""

from __future__ import annotations

import pytest
import yaml

from kraft import forges
from kraft.config import ConfigError, RepoEntry


def _load(tmp_path, text):
    path = tmp_path / "forges.yaml"
    path.write_text(text)
    return forges.ForgesFile.load(path)


@pytest.mark.parametrize(
    ("forge", "web_url", "api", "extra"),
    [
        ("github", "https://github.com", "https://api.github.com", {}),
        ("github", "https://acme.ghe.com", "https://api.acme.ghe.com", {}),
        ("github", "https://git.corp.example", "https://git.corp.example/api/v3", {}),
        ("github", "https://ghe.corp:8443", "https://ghe.corp:8443/api/v3", {}),
        (
            "gitlab",
            "https://code.corp.example/gitlab",
            "https://code.corp.example/gitlab/api/v4",
            {},
        ),
        (
            "gitlab",
            "https://code.corp.example/gitlab/",
            "https://code.corp.example/gitlab/api/v4",
            {},
        ),
        (
            "gitlab",
            "https://code.corp.example",
            "https://code.corp.example/api/v4",
            {"client": "api", "token": {"from": "env", "env": "CORP_GL", "kind": "pat"}},
        ),
    ],
    ids=[
        "github-com",
        "ghe-data-residency",
        "ghes",
        "ghes-port",
        "gitlab-prefix",
        "trailing-slash",
        "api-with-env-source",
    ],
)
def test_an_account_loads_with_its_api_url_derived(tmp_path, forge, web_url, api, extra):
    acct = {"forge": forge, "web_url": web_url, **extra}
    file = _load(tmp_path, yaml.safe_dump({"accounts": {"a": acct}}))
    assert file.accounts["a"].api() == api
    if extra:
        assert file.accounts["a"].source().label() == "env CORP_GL"


@pytest.mark.parametrize(
    ("given", "host"),
    [
        ("https://Code.Corp.Example:8443", "code.corp.example"),
        ("https://code.corp.example./x", "code.corp.example"),
    ],
    ids=["case-and-port", "trailing-dot"],
)
def test_web_hosts_compare_without_case_port_or_dot(given, host):
    assert forges.web_host(given) == host


@pytest.mark.parametrize(
    ("remote", "host"),
    [
        ("ssh://git@code.corp.example:2222/team/app.git", "code.corp.example"),
        ("https://Code.Corp.Example:8443/team/app.git", "code.corp.example"),
        ("git@code.corp.example.:team/app.git", "code.corp.example"),
        ("git@github-work:acme/app.git", "github-work"),
        ("not a remote", None),
    ],
    ids=["ssh-port", "https-case-port", "scp-dot", "ssh-alias", "unparseable"],
)
def test_remote_hosts_compare_without_case_port_or_dot(remote, host):
    assert forges.remote_host(remote) == host


@pytest.mark.parametrize(
    ("forge", "web_url", "api_url"),
    [
        ("github", "https://github.com", "https://api.github.com"),
        ("github", "https://acme.ghe.com", "https://api.acme.ghe.com"),
        ("gitlab", "https://g.example:8443", "https://g.example:8443/api/v4"),
        ("github", "https://github.com:443", "https://api.github.com:443"),
    ],
    ids=[
        "github-com-api-host",
        "ghe-data-residency-api-host",
        "same-host-with-port",
        "explicit-443",
    ],
)
def test_an_explicit_api_url_on_an_allowed_host_loads(tmp_path, forge, web_url, api_url):
    acct = {"forge": forge, "web_url": web_url, "api_url": api_url}
    file = _load(tmp_path, yaml.safe_dump({"accounts": {"a": acct}}))
    assert file.accounts["a"].api() == api_url


_CLI = "accounts:\n  a: {forge: github, web_url: 'https://github.com', "
_API = "accounts:\n  a: {forge: gitlab, web_url: 'https://g.example', client: api, "


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("accounts:\n  a: {forge: github, web_url: 'http://github.com'}\n", "https://"),
        (
            "accounts:\n  a: {forge: gitlab, web_url: 'https://g.example', "
            "api_url: 'https://other.example/api/v4'}\n",
            "must be web_url's origin",
        ),
        (
            "accounts:\n  a: {forge: gitlab, web_url: 'https://code.corp.example', client: api, "
            "api_url: 'https://code.corp.example:8443/api/v4'}\n",
            "must be web_url's origin",
        ),
        ("accounts:\n  a: {forge: github, web_url: 'https://github.com', token: x}\n", "token"),
        (_CLI + "token: {from: env, env: CORP_GL}}\n", "carries no token"),
        (_CLI + "ca_file: /etc/ca.pem}\n", "carries no token"),
        (_API + "token: {from: env}}\n", "token from: env takes only `env`"),
        (_API + "token: {from: env, env: ANTHROPIC_API_KEY}}\n", "which Kraft passes to agents"),
        (_API + "token: {from: env, env: KRAFT_HOME}}\n", "token.env names a KRAFT_ variable"),
        (_API + "token: {from: env, env: GIT_CONFIG_COUNT}}\n", "token.env names a GIT_ variable"),
        (_API + "token: {from: env, env: glpat-SECRETSECRET}}\n", "must name an environment"),
        (_API + "token: {glpat-SECRETSECRET}}\n", "accounts.a.token has a key forges.yaml"),
        (_CLI + "glpat-SECRETSECRET: x}\n", "accounts.a has a key forges.yaml does not know"),
        (
            "accounts:\n  a: {forge: github, web_url: 'https://github.com:8443', "
            "api_url: 'https://api.github.com'}\n",
            "must be web_url's origin",
        ),
        (_API + "token: {from: file, file: secrets/gl}}\n", "must be an absolute path"),
        (_API + "token: {from: env, env: A_B, file: /x}}\n", "token from: env takes only"),
        (_API + "token: glpat-SECRETSECRET}\n", "'accounts'.a.token"),
        ("accounts:\n  a: {forge: gitea, web_url: 'https://g.example'}\n", "'github' or 'gitlab'"),
        (
            "accounts:\n"
            "  a: {forge: gitlab, web_url: 'https://g.example', default_for_host: true}\n"
            "  b: {forge: gitlab, web_url: 'https://G.example/', default_for_host: true}\n",
            "both default_for_host",
        ),
        ("accounts:\n  a: {forge: github, web_url: 'https://[::1'}\n", "plain https://"),
        ("accounts:\n  a: {forge: gitlab, web_url: 'https://u:tok@g.example'}\n", "credentials"),
        ("accounts:\n  a: {forge: gitlab, web_url: 'https://g.example?x'}\n", "query"),
        ("accounts:\n  a: {forge: gitlab, web_url: 'https://g.example:99999'}\n", "plain https://"),
        ("accounts:\n  a:\n    token: ghp_SECRET: x\n", "at line 3, column"),
        ("accounts: *ghp_SECRET\n", "not valid YAML at line 1"),
    ],
    ids=[
        "http-web-url",
        "api-url-other-host",
        "api-url-other-port",
        "token-key",
        "cli-with-token",
        "cli-with-ca-file",
        "env-source-without-env",
        "env-names-anthropic-key",
        "env-names-kraft-var",
        "env-names-git-var",
        "env-holds-a-token",
        "token-flow-key",
        "account-unknown-key-holds-a-token",
        "github-api-host-other-port",
        "file-relative",
        "two-source-keys",
        "token-as-a-string",
        "unknown-forge",
        "two-defaults-one-host",
        "unclosed-bracket",
        "userinfo",
        "query",
        "bad-port",
        "unparseable-names-the-line-not-its-text",
        "an-alias-is-not-quoted",
    ],
)
def test_a_bad_account_is_refused_naming_the_file_and_key(tmp_path, text, says):
    with pytest.raises(ConfigError) as err:
        _load(tmp_path, text)
    assert "forges.yaml" in str(err.value)
    assert says in str(err.value)
    assert "ghp_SECRET" not in str(err.value)  # shown on the open /health
    assert "glpat-SECRETSECRET" not in str(err.value)
    # No chained pydantic error: its `input_value` would put the token in a traceback.
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_a_missing_file_is_no_accounts(tmp_path):
    assert forges.ForgesFile.load(tmp_path / "forges.yaml").accounts == {}


@pytest.mark.parametrize(
    ("account", "present", "absent"),
    [
        (
            forges.ForgeAccount(forge="github", web_url="https://github.com"),
            [],
            ["client", "api_url"],
        ),
        (
            forges.ForgeAccount(
                forge="gitlab",
                web_url="https://g.example",
                client="api",
                token={"from": "env", "env": "CORP_GL"},
            ),
            ["from: env", "env: CORP_GL"],
            ["from_", "kind: unknown"],
        ),
        (
            forges.ForgeAccount(
                forge="gitlab", web_url="https://g.example", client="api", token={}
            ),
            ["from: git-credential"],
            ["token: {}", "kind: unknown"],
        ),
    ],
    ids=["cli", "api-env-source", "api-default-source-keeps-from"],
)
def test_a_save_round_trips_at_0600_without_defaults(tmp_path, account, present, absent):
    path = tmp_path / "forges.yaml"
    path.write_text("# mine\naccounts: {}\n")
    file = forges.ForgesFile(accounts={"a": account})
    file.save(path)
    text = path.read_text()
    assert "# mine" in text
    assert all(t in text for t in present) and not any(t in text for t in absent)
    assert forges.ForgesFile.load(path) == file
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("name", "refused"),
    [
        ("PATH", True),
        ("HOME", True),
        ("GIT_CONFIG_COUNT", True),
        ("ANTHROPIC_API_KEY", True),
        ("CLAUDE_CODE_OAUTH_TOKEN", True),
        ("KRAFT_PORT", True),
        ("1BAD", True),
        ("CORP_GL", False),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_an_env_name_kraft_needs_is_never_a_source(name, refused):
    assert (forges.env_name_problem(name) is not None) is refused


@pytest.mark.parametrize(
    ("remote", "web_url", "forge", "path"),
    [
        (
            "https://code.corp.example/gitlab/team/app.git",
            "https://code.corp.example/gitlab",
            "gitlab",
            "team/app",
        ),
        (
            "git@code.corp.example:gitlab/team/app.git",
            "https://code.corp.example/gitlab",
            "gitlab",
            "team/app",
        ),
        ("git@github-work:acme/app.git", "https://github.com", "github", "acme/app"),
        ("https://github.com/acme/app", "https://github.com", "github", "acme/app"),
        ("", "https://github.com", "github", None),
    ],
    ids=["https-with-prefix", "scp-with-prefix", "ssh-alias-host", "github", "empty"],
)
def test_the_project_is_the_remotes_path_less_the_web_prefix(remote, web_url, forge, path):
    account = forges.ForgeAccount(forge=forge, web_url=web_url)
    assert forges.project_path(remote, account) == path


_GH = forges.ForgeAccount(forge="github", web_url="https://git.corp.example", default_for_host=True)
_GL = forges.ForgeAccount(forge="gitlab", web_url="https://git.corp.example", default_for_host=True)
_OTHER = forges.ForgeAccount(forge="github", web_url="https://github.com")


@pytest.mark.parametrize(
    ("accounts", "entry", "host", "expected"),
    [
        ({"gh": _GH, "o": _OTHER}, {"forge_account": "o"}, "git.corp.example", "o"),
        ({"gh": _GH}, {}, "git.corp.example", "gh"),
        ({"gl": _GL}, {}, "git.corp.example", None),
        ({"gh": _GH}, {}, "github-work", None),
        ({}, {}, "git.corp.example", None),
        ({"gh": _GH}, {"forge_account": "ghost"}, "git.corp.example", None),
    ],
    ids=[
        "named",
        "host-default",
        "default-other-forge-skipped",
        "ssh-other-host-none",
        "no-file-none",
        "unknown-name-none",
    ],
)
def test_account_for_picks_named_then_host_default_then_none(accounts, entry, host, expected):
    repo = RepoEntry(path="/r", forge="github", **entry)
    found = forges.account_for(repo, forges.ForgesFile(accounts=accounts), host)
    assert (found[0] if found else None) == expected


@pytest.mark.parametrize(
    ("forge_account", "says", "passthrough"),
    [
        ("ghost", "names no account", []),
        ("gl", "says forge: gitlab", []),
        ("", "env_passthrough names CORP_GL", ["CORP_GL"]),
    ],
    ids=["unknown-account", "forge-mismatch", "env-passthrough-names-a-token-source"],
)
def test_account_problems_names_each(forge_account, says, passthrough):
    repo = RepoEntry(
        path="/r", forge="github", forge_account=forge_account, env_passthrough=passthrough
    )
    # Another account's token source counts too: the variable is gone for every repo.
    api = forges.ForgeAccount(
        forge="gitlab",
        client="api",
        web_url="https://gl.example",
        token=forges.TokenSource(**{"from": "env", "env": "CORP_GL"}),
    )
    problems = forges.account_problems(forges.ForgesFile(accounts={"gl": _GL, "api": api}), [repo])
    assert len(problems) == 1 and forge_account in problems[0] and says in problems[0]
