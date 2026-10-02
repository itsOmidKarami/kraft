"""Make the release's artifacts say which release they are.

Two things a release writes into what it ships, which a tag alone does not:

  python3 dev/release_artifacts.py pin-wheel <wheel> <tag>
      Points the wheel's description (README.md, as PyPI renders it) at <tag>
      instead of `main`, so a version's PyPI page keeps the screenshots that
      version shipped with.
  python3 dev/release_artifacts.py vsix-version <tag>
      Prints <tag> as the version `vsce package` stamps on the .vsix, in
      semver: v1.5.0 -> 1.5.0, v1.5.0rc2 -> 1.5.0-rc.2.

release.yml runs this from main's copy of `dev/`, like the other release tools.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path

from next_tag import PRE_MARKS

#: A GitHub URL that serves a file at a branch, which moves. `raw.githubusercontent`
#: and `github.com/o/r/raw` are the two forms an image can be hot-linked by.
#: A ref that is already a tag or a commit is left alone.
_MOVING = re.compile(
    r"(https://(?:raw\.githubusercontent\.com/[^/\s)\"']+/[^/\s)\"']+"
    r"|github\.com/[^/\s)\"']+/[^/\s)\"']+/raw))/(?:main|master|HEAD)/"
)

_TAG = re.compile(rf"v?(\d+\.\d+\.\d+)(?:({'|'.join(PRE_MARKS.values())})(\d+))?")
_SEMVER_PRE = {mark: kind for kind, mark in PRE_MARKS.items()}


def pin_refs(text: str, tag: str) -> str:
    """`text` with every hot-linked `main` (or `HEAD`) asset URL pointing at `tag`."""
    return _MOVING.sub(lambda m: f"{m[1]}/{tag}/", text)


def _record_row(path: str, data: bytes) -> list[str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return [path, f"sha256={digest}", str(len(data))]


def pin_wheel(wheel: Path, tag: str) -> None:
    """Rewrite the wheel's METADATA in place, keeping RECORD's hash of it true.

    Done on the built wheel because every other place to do it is worse: the
    tag's README is the one on `main`, and editing README.md before the build
    dirties the tree, which setuptools-scm answers with a `+d20261002` local
    version that PyPI refuses.
    """
    with zipfile.ZipFile(wheel) as src:
        infos = src.infolist()
        contents = {info.filename: src.read(info) for info in infos}
    (meta,) = (n for n in contents if re.fullmatch(r"[^/]+\.dist-info/METADATA", n))
    pinned = pin_refs(contents[meta].decode(), tag).encode()
    if pinned == contents[meta]:
        return
    contents[meta] = pinned
    record = meta.removesuffix("METADATA") + "RECORD"
    rows = []
    for row in csv.reader(io.StringIO(contents[record].decode())):
        rows.append(_record_row(meta, pinned) if row[0] == meta else row)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    contents[record] = out.getvalue().encode()
    tmp = wheel.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as dst:
        for info in infos:
            dst.writestr(info, contents[info.filename], compress_type=info.compress_type)
    tmp.replace(wheel)


def vsix_version(tag: str) -> str:
    """The semver the extension is packed at.

    A pre-release keeps its own place below the release: `1.5.0-rc.2` sorts
    after `1.5.0-rc.1` and before `1.5.0`, which is how VS Code compares
    extension versions. The count is its own dot-separated number on purpose:
    `1.5.0-rc10` would sort below `1.5.0-rc9`.
    """
    match = _TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"cannot read a version out of {tag!r}")
    base, mark, number = match.groups()
    return base if mark is None else f"{base}-{_SEMVER_PRE[mark]}.{number}"


def main(argv: list[str]) -> None:
    if len(argv) == 3 and argv[0] == "pin-wheel":
        pin_wheel(Path(argv[1]), argv[2])
    elif len(argv) == 2 and argv[0] == "vsix-version":
        print(vsix_version(argv[1]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
