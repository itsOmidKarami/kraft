"""`forges.yaml`: the account model, its address rules and its file."""

from __future__ import annotations

import pytest
import yaml

from kraft import forges
from kraft.config import ConfigError


def _load(tmp_path, text):
    path = tmp_path / "forges.yaml"
    path.write_text(text)
    return forges.ForgesFile.load(path)


@pytest.mark.parametrize(
    ("forge", "web_url", "api"),
    [
        ("github", "https://github.com", "https://api.github.com"),
        ("github", "https://acme.ghe.com", "https://api.acme.ghe.com"),
        ("github", "https://git.corp.example", "https://git.corp.example/api/v3"),
        (
            "gitlab",
            "https://code.corp.example/gitlab",
            "https://code.corp.example/gitlab/api/v4",
        ),
        (
            "gitlab",
            "https://code.corp.example/gitlab/",
            "https://code.corp.example/gitlab/api/v4",
        ),
    ],
    ids=["github-com", "ghe-data-residency", "ghes", "gitlab-prefix", "trailing-slash"],
)
def test_an_account_loads_with_its_api_url_derived(tmp_path, forge, web_url, api):
    file = _load(
        tmp_path, yaml.safe_dump({"accounts": {"a": {"forge": forge, "web_url": web_url}}})
    )
    assert file.accounts["a"].api() == api


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
    ("text", "says"),
    [
        ("accounts:\n  a: {forge: github, web_url: 'http://github.com'}\n", "https://"),
        (
            "accounts:\n  a: {forge: gitlab, web_url: 'https://g.example', "
            "api_url: 'https://other.example/api/v4'}\n",
            "must be web_url's host",
        ),
        ("accounts:\n  a: {forge: github, web_url: 'https://github.com', token: x}\n", "token"),
        (
            "accounts:\n  a: {forge: github, web_url: 'https://github.com', client: api}\n",
            "`client: api` arrives with the API backends; use `client: cli`",
        ),
        ("accounts:\n  a: {forge: gitea, web_url: 'https://g.example'}\n", "forge"),
        (
            "accounts:\n"
            "  a: {forge: gitlab, web_url: 'https://g.example', default_for_host: true}\n"
            "  b: {forge: gitlab, web_url: 'https://G.example/', default_for_host: true}\n",
            "both default_for_host",
        ),
    ],
    ids=[
        "http-web-url",
        "api-url-other-host",
        "token-key",
        "client-api",
        "unknown-forge",
        "two-defaults-one-host",
    ],
)
def test_a_bad_account_is_refused_naming_the_file_and_key(tmp_path, text, says):
    with pytest.raises(ConfigError) as err:
        _load(tmp_path, text)
    assert "forges.yaml" in str(err.value)
    assert says in str(err.value)


def test_a_missing_file_is_no_accounts(tmp_path):
    assert forges.ForgesFile.load(tmp_path / "forges.yaml").accounts == {}


def test_a_save_round_trips_at_0600_without_defaults(tmp_path):
    path = tmp_path / "forges.yaml"
    path.write_text("# mine\naccounts: {}\n")
    file = forges.ForgesFile(
        accounts={"a": forges.ForgeAccount(forge="github", web_url="https://github.com")}
    )
    file.save(path)
    text = path.read_text()
    assert "# mine" in text
    assert "client" not in text and "api_url" not in text
    assert forges.ForgesFile.load(path) == file
    assert path.stat().st_mode & 0o777 == 0o600
