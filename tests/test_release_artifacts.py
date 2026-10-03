"""What a release writes into its artifacts: the wheel's README and the .vsix's version."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

# `dev/` is not a package; release_artifacts imports next_tag as a sibling script.
_ROOT = Path(__file__).resolve().parents[1]
_DEV = _ROOT / "dev"
sys.path.insert(0, str(_DEV))
_SPEC = importlib.util.spec_from_file_location("release_artifacts", _DEV / "release_artifacts.py")
release_artifacts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_artifacts)

RELEASE_YML = _ROOT / ".github" / "workflows" / "release.yml"
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "![a](https://raw.githubusercontent.com/o/r/main/.github/assets/a.png)",
            "![a](https://raw.githubusercontent.com/o/r/v1.5.0/.github/assets/a.png)",
        ),
        (
            "![a](https://raw.githubusercontent.com/o/r/HEAD/a.png)",
            "![a](https://raw.githubusercontent.com/o/r/v1.5.0/a.png)",
        ),
        (
            "![a](https://github.com/o/r/raw/HEAD/vscode/media/a.png)",
            "![a](https://github.com/o/r/raw/v1.5.0/vscode/media/a.png)",
        ),
        # Already pinned, a link and not an image, or a branch of a longer name:
        # nothing to move.
        ("https://raw.githubusercontent.com/o/r/v1.4.0/a.png", None),
        ("https://raw.githubusercontent.com/o/r/0123abc/a.png", None),
        ("https://example.com/o/r/main/a.png", None),
        # Someone else's repository: its tag is not ours, so its link would break.
        ("https://raw.githubusercontent.com/other/repo/main/a.png", None),
        ("https://github.com/other/repo/raw/main/a.png", None),
        ("https://raw.githubusercontent.com/o/r2/main/a.png", None),
        ("https://raw.githubusercontent.com/o2/r/main/a.png", None),
        ("https://github.com/o/r/blob/main/LICENSE", None),
        ("https://raw.githubusercontent.com/o/r/maintenance/a.png", None),
    ],
)
def test_a_moving_asset_url_is_pinned_to_the_tag(before, after):
    assert release_artifacts.pin_refs(before, "v1.5.0", "o/r") == (after or before)


_NEXT = "[What's new](https://o.github.io/r/next/get-started/whats-new)"


@pytest.mark.parametrize(
    ("tag", "before", "after"),
    [
        ("v2.0.0", _NEXT, "[What's new](https://o.github.io/r/get-started/whats-new)"),
        ("v2.0.0", "https://O.GitHub.io/r/next/", "https://O.GitHub.io/r/"),
        # A pre-release's stable docs are still the last release's.
        ("v2.0.0rc2", _NEXT, None),
        ("v2.0.0b1", _NEXT, None),
        # Already the release's, someone else's site, or a page named next.
        ("v2.0.0", "https://o.github.io/r/get-started/whats-new", None),
        ("v2.0.0", "https://other.github.io/r/next/x", None),
        ("v2.0.0", "https://o.github.io/r2/next/x", None),
        ("v2.0.0", "https://o.github.io/r/guides/next/x", None),
    ],
    ids=["stable", "case", "rc", "beta", "pinned", "other-owner", "other-repo", "deeper"],
)
def test_a_stable_tag_points_main_s_docs_at_the_release_s(tag, before, after):
    """The README links What's new on `/next/` until the release that adds it
    is out; frozen into that release's PyPI page, the link would drift to
    whatever `main` says later (R11D-02)."""
    assert release_artifacts.pin_docs(before, tag, "o/r") == (after or before)


def test_the_repository_matches_however_github_spells_it():
    url = "https://raw.githubusercontent.com/ITSomidkarami/Kraft/main/a.png"
    assert release_artifacts.pin_refs(url, "v1.5.0") == url.replace("/main/", "/v1.5.0/")


def test_every_image_in_the_pypi_readme_is_one_the_pin_knows():
    readme = (_ROOT / "README.md").read_text()
    pinned = release_artifacts.pin_refs(readme, "v9.9.9")
    urls = IMAGE.findall(pinned)
    assert urls, "README.md has no images; the pin has nothing to do and this test nothing to check"
    # A README that hot-links some other form of branch URL (`?raw=true`, a
    # `blob/main` path) would slip past the pin and put `main` back on PyPI.
    for url in urls:
        assert not re.search(r"/(main|master|HEAD)/", url), url
        if "githubusercontent" in url:
            assert "/v9.9.9/" in url, url


def test_the_extension_readme_links_its_images_relative_to_vscode():
    # `vsce package --baseImagesUrl` makes these absolute at the release's tag.
    urls = IMAGE.findall((_ROOT / "vscode" / "README.md").read_text())
    assert urls
    for url in urls:
        assert not url.startswith(("http:", "https:", "/")), url
        assert (_ROOT / "vscode" / url).is_file(), url


def _record(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def _digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _wheel(path: Path, description: str) -> dict[str, bytes]:
    metadata = (
        "Metadata-Version: 2.4\nName: kraft-sdlc\nVersion: 1.5.0\n"
        "Project-URL: Source, https://github.com/o/r\n"
        "Description-Content-Type: text/markdown\n\n" + description
    ).encode()
    files = {
        "kraft/__init__.py": b"x = 1\n",
        "kraft/bin/kraft": b"#!/bin/sh\n",
        "kraft_sdlc-1.5.0.dist-info/METADATA": metadata,
        "kraft_sdlc-1.5.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
    }
    rows = [[n, _digest(d), str(len(d))] for n, d in files.items()]
    rows.append(["kraft_sdlc-1.5.0.dist-info/RECORD", "", ""])
    files["kraft_sdlc-1.5.0.dist-info/RECORD"] = _record(rows).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 12, 0, 0))
            info.external_attr = (0o755 if name.endswith("/kraft") else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return files


def test_the_wheels_description_is_pinned_and_nothing_else_moves(tmp_path):
    wheel = tmp_path / "kraft_sdlc-1.5.0-py3-none-any.whl"
    image = "![board](https://raw.githubusercontent.com/o/r/main/.github/assets/board.png)\n"
    image += "![theirs](https://raw.githubusercontent.com/other/repo/main/a.png)\n"
    image += "[new](https://o.github.io/r/next/get-started/whats-new)\n"
    before = _wheel(wheel, image)
    release_artifacts.pin_wheel(wheel, "v1.5.0", "o/r")
    with zipfile.ZipFile(wheel) as z:
        assert z.testzip() is None
        assert [i.filename for i in z.infolist()] == list(before)
        after = {i.filename: z.read(i) for i in z.infolist()}
        modes = {i.filename: i.external_attr >> 16 for i in z.infolist()}
        stamps = {i.date_time for i in z.infolist()}
        compression = {i.compress_type for i in z.infolist()}
    meta = "kraft_sdlc-1.5.0.dist-info/METADATA"
    assert b"/o/r/v1.5.0/.github/assets/board.png" in after[meta]
    assert b"/o/r/main/" not in after[meta]
    assert b"/other/repo/main/a.png" in after[meta]
    assert b"(https://o.github.io/r/get-started/whats-new)" in after[meta]
    # The header's own github.com link is not an asset URL.
    assert b"Project-URL: Source, https://github.com/o/r\n" in after[meta]
    for name in before:
        if name not in (meta, "kraft_sdlc-1.5.0.dist-info/RECORD"):
            assert after[name] == before[name]
    assert modes["kraft/bin/kraft"] == 0o755
    assert stamps == {(2026, 10, 1, 12, 0, 0)}
    assert compression == {zipfile.ZIP_DEFLATED}
    # RECORD tells the truth about the file that changed, and only that one.
    rows = {
        r[0]: r
        for r in csv.reader(io.StringIO(after["kraft_sdlc-1.5.0.dist-info/RECORD"].decode()))
    }
    assert rows[meta] == [meta, _digest(after[meta]), str(len(after[meta]))]
    assert rows["kraft/__init__.py"] == ["kraft/__init__.py", _digest(b"x = 1\n"), "6"]
    assert rows["kraft_sdlc-1.5.0.dist-info/RECORD"] == [
        "kraft_sdlc-1.5.0.dist-info/RECORD",
        "",
        "",
    ]


def test_pinning_a_wheel_with_nothing_to_pin_leaves_it_byte_for_byte(tmp_path):
    wheel = tmp_path / "kraft_sdlc-1.5.0-py3-none-any.whl"
    _wheel(wheel, "No images here.\n")
    before = wheel.read_bytes()
    release_artifacts.pin_wheel(wheel, "v1.5.0", "o/r")
    assert wheel.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v1.5.0", "1.5.0"),
        ("v1.5.0rc1", "1.5.0-rc.1"),
        ("v1.5.0rc10", "1.5.0-rc.10"),
        ("v1.5.0b2", "1.5.0-beta.2"),
        ("v1.5.0a3", "1.5.0-alpha.3"),
        ("1.5.0rc1", "1.5.0-rc.1"),
    ],
)
def test_the_vsix_is_packed_at_a_semver_that_sorts_below_its_release(tag, version):
    assert release_artifacts.vsix_version(tag) == version


def _precedence(version: str):
    """semver 2.0 precedence, which is how VS Code orders extension versions."""
    core, _, pre = version.partition("-")
    ids = [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")] if pre else None
    return tuple(int(n) for n in core.split(".")), ids is None, ids or []


def test_a_pre_release_is_older_than_its_release_and_newer_than_the_last_one():
    tags = ["v1.5.0a1", "v1.5.0b1", "v1.5.0rc1", "v1.5.0rc2", "v1.5.0rc9", "v1.5.0rc10", "v1.5.0"]
    versions = [release_artifacts.vsix_version(t) for t in tags]
    assert sorted(versions, key=_precedence) == versions
    assert _precedence("1.4.0") < _precedence(versions[0])


@pytest.mark.parametrize("tag", ["", "main", "v1.5", "v1.5.0-rc.1", "v1.5.0.rc1", "v1.5.0rc"])
def test_a_tag_that_is_not_one_of_ours_is_refused(tag):
    with pytest.raises(ValueError, match="cannot read a version"):
        release_artifacts.vsix_version(tag)


def test_the_command_line(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    release_artifacts.main(["vsix-version", "v1.5.0rc2"])
    assert capsys.readouterr().out == "1.5.0-rc.2\n"
    wheel = tmp_path / "w.whl"
    _wheel(wheel, "![a](https://raw.githubusercontent.com/o/r/main/a.png)\n")
    release_artifacts.main(["pin-wheel", str(wheel), "v1.5.0"])
    with zipfile.ZipFile(wheel) as z:
        assert b"/o/r/v1.5.0/a.png" in z.read("kraft_sdlc-1.5.0.dist-info/METADATA")
    with pytest.raises(SystemExit):
        release_artifacts.main(["pin-wheel", str(wheel)])


def _steps() -> list[str]:
    return re.findall(r"- (?:name: (.+)|uses: .+)", RELEASE_YML.read_text())


def test_the_wheel_is_built_with_the_tagged_commits_time():
    """Without `SOURCE_DATE_EPOCH`, two builds of one tag differed in the zip
    dates of every bundled file, so nobody could re-derive the published
    sha256 from the tag."""
    text = RELEASE_YML.read_text()
    start = text.index("- name: tag locally, then build")
    step = text[start : text.index("\n      - ", start + 1)]
    epoch = step.find('export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"')
    assert -1 < epoch < step.index("just bundle") < step.index("uv build --wheel")


def test_the_wheel_is_pinned_after_it_is_built_and_before_it_is_smoke_tested():
    names = [n for n in _steps() if n]
    pin, build, smoke = (
        names.index("point the wheel's README at this tag"),
        names.index("tag locally, then build"),
        names.index("smoke test the wheel"),
    )
    assert build < pin < smoke


def test_kraft_ships_everywhere_before_the_extension_is_published():
    """A failed step stops every step after it, and the Marketplace token
    expires. With the extension's steps last, an expired token fails the run
    without leaving Homebrew on the old version or main unstamped."""
    names = [n for n in _steps() if n]
    assert names[-3:] == [
        "publish the VS Code extension",
        "publish the VS Code extension to Open VSX",
        "check the Marketplace lists this release",
    ]
    extension = len(names) - 3
    for kraft in [
        "push the tag and create the release",
        "publish to PyPI",
        "stamp plugin manifests and CHANGELOG.md for this release",
        "bump the homebrew tap",
        "check PyPI serves this release",
    ]:
        assert names.index(kraft) < extension, kraft


def _job() -> dict:
    return yaml.safe_load(RELEASE_YML.read_text())["jobs"]["release"]


def test_only_the_marketplace_steps_hold_the_marketplace_token():
    """VSCE_PAT is a year-long token. As a job-level variable it sat in the
    environment of every step, `npm ci`'s install scripts among them."""
    job = _job()
    assert "VSCE_PAT" not in (job.get("env") or {})
    holders = [s.get("name") for s in job["steps"] if "secrets.VSCE_PAT" in str(s.get("env"))]
    assert holders == ["is there a Marketplace token", "publish the VS Code extension"]
    assert RELEASE_YML.read_text().count("secrets.VSCE_PAT") == 2
    steps = job["steps"]
    gated = {s["name"]: s["if"] for s in steps if re.search("VSCE_PAT|has_pat", s.get("if", ""))}
    assert list(gated) == [
        "publish the VS Code extension",
        "check the Marketplace lists this release",
    ]
    for condition in gated.values():
        assert condition.endswith("&& steps.marketplace.outputs.has_pat == 'true'"), condition


@pytest.mark.parametrize(("pat", "has_pat"), [("a-token", "true"), ("", "")], ids=["set", "unset"])
def test_the_marketplace_steps_run_only_when_the_token_is_set(tmp_path, pat, has_pat):
    (probe,) = (s for s in _job()["steps"] if s.get("id") == "marketplace")
    output = tmp_path / "output"
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", probe["run"]],
        env={"PATH": os.environ["PATH"], "VSCE_PAT": pat, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    # The token itself never reaches the output, which later steps can read.
    assert output.read_text() == f"has_pat={has_pat}\n"


def _smoke_version_check() -> str:
    text = RELEASE_YML.read_text()
    step = text[text.index("- name: smoke test the wheel") : text.index("- name: build the VS")]
    check = r"^( *)(VERSION=\$\(/tmp/smoke/bin/kraft --version\)\n.*?^\1fi\n)"
    match = re.search(check, step, re.M | re.S)
    assert match, "the smoke test no longer reads `kraft --version` into VERSION; update this test"
    return match.group(2).replace("/tmp/smoke/bin/kraft --version", 'printf "%s\\n" "$SAYS"')


@pytest.mark.parametrize(
    ("tag", "says", "ok"),
    [
        ("v1.5.0", "kraft 1.5.0", True),
        ("v1.5.0rc11", "kraft 1.5.0rc11", True),
        ("v1.5.0", "kraft 1.5.0rc11", False),
        ("v1.5.0", "kraft 1.5.0.post1", False),
        ("v1.5.0", "kraft 11.5.0", False),
    ],
    ids=["stable", "rc", "rc-for-stable", "post-for-stable", "longer-major"],
)
def test_the_smoke_test_wants_the_wheel_to_report_exactly_the_tag(tag, says, ok):
    """GitHub runs a step under `bash -eo pipefail`; so does this."""
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _smoke_version_check()],
        env={"PATH": os.environ["PATH"], "TAG": tag, "SAYS": says},
        capture_output=True,
        text=True,
    )
    assert (run.returncode == 0) is ok, run.stdout + run.stderr


def _notice_check(step: str, artifact: str) -> str:
    """The `if ! unzip -l ... fi` in `step` that checks `artifact` for its notices."""
    text = RELEASE_YML.read_text()
    start = text.index(f"- name: {step}")
    body = text[start : text.index("\n      - ", start + 1)]
    check = rf"^ *(if ! unzip -l \"{re.escape(artifact)}\" .*?^ *fi\n)"
    match = re.search(check, body, re.M | re.S)
    assert match, f"{step!r} no longer checks {artifact} for THIRD_PARTY_LICENSES.txt"
    return match.group(1)


def _zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name in names:
            z.writestr(name, "x")


@pytest.mark.parametrize(
    ("step", "artifact", "notice", "other"),
    [
        (
            "smoke test the wheel",
            "$WHEEL",
            "kraft/_bundled/web/THIRD_PARTY_LICENSES.txt",
            "kraft/_bundled/web/index.html",
        ),
        (
            "build the VS Code extension",
            "$RUNNER_TEMP/vsix/kraft-${TAG#v}.vsix",
            "extension/dist/THIRD_PARTY_LICENSES.txt",
            "extension/dist/extension.js",
        ),
    ],
    ids=["wheel", "vsix"],
)
@pytest.mark.parametrize("shipped", [True, False], ids=["with-notices", "without"])
def test_a_release_artifact_without_its_third_party_licenses_fails_the_run(
    tmp_path, step, artifact, notice, other, shipped
):
    """The web UI and the extension bundle MIT, ISC and OFL code whose licenses
    ask for their notices to go with every copy. The build writes them; this is
    the check that a wheel or a .vsix that lost them is never published."""
    (tmp_path / "vsix").mkdir()
    env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path), "TAG": "v1.5.0"}
    env["WHEEL"] = str(tmp_path / "kraft_sdlc-1.5.0-py3-none-any.whl")
    path = env["WHEEL"] if artifact == "$WHEEL" else str(tmp_path / "vsix" / "kraft-1.5.0.vsix")
    _zip(Path(path), [other, notice] if shipped else [other])
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _notice_check(step, artifact)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert (run.returncode == 0) is shipped, run.stdout + run.stderr
    if not shipped:
        kind = "wheel" if artifact == "$WHEEL" else ".vsix"
        assert f"::error::the {kind} has no {notice}" in run.stdout


def test_the_extension_is_packed_with_the_release_tags_version_images_and_changelog():
    text = RELEASE_YML.read_text()
    step = text[
        text.index("- name: build the VS Code extension") : text.index("- name: push the tag")
    ]
    assert 'release_artifacts.py" vsix-version "$TAG"' in step
    assert 'npx vsce package "$VSIX_VERSION"' in step
    assert '--baseImagesUrl "https://github.com/$GITHUB_REPOSITORY/raw/$TAG/vscode/"' in step
    # The extension's changelog is written before packing and put back after, so
    # the stamp step branches off a clean tree. The section is headed with the
    # version the package carries, `1.5.0-rc.2`, not the tag's spelling.
    assert 'changelog "$VSIX_VERSION"' in step
    assert (
        step.index("changelog")
        < step.index("vsce package")
        < step.index("git checkout -- CHANGELOG.md")
    )
    # The Get Started link is fixed on the packed .vsix, the one released.
    assert 'release_artifacts.py" vsix-links "$RUNNER_TEMP/vsix/kraft-${TAG#v}.vsix"' in step
    assert step.index("--out ") < step.index("vsix-links")


def test_the_command_line_defaults_to_this_repository(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    wheel = tmp_path / "w.whl"
    _wheel(
        wheel,
        f"![a](https://raw.githubusercontent.com/{release_artifacts.REPOSITORY}/main/a.png)\n",
    )
    release_artifacts.main(["pin-wheel", str(wheel), "v1.5.0"])
    with zipfile.ZipFile(wheel) as z:
        assert f"/{release_artifacts.REPOSITORY}/v1.5.0/a.png".encode() in z.read(
            "kraft_sdlc-1.5.0.dist-info/METADATA"
        )


def _vsix(path: Path, homepage: str = "https://example.com/guide?a=1&b=2") -> dict[str, bytes]:
    repo = "https://github.com/o/r.git"
    links = "".join(
        f'<Property Id="Microsoft.VisualStudio.Services.Links.{kind}" Value="{repo}" />\n'
        for kind in ("Source", "Getstarted", "GitHub")
    )
    files = {
        "extension.vsixmanifest": f"<Properties>\n{links}</Properties>\n".encode(),
        "[Content_Types].xml": b"<Types />\n",
        "extension/package.json": json.dumps({"name": "kraft", "homepage": homepage}).encode(),
        "extension/dist/extension.js": b"module.exports = 1;\n",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 1, 12, 0, 0)), data)
    return files


def test_the_vsix_get_started_link_goes_to_the_extensions_guide(tmp_path):
    """vsce sets Get Started to the clone URL, `.git` and all, and has no
    setting for it. The other repository links are right as they are."""
    vsix = tmp_path / "kraft-1.5.0.vsix"
    before = _vsix(vsix)
    release_artifacts.vsix_links(vsix)
    with zipfile.ZipFile(vsix) as z:
        assert z.testzip() is None
        assert [i.filename for i in z.infolist()] == list(before)
        after = {i.filename: z.read(i) for i in z.infolist()}
    manifest = after["extension.vsixmanifest"].decode()
    assert (
        '<Property Id="Microsoft.VisualStudio.Services.Links.Getstarted" '
        'Value="https://example.com/guide?a=1&amp;b=2" />'
    ) in manifest
    assert manifest.count('Value="https://github.com/o/r.git"') == 2
    for name in before:
        if name != "extension.vsixmanifest":
            assert after[name] == before[name]


def test_a_vsix_whose_get_started_link_moved_stops_the_release(tmp_path):
    vsix = tmp_path / "kraft-1.5.0.vsix"
    _vsix(vsix)
    with zipfile.ZipFile(vsix) as z:
        contents = {i.filename: z.read(i) for i in z.infolist()}
    contents["extension.vsixmanifest"] = b"<Properties />\n"
    with zipfile.ZipFile(vsix, "w") as z:
        for name, data in contents.items():
            z.writestr(name, data)
    with pytest.raises(ValueError, match="0 Get Started links"):
        release_artifacts.vsix_links(vsix)


def test_the_extension_declares_kraft_s_own_license():
    """Not `SEE LICENSE IN LICENSE`: the Marketplace and npm read an SPDX id."""
    package = json.loads((_ROOT / "vscode" / "package.json").read_text())
    pyproject = (_ROOT / "pyproject.toml").read_text()
    assert f'license = "{package["license"]}"' in pyproject


def test_the_packed_extension_is_built_without_a_source_map():
    """`.vscodeignore` keeps *.map out of the .vsix, so the build `vsce
    package` runs must not link one."""
    package = json.loads((_ROOT / "vscode" / "package.json").read_text())
    assert package["scripts"]["vscode:prepublish"] == "node esbuild.mjs --production"
    assert "**/*.map" in (_ROOT / "vscode" / ".vscodeignore").read_text().splitlines()


def _source_map_check() -> str:
    text = RELEASE_YML.read_text()
    start = text.index("- name: build the VS Code extension")
    step = text[start : text.index("- name: push the tag")]
    match = re.search(r"^ *(if unzip -p [^\n]*sourceMappingURL.*?^ *fi\n)", step, re.M | re.S)
    assert match, "the extension's build no longer checks extension.js for a source map link"
    return match.group(1)


@pytest.mark.parametrize(
    ("tail", "ok"),
    [("", True), ("//# sourceMappingURL=extension.js.map\n", False)],
    ids=["no-link", "dangling-link"],
)
def test_a_vsix_that_links_a_missing_source_map_fails_the_run(tmp_path, tail, ok):
    (tmp_path / "vsix").mkdir()
    with zipfile.ZipFile(tmp_path / "vsix" / "kraft-1.5.0.vsix", "w") as z:
        z.writestr("extension/dist/extension.js", "x".join(["module.exports = 1;\n"] * 9000) + tail)
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _source_map_check()],
        env={"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path), "TAG": "v1.5.0"},
        capture_output=True,
        text=True,
    )
    assert (run.returncode == 0) is ok, run.stdout + run.stderr
